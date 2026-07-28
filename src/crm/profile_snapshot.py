import json
import logging
import threading
from pathlib import Path
from datetime import datetime
from .customer_profile import CustomerProfile
from .tag_manager import TagManager

logger = logging.getLogger(__name__)

_LEGACY_GLOBAL_PROFILE_FILE = Path.home() / ".xm-ai-bot" / "crm_profiles.json"

def _crm_snapshot_path(account_id: str) -> Path:
    from src.crm.account_data import get_account_data_dir
    aid = account_id or "default"
    return Path(get_account_data_dir(aid)) / "crm_profiles.json"

def save_local_snapshot(manager):
    """防抖落盘"""
    if getattr(manager, "_snapshot_timer", None):
        manager._snapshot_timer.cancel()
    manager._snapshot_timer = threading.Timer(10.0, lambda: _do_save_snapshot(manager))
    manager._snapshot_timer.daemon = True
    manager._snapshot_timer.start()

def _do_save_snapshot(manager):
    try:
        profiles_data = [p.to_dict() for p in manager._cache.values()]
        snapshot = {
            "account_id": manager.account_id,
            "profiles": profiles_data,
            "saved_at": datetime.now().isoformat(),
        }
        path = _crm_snapshot_path(manager.account_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        logger.debug(f"[CRM] 💾 本地画像快照已保存 ({len(profiles_data)} 条) → {path}")
    except Exception as e:
        logger.warning(f"[CRM] 💾 本地画像快照保存失败: {e}")

def load_local_snapshot(manager):
    try:
        path = _crm_snapshot_path(manager.account_id)
        if not path.exists():
            _maybe_migrate_legacy_global_snapshot(manager.account_id, path)

        if not path.exists():
            return
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return
        snap_acc = raw.get("account_id")
        if snap_acc and snap_acc != manager.account_id:
            logger.warning(
                f"[CRM] 跳过本地快照：文件 account_id={snap_acc!r} 与当前 {manager.account_id!r} 不一致"
            )
            return

        profiles_data = raw.get("profiles", [])
        if not profiles_data:
            return

        restored = 0
        for item in profiles_data:
            try:
                profile = CustomerProfile.from_dict(item)
                if profile.wxid and profile.wxid not in manager._cache:
                    manager._cache[profile.wxid] = profile
                    restored += 1
            except Exception:
                continue

        saved_at = raw.get("saved_at", "未知")
        if restored > 0:
            logger.info(
                f"[CRM] 💾 从本地快照恢复 {restored} 条客户画像 (快照时间: {saved_at})"
            )
    except Exception as e:
        logger.warning(f"[CRM] 💾 本地画像快照加载失败: {e}")

def _maybe_migrate_legacy_global_snapshot(account_id: str, target_path: Path) -> None:
    if not _LEGACY_GLOBAL_PROFILE_FILE.exists():
        return
    try:
        raw = json.loads(_LEGACY_GLOBAL_PROFILE_FILE.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return
        legacy_acc = raw.get("account_id")
        if legacy_acc and legacy_acc != account_id:
            return
        if not legacy_acc:
            logger.info("[CRM] 跳过迁移旧版全局 crm_profiles.json（无 account_id，避免多号混数据）")
            return
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"[CRM] 已从旧版全局快照迁移到 {target_path}")
    except Exception as e:
        logger.warning(f"[CRM] 迁移旧版客户画像快照失败: {e}")


# ==================== 画像自愈工具（从 profile_manager 拆离）====================

def is_standard_wxid(wxid: str) -> bool:
    """判断 wxid 是否为标准微信 ID（纯 ASCII、无中文/空白/特殊分隔符）。"""
    if not wxid:
        return False
    try:
        wxid.encode("ascii")
    except UnicodeEncodeError:
        return False
    for c in wxid:
        if c.isspace() or c in (",", ";", "\\", "/"):
            return False
    return True


def heal_profiles(manager) -> None:
    """自愈清理：将非标准临时画像合并到对应的真实 ID 画像中，并删除脏临时条目。

    从 profile_manager._heal_profiles() 拆离，满足单文件 300 行限额红线。
    """
    standard_profiles: dict = {}  # nickname/remark -> profile
    temp_profiles = []            # 非标准临时 ID 的画像列表

    # 建立标准画像的查找索引
    for wxid, p in list(manager._cache.items()):
        if is_standard_wxid(wxid):
            if p.nickname:
                standard_profiles[p.nickname.strip()] = p
            if p.remark:
                standard_profiles[p.remark.strip()] = p
        else:
            temp_profiles.append(p)

    # 合并与清理
    changed = False
    for tp in temp_profiles:
        temp_key = tp.wxid.strip()
        target_profile = standard_profiles.get(temp_key)
        if not target_profile and tp.nickname:
            target_profile = standard_profiles.get(tp.nickname.strip())

        if target_profile:
            logger.info(f"[CRM自愈] 发现重复临时画像 '{tp.wxid}'，合并至标准画像 '{target_profile.wxid}'")
            if tp.tags:
                target_profile.tags = TagManager.merge_tags(target_profile.tags, tp.tags)
            if tp.notes:
                for note in tp.notes:
                    if note not in target_profile.notes:
                        target_profile.notes.append(note)
            if tp.chat_count:
                target_profile.chat_count += tp.chat_count
            manager._cache.pop(tp.wxid, None)
            changed = True
        elif not is_standard_wxid(tp.wxid) and not tp.tags and tp.chat_count == 0:
            # 清理既无标签又无会话的纯脏临时画像
            manager._cache.pop(tp.wxid, None)
            changed = True

    if changed:
        save_local_snapshot(manager)
