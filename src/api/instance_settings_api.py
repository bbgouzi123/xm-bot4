"""
实例独立配置 API（instance_settings_api.py）

每个微信实例（以 wxid 为主键）可独立配置：
  - 自动回复开关（该实例是否参与 AI 自动回复）
  - 联系人过滤规则（黑/白名单模式：仅回复白名单 or 屏蔽黑名单）
  - 独立系统提示词（覆盖全局 AI 配置中的 system_prompt）
  - 关联行业 profile_id（优先级高于全局选中行业）

存储路径：<account_data_dir>/<wxid>/instance_settings.json
"""

import json
import os
import logging
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, List
from src.utils.response import ok, err, ok_msg

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/instances", tags=["instance-settings"])

_DEFAULT_SETTINGS = {
    "auto_reply_enabled": True,          # 该实例是否参与自动回复
    "contact_filter_mode": "none",       # none | whitelist | blacklist
    "contact_whitelist": [],             # 白名单（wxid 列表，仅回复这些人）
    "contact_blacklist": [],             # 黑名单（wxid 列表，不回复这些人）
    "system_prompt_override": "",        # 独立提示词（空=使用全局配置）
    "industry_profile_id": "",           # 关联行业 ID（空=使用全局选中行业）
    "sales_package_id": "",              # 关联销冠包 ID（空=不绑定）
    "note": "",                          # 备注（给管理者看的标签）
    "wechat_hex_key": "",                # 手动指定的 64 位微信数据库解密密钥（故障兜底）
    "wechat_data_dir": "",               # 手动指定的微信数据文件夹目录（故障兜底）
}


class InstanceSettings(BaseModel):
    auto_reply_enabled: Optional[bool] = None
    contact_filter_mode: Optional[str] = None        # none | whitelist | blacklist
    contact_whitelist: Optional[List[str]] = None
    contact_blacklist: Optional[List[str]] = None
    system_prompt_override: Optional[str] = None
    industry_profile_id: Optional[str] = None
    sales_package_id: Optional[str] = None
    note: Optional[str] = None
    wechat_hex_key: Optional[str] = None
    wechat_data_dir: Optional[str] = None


def _settings_path(wxid: str) -> str:
    """返回该实例配置文件路径。"""
    from src.crm.account_data import get_account_data_dir
    d = get_account_data_dir(wxid)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "instance_settings.json")


def load_instance_settings(wxid: str) -> dict:
    """读取指定微信实例的独立配置，缺失字段用默认值补齐。"""
    path = _settings_path(wxid)
    data = {}
    if os.path.exists(path):
        try:
            data = json.loads(open(path, encoding="utf-8").read())
        except Exception as e:
            logger.warning(f"[实例配置] 读取 {wxid} 配置失败: {e}")
    merged = {**_DEFAULT_SETTINGS, **data}
    return merged


def save_instance_settings(wxid: str, settings: dict) -> bool:
    """保存实例配置到磁盘。"""
    path = _settings_path(wxid)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"[实例配置] 写入 {wxid} 配置失败: {e}")
        return False


def should_reply(wxid: str, sender_wxid: str) -> bool:
    """判断当前实例是否应该回复某个发送者。

    被 ChatMonitor 在处理每条消息前调用，实现 per-instance 黑白名单过滤。
    """
    cfg = load_instance_settings(wxid)

    # 1. 该实例已关闭自动回复
    if not cfg.get("auto_reply_enabled", True):
        return False

    mode = cfg.get("contact_filter_mode", "none")

    # 2. 白名单模式：只有在白名单中的发送者才回复
    if mode == "whitelist":
        wl = cfg.get("contact_whitelist", [])
        return sender_wxid in wl

    # 3. 黑名单模式：黑名单中的发送者不回复
    if mode == "blacklist":
        bl = cfg.get("contact_blacklist", [])
        return sender_wxid not in bl

    # 4. 不过滤：全部回复
    return True


def get_instance_system_prompt(wxid: str, global_prompt: str = "") -> str:
    """获取该实例有效的系统提示词（独立提示词优先于全局）。"""
    cfg = load_instance_settings(wxid)
    override = cfg.get("system_prompt_override", "").strip()
    return override if override else global_prompt


# ── API 路由 ──────────────────────────────────────────────────────────────────

@router.get("/{wxid}/settings")
async def get_settings(wxid: str):
    """获取指定微信实例的独立配置。"""
    if not wxid or wxid in ("none", "null", "undefined"):
        return err(40000, "wxid 不能为空")
    settings = load_instance_settings(wxid)

    # 0. 若 instance_settings 中已有密钥，先做一次交叉校验（防止历史 bug 写入脏数据）
    # 场景：之前的 last_key fallback bug 可能把 nudef 的密钥错绑到其他账号的 instance_settings.json
    existing_key = settings.get("wechat_hex_key")
    if existing_key and len(existing_key) == 64:
        try:
            from src.utils.wechat_key_store import verify_wechat_key, clear_persisted_wechat_key
            if not verify_wechat_key(existing_key, wxid):
                logger.warning(f"[实例配置] wxid={wxid} instance_settings 中的密钥 {existing_key[:6]}****** 校验失败，已清除脏数据")
                settings["wechat_hex_key"] = ""
                # 同步从 KeyStore 清除脏数据
                clear_persisted_wechat_key(wxid)
                # 也从 instance_settings.json 持久化清除
                current_saved = load_instance_settings(wxid)
                current_saved["wechat_hex_key"] = ""
                save_instance_settings(wxid, current_saved)
        except Exception as _ve:
            logger.debug(f"[实例配置] 密钥交叉校验异常（非严重）: {_ve}")

    # 1. 密钥回显：若 instance_settings 无密钥，从 KeyStore 补充
    if not settings.get("wechat_hex_key"):
        try:
            from src.utils.wechat_key_store import get_persisted_wechat_key
            persisted_key = get_persisted_wechat_key(wxid)
            if persisted_key and len(persisted_key) == 64:
                settings["wechat_hex_key"] = persisted_key
        except Exception:
            pass

    # 2. 密钥回显：若还是空，尝试从内存中已运行的 WcdbSessionMonitor 拿
    if not settings.get("wechat_hex_key"):
        try:
            from app.state import account_manager as _am
            if _am:
                for _inst in _am._instances.values():
                    _mon = getattr(_inst, "monitor", None)
                    if _mon and getattr(_mon, "_wcdb_session_monitor", None):
                        _wsm = _mon._wcdb_session_monitor
                        _wsm_wxid = getattr(_wsm, "_wxid", "") or ""
                        if _wsm_wxid == wxid and getattr(_wsm, "_hex_key", ""):
                            settings["wechat_hex_key"] = _wsm._hex_key
                            break
        except Exception:
            pass

    # 3. 数据目录回显：若 instance_settings 无目录，先从内存中运行的 WcdbSessionMonitor 拿
    if not settings.get("wechat_data_dir"):
        try:
            from app.state import account_manager as _am
            if _am:
                for _inst in _am._instances.values():
                    _mon = getattr(_inst, "monitor", None)
                    if _mon and getattr(_mon, "_wcdb_session_monitor", None):
                        _wsm = _mon._wcdb_session_monitor
                        _wsm_wxid = getattr(_wsm, "_wxid", "") or ""
                        if _wsm_wxid == wxid and getattr(_wsm, "_db_path", ""):
                            import os as _os
                            settings["wechat_data_dir"] = _os.path.dirname(_wsm._db_path)
                            break
        except Exception:
            pass

    # 4. 数据目录回显：若内存中也没有，尝试用已知密钥自动探测磁盘路径
    if not settings.get("wechat_data_dir") and settings.get("wechat_hex_key"):
        try:
            from src.wechat_4x.db_match_helper import auto_detect_db_path
            import os as _os
            _detected = auto_detect_db_path(settings["wechat_hex_key"], wxid)
            if _detected and _os.path.exists(_detected):
                settings["wechat_data_dir"] = _os.path.dirname(_detected)
        except Exception:
            pass

    return ok(settings)


@router.post("/{wxid}/settings")
async def save_settings(wxid: str, body: InstanceSettings):
    """更新指定微信实例的独立配置（仅更新传入的字段，其余保留原值）。"""
    if not wxid or wxid in ("none", "null", "undefined"):
        return err(40000, "wxid 不能为空")

    current = load_instance_settings(wxid)
    patch = body.model_dump(exclude_none=True)
    current.update(patch)

    if not save_instance_settings(wxid, current):
        return err(50000, "配置写入失败")

    # 实时同步到内存中的 ChatMonitor（无需重启）
    _sync_to_live_monitor(wxid, current)

    logger.info(f"[实例配置] {wxid} 配置已更新: {list(patch.keys())}")
    return ok(current)


@router.delete("/{wxid}/settings")
async def reset_settings(wxid: str):
    """重置指定微信实例的独立配置为默认值。"""
    if not wxid:
        return err(40000, "wxid 不能为空")
    if save_instance_settings(wxid, dict(_DEFAULT_SETTINGS)):
        return ok_msg("已重置为默认配置")
    return err(50000, "重置失败")


@router.get("/settings/batch")
async def batch_get_settings():
    """批量获取所有在线实例的配置摘要（账号管理页面大盘视图用）。"""
    try:
        from app.state import account_manager as am
        result = {}
        for hwnd, inst in am._instances.items():
            wxid = inst.wxid
            if not wxid:
                continue
            cfg = load_instance_settings(wxid)
            result[wxid] = {
                "wxid": wxid,
                "nickname": inst.nickname,
                "auto_reply_enabled": cfg.get("auto_reply_enabled", True),
                "contact_filter_mode": cfg.get("contact_filter_mode", "none"),
                "has_prompt_override": bool(cfg.get("system_prompt_override", "").strip()),
                "industry_profile_id": cfg.get("industry_profile_id", ""),
                "note": cfg.get("note", ""),
            }
        return ok(result)
    except Exception as e:
        logger.error(f"[实例配置] 批量获取异常: {e}")
        return ok({})


def _sync_to_live_monitor(wxid: str, settings: dict):
    """将新配置实时同步到内存中对应实例的 ChatMonitor。"""
    try:
        from app.state import account_manager as am
        inst = am.get_instance_by_wxid(wxid)
        if inst and inst.monitor:
            inst.monitor._instance_settings = settings
            logger.debug(f"[实例配置] 已实时同步到 {wxid} 的 ChatMonitor")
    except Exception as e:
        logger.debug(f"[实例配置] 实时同步异常: {e}")
