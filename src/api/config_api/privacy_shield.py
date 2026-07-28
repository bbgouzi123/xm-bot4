from fastapi import Request
from src.utils.response import ok, err, ok_msg
from .state import router
from . import state

@router.get("/api/privacy-shield/status")
async def get_privacy_shield_status():
    from src.uia.privacy_shield import get_privacy_shield
    shield = get_privacy_shield()
    return ok({**shield.get_status()})

@router.post("/api/privacy-shield/toggle")
async def toggle_privacy_shield():
    from src.uia.privacy_shield import get_privacy_shield
    shield = get_privacy_shield()
    wechat_hwnd = state._driver.hwnd if state._driver else None
    new_state = shield.toggle(wechat_hwnd)
    return ok({"enabled": new_state})

@router.post("/api/privacy-shield/enable")
async def enable_privacy_shield():
    from src.uia.privacy_shield import get_privacy_shield
    shield = get_privacy_shield()
    wechat_hwnd = state._driver.hwnd if state._driver else None
    if not wechat_hwnd:
        return err(40000, "微信未连接，无法启用隐私保护")
    shield.enable(wechat_hwnd)
    return ok({"enabled": True})

@router.post("/api/privacy-shield/disable")
async def disable_privacy_shield():
    from src.uia.privacy_shield import get_privacy_shield
    shield = get_privacy_shield()
    shield.disable()
    return ok({"enabled": False})

@router.post("/api/privacy-shield/set-record-mode")
async def set_privacy_shield_record_mode(request: Request):
    body = await request.json()
    enabled = bool(body.get("enabled", False))
    from src.uia.privacy_shield import get_privacy_shield
    shield = get_privacy_shield()
    shield.set_record_mode(enabled)
    return ok({"record_mode": enabled})

@router.get("/api/system/bot/auto-reply-config")
async def get_auto_reply_config(wxid: str = None):
    """读取自动回复配置 — 按微信号隔离存储，支持显式 wxid。

    存储架构：
        ~/.xm-ai-bot/accounts/{wxid}/settings.json → reply 字段
    首次迁移：
        若 account_settings 中无 reply.auto_chat_friend_mode 等字段，
        自动从全局 config（旧存储）一次性迁移过来。
    """
    if not wxid:
        from src.crm.account_data import get_active_account
        wxid = get_active_account()
    reply_cfg = _get_reply_config_isolated(wxid)
    return ok(reply_cfg)


@router.post("/api/system/bot/auto-reply-config")
async def save_auto_reply_config(request: Request, wxid: str = None):
    """保存自动回复配置 — 写入按微信号隔离的 account_settings，支持显式 wxid。"""
    data = await request.json()

    if not wxid:
        wxid = data.get("wxid")

    if not wxid:
        from src.crm.account_data import get_active_account
        wxid = get_active_account()

    from src.crm.account_data import get_account_settings, save_account_settings
    settings = get_account_settings(wxid, force_reload=True)
    reply = settings.get("reply", {})

    _REPLY_KEYS = [
        "bot_group_auto_start", "auto_chat_friend_excludes",
        "auto_chat_group_excludes", "moment_interact_friend_excludes",
        "auto_chat_friend_whitelist", "auto_chat_group_whitelist",
        "moment_interact_friend_whitelist",
        "moment_interact_friend_mode", "auto_chat_friend_mode",
        "auto_chat_group_mode", "auto_chat_group_at_only",
        "friend_daily_limit", "friend_limit_reply",
        "respond_to_all_mentions",
        "auto_receipt_enabled", "custom_receipt_keywords",
        "moment_interact_tags", "moment_interact_tag_mode",
        "auto_tag_enabled", "fetch_profile_enabled",
        "voice_reply_enabled", "voice_reply_id",
        "auto_redpacket_friend_enabled", "auto_redpacket_group_enabled",
        "auto_accept_group_enabled", "delegated_admin_wxid", "delegated_admin_name",
    ]
    for key in _REPLY_KEYS:
        if key in data:
            val = data[key]
            if key == "friend_daily_limit":
                val = int(val)
            elif key == "friend_limit_reply":
                val = str(val)
            elif key in (
                "auto_receipt_enabled", "auto_tag_enabled", "fetch_profile_enabled",
                "voice_reply_enabled", "auto_redpacket_friend_enabled", "auto_redpacket_group_enabled",
                "auto_accept_group_enabled"
            ):
                val = bool(val)
            elif key in ("voice_reply_id", "delegated_admin_wxid", "delegated_admin_name"):
                val = str(val)
            elif key == "custom_receipt_keywords" or key == "moment_interact_tags":
                # 兼容前端传入的逗号分隔字符串 or 数组
                if isinstance(val, str):
                    val = [x.strip() for x in val.split(",") if x.strip()]
                elif isinstance(val, list):
                    val = [str(x).strip() for x in val if str(x).strip()]
            elif key == "moment_interact_tag_mode":
                val = str(val).strip()
            reply[key] = val

    settings["reply"] = reply
    save_account_settings(settings, wxid)

    if "auto_reply_enabled" in data:
        val = bool(data["auto_reply_enabled"])
        try:
            from src.api.config_api import _load_configs, _save_configs
            g_cfgs = _load_configs()
            g_cfgs["auto_reply_enabled"] = val
            _save_configs(g_cfgs)
        except Exception:
            pass
        try:
            from src.api.instance_settings_api import load_instance_settings, save_instance_settings, _sync_to_live_monitor
            inst_cfg = load_instance_settings(wxid)
            inst_cfg["auto_reply_enabled"] = val
            save_instance_settings(wxid, inst_cfg)
            _sync_to_live_monitor(wxid, inst_cfg)
            logger.info(f"[自动回复配置] 同步实例 {wxid} 自动回复开关为 {val}")
        except Exception as inst_ex:
            logger.error(f"[自动回复配置] 同步实例开关异常: {inst_ex}")

    return ok_msg("操作成功")


# ==================== 微信号隔离配置读取 ====================

# 需要按微信号隔离的回复配置键列表
_REPLY_ISOLATED_KEYS = {
    "bot_group_auto_start": False,
    "auto_chat_friend_excludes": [],
    "auto_chat_group_excludes": [],
    "moment_interact_friend_excludes": [],
    "auto_chat_friend_whitelist": [],
    "auto_chat_group_whitelist": [],
    "moment_interact_friend_whitelist": [],
    "moment_interact_friend_mode": "black",
    "auto_chat_friend_mode": "white",
    "auto_chat_group_mode": "white",
    "auto_chat_group_at_only": True,
    "friend_daily_limit": 0,
    "friend_limit_reply": "您今天的免费咨询额度已用完，请联系客服解锁更多次数。",
    "respond_to_all_mentions": False,
    "auto_receipt_enabled": False,
    "custom_receipt_keywords": [],
    "moment_interact_tags": [],
    "moment_interact_tag_mode": "none",
    "auto_tag_enabled": True,
    "fetch_profile_enabled": True,
    "voice_reply_enabled": False,
    "voice_reply_id": "",
    "auto_redpacket_friend_enabled": False,
    "auto_redpacket_group_enabled": False,
    "auto_accept_group_enabled": False,
    "delegated_admin_wxid": "",
    "delegated_admin_name": "",
}


def _read_reply_from_disk_direct(wxid: str = None) -> dict:
    """
    🛡️ 升级安全保护：直接从本地磁盘文件读取 reply 配置，完全绕过内存缓存和云端层。

    用于在 _get_reply_config_isolated 中，当内存/云端数据短暂为空时（如升级重启的
    冷启动竞态窗口），作为最后一道防线，防止白名单/黑名单被默认空值静默覆盖。
    """
    try:
        import os, json
        from src.crm.account_data import get_account_data_dir
        path = os.path.join(get_account_data_dir(wxid), "settings.json")
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        # 兼容旧版嵌套包裹格式
        if isinstance(raw, dict) and "settings" in raw and isinstance(raw["settings"], dict):
            raw = raw["settings"]
        reply = raw.get("reply", {}) if isinstance(raw, dict) else {}
        # 仅当磁盘 reply 中包含至少一个隔离配置字段时，才视为有效数据
        _guard_keys = (
            "auto_chat_friend_mode", "auto_chat_group_mode",
            "auto_chat_group_whitelist", "auto_chat_friend_whitelist",
            "auto_chat_group_excludes", "auto_chat_friend_excludes",
        )
        if isinstance(reply, dict) and any(k in reply for k in _guard_keys):
            return reply
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"[升级保护] 直接读磁盘 reply 配置失败: {e}")
    return {}


def _get_reply_config_isolated(wxid: str = None) -> dict:
    """从 account_settings（按微信号隔离）读取自动回复配置。

    首次访问时若无配置则自动进行初始化，后续纯粹从隔离存储读取。
    """
    import logging as _logging
    _logger = _logging.getLogger(__name__)
    from src.crm.account_data import get_account_settings, save_account_settings

    settings = get_account_settings(wxid)
    reply = settings.get("reply", {})

    # 检查是否已初始化：如果 reply 中没有 auto_chat_friend_mode，触发首次初始化
    if "auto_chat_friend_mode" not in reply:
        # 🛡️ 升级安全防线：先直接读磁盘物理文件，防止升级重启期间内存/云端短暂为空
        # 时把已有白名单/黑名单等配置用默认空值覆盖（白名单丢失最常见于此竞态场景）
        disk_reply = _read_reply_from_disk_direct(wxid)
        if disk_reply:
            _logger.info(
                f"[升级保护] 检测到内存/云端 reply 为空但磁盘有历史配置，"
                f"使用磁盘数据兜底（白名单保护生效）(wxid: {wxid or 'default'})"
            )
            # 补全磁盘数据中缺失的新字段（保留已有白名单，只追加新默认字段）
            for key, default in _REPLY_ISOLATED_KEYS.items():
                if key not in disk_reply:
                    disk_reply[key] = default
            settings["reply"] = disk_reply
            save_account_settings(settings, wxid)
            return {key: disk_reply.get(key, default) for key, default in _REPLY_ISOLATED_KEYS.items()}

        # 磁盘也没有有效配置时，说明是真正的首次使用，安全地初始化默认值
        initialized = False
        for key, default in _REPLY_ISOLATED_KEYS.items():
            if key not in reply:
                reply[key] = default
                initialized = True
        if initialized:
            settings["reply"] = reply
            save_account_settings(settings, wxid)
            _logger.info(f"[配置初始化] 已为微信号隔离存储初始化默认配置 (wxid: {wxid or 'default'})")

    # 构建返回值
    result = {}
    for key, default in _REPLY_ISOLATED_KEYS.items():
        result[key] = reply.get(key, default)
    return result


@router.get("/api/test")
async def test_endpoint():
    return ok_msg("ok")

@router.get("/api/system/local-logs")
async def get_system_local_logs():
    import os
    appdata = os.getenv("APPDATA") or os.path.expanduser("~")
    log_dir = os.path.join(appdata, "xm-bot4", "logs")
    latest_log = os.path.join(log_dir, "latest.log")
    
    logs_content = ""
    if os.path.exists(latest_log):
        try:
            with open(latest_log, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
                logs_content = "".join(lines[-2000:])
        except Exception as e:
            logs_content = f"读取日志出错: {str(e)}"
    else:
        logs_content = "暂无本地日志文件"
        
    return ok({"logs": logs_content})
