import logging
import threading
from datetime import datetime
from typing import Dict, List
from .helper import ContactsCacheHelperMixin
from .writer import ContactsCacheWriterMixin
from .snapshot import ContactsCacheSnapshotMixin
from .cloud import ContactsCacheCloudMixin
from .delete_store import filter_deleted_contacts, remove_from_deleted_contacts

logger = logging.getLogger(__name__)

class ContactsCache(
    ContactsCacheHelperMixin,
    ContactsCacheWriterMixin,
    ContactsCacheSnapshotMixin,
    ContactsCacheCloudMixin
):
    """全局统一通讯录内存缓存"""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._rw_lock = threading.RLock()
        self._friends: Dict[str, List[dict]] = {}
        self._groups: Dict[str, List[dict]] = {}
        self._group_members: Dict[str, List[dict]] = {}
        self._contact_tags: Dict[str, List[dict]] = {}
        logger.info("[ContactsCache] 通讯录内存缓存已初始化")

    def set_friends(self, account_id: str, friends: List[dict], sync_cloud: bool = True):
        account_id = self._normalize_account_id(account_id)
        friends = filter_deleted_contacts(account_id, friends, is_group=False)
        deduped = self._deduplicate_friends(friends)
        with self._rw_lock:
            self._friends[account_id] = deduped
        if sync_cloud:
            self._async_push("contacts", deduped)
        self._save_local_snapshot()
        self._sync_to_account_contacts_json(account_id)

    def _lazy_load_account_contacts(self, account_id: str):
        account_id = self._normalize_account_id(account_id)
        with self._rw_lock:
            if account_id != "default" and "default" in self._friends and account_id not in self._friends:
                import os
                from src.crm.account_data import get_contacts_path
                contacts_path = get_contacts_path(account_id)
                if not contacts_path or not os.path.exists(contacts_path):
                    self._friends[account_id] = list(self._friends["default"])
                    self._groups[account_id] = list(self._groups.get("default", []))
                    self._group_members[account_id] = list(self._group_members.get("default", []))
                    self._contact_tags[account_id] = list(self._contact_tags.get("default", []))
                    return
            if account_id not in self._friends and account_id not in self._groups:
                import os
                import json
                from src.crm.account_data import get_contacts_path
                contacts_path = get_contacts_path(account_id)
                if contacts_path and os.path.exists(contacts_path):
                    try:
                        with open(contacts_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        if isinstance(data, list) and data:
                            mapped_friends = []
                            mapped_groups = []
                            for item in data:
                                cat = item.get("category", "")
                                c_type = item.get("contact_type", "")
                                if cat == "群聊" or c_type == "group":
                                    mapped_groups.append(item)
                                else:
                                    mapped_friends.append(item)
                            self._friends[account_id] = mapped_friends
                            self._groups[account_id] = mapped_groups
                            logger.info(f"[ContactsCache] 延迟加载成功：从磁盘 {contacts_path} 加载了 {len(mapped_friends)} 个好友, {len(mapped_groups)} 个群聊")
                    except Exception as e:
                        logger.warning(f"[ContactsCache] 延迟加载磁盘通讯录失败: {e}")

    def get_friends(self, account_id: str = None, tag: str = None) -> List[dict]:
        account_id = self._normalize_account_id(account_id) if account_id else None
        if account_id:
            self._lazy_load_account_contacts(account_id)
        with self._rw_lock:
            if account_id:
                friends = list(self._friends.get(account_id, []))
            else:
                friends = []
                for v in self._friends.values():
                    friends.extend(v)
        if tag:
            friends = [
                f for f in friends 
                if tag in (f.get("tag", "") or "") 
                or (isinstance(f.get("tags"), list) and tag in f.get("tags"))
            ]
        return friends

    def clear_memory_cache(self):
        with self._rw_lock:
            self._friends.clear(); self._groups.clear(); self._group_members.clear(); self._contact_tags.clear()
            if hasattr(self, '_cloud_loaded_flag'): self._cloud_loaded_flag.clear()
            logger.info("[ContactsCache] 内存缓存已清空 (账号切换)")

    def remove_friend(self, account_id: str, wxid: str) -> bool:
        """按 wxid 删除单个联系人。返回是否删除成功。"""
        account_id = self._normalize_account_id(account_id)
        with self._rw_lock:
            friends = self._friends.get(account_id, [])
            before = len(friends)
            friends = [f for f in friends if (f.get("wxid") or "") != wxid]
            self._friends[account_id] = friends
            removed = before - len(friends)
        if removed > 0:
            self._async_push("contacts", self._friends.get(account_id, []))
            self._save_local_snapshot()
            self._delete_contact_from_local_files(account_id, wxids=[wxid])
            logger.info(f"[ContactsCache] 🗑 已删除联系人 wxid={wxid} (account={account_id})")
        return removed > 0

    def remove_friends(self, account_id: str, wxids: List[str]) -> int:
        account_id = self._normalize_account_id(account_id)
        if not wxids or not (targets := {str(x).strip() for x in wxids if str(x).strip()}):
            return 0
        with self._rw_lock:
            friends = self._friends.get(account_id, [])
            before = len(friends)
            friends = [f for f in friends if (f.get("wxid") or "") not in targets]
            self._friends[account_id] = friends
            removed = before - len(friends)
        if removed > 0:
            self._async_push("contacts", self._friends.get(account_id, []))
            self._save_local_snapshot()
            self._delete_contact_from_local_files(account_id, wxids=wxids)
            logger.info(f"[ContactsCache] 🗑 批量删除联系人 {removed} 个 (account={account_id})")
        return removed

    def set_groups(self, account_id: str, groups: List[dict], sync_cloud: bool = True):
        """UIA 抓取群聊列表后写入内存 + 异步推同步后端 + 本地落盘"""
        account_id = self._normalize_account_id(account_id)
        groups = filter_deleted_contacts(account_id, groups, is_group=True)
        with self._rw_lock:
            self._groups[account_id] = groups
        if sync_cloud:
            self._async_push("groups", groups)
        self._save_local_snapshot()
        self._sync_to_account_contacts_json(account_id)

    def get_groups(self, account_id: str = None) -> List[dict]:
        """获取群聊列表"""
        account_id = self._normalize_account_id(account_id) if account_id else None
        if account_id:
            self._lazy_load_account_contacts(account_id)
        with self._rw_lock:
            if account_id:
                return list(self._groups.get(account_id, []))
            groups = []
            for v in self._groups.values():
                groups.extend(v)
            return groups

    def set_group_members(self, account_id: str, members: List[dict], sync_cloud: bool = True):
        """写入群成员"""
        account_id = self._normalize_account_id(account_id)
        with self._rw_lock:
            self._group_members[account_id] = members
        if sync_cloud:
            self._async_push("group-members", members)

    def get_group_members(self, account_id: str = None, group_name: str = None) -> List[dict]:
        """获取群成员"""
        account_id = self._normalize_account_id(account_id) if account_id else None
        with self._rw_lock:
            if account_id:
                members = list(self._group_members.get(account_id, []))
            else:
                members = []
                for v in self._group_members.values():
                    members.extend(v)
        if group_name:
            members = [m for m in members if m.get("group_name") == group_name]
        return members

    def set_contact_tags(self, account_id: str, tags: List[dict], sync_cloud: bool = True):
        """写入标签"""
        account_id = self._normalize_account_id(account_id)
        with self._rw_lock:
            self._contact_tags[account_id] = tags
        if sync_cloud:
            self._async_push("contact-tags", tags)

    def get_contact_tags(self, account_id: str = None) -> List[dict]:
        """获取标签"""
        account_id = self._normalize_account_id(account_id) if account_id else None
        with self._rw_lock:
            if account_id:
                return list(self._contact_tags.get(account_id, []))
            tags = []
            for v in self._contact_tags.values():
                tags.extend(v)
            return tags

    def update_friend(self, account_id: str, wxid: str, **kwargs):
        """更新单个好友的字段"""
        account_id = self._normalize_account_id(account_id)
        friend_name = None
        friend_remark = None
        with self._rw_lock:
            friends = self._friends.get(account_id, [])
            for f in friends:
                if f.get("wxid") == wxid:
                    f.update(kwargs)
                    friend_name = f.get("name")
                    friend_remark = f.get("remark")
                    break
        self._async_push("contacts", self._friends.get(account_id, []))
        if kwargs.get("is_takeover") is False:
            try:
                from app.state import monitor
                if monitor:
                    keys_to_remove = [wxid]
                    if friend_name:
                        keys_to_remove.append(friend_name)
                    if friend_remark:
                        keys_to_remove.append(friend_remark)
                    for k in keys_to_remove:
                        monitor._manual_interventions.pop(k, None)
                        if hasattr(monitor, "_human_takeover_sessions"):
                            monitor._human_takeover_sessions.discard(k)
                    
                    try:
                        partition = monitor.get_account_partition()
                        for k in keys_to_remove:
                            partition.suspended_sessions.pop(k, None)
                    except Exception:
                        pass
            except Exception as e:
                logger.error(f"[ContactsCache] 清除人工干预避让状态失败: {e}")

    def merge_friend_detail_by_name(self, account_id: str, name: str, category: str, sync_cloud: bool = True, **kwargs):
        """详情同步后按 name+category 合并。"""
        account_id = self._normalize_account_id(account_id)
        # ── 如果此人被单条重新同步，从已删除黑名单撤销 ───────────────────
        remove_from_deleted_contacts(account_id, wxid=kwargs.get("wxid"), name=name, category=category)
        cat = category or "联系人"
        found = False
        friend_wxid = None
        friend_remark = None
        with self._rw_lock:
            friends = self._friends.get(account_id, [])
            for f in friends:
                if f.get("name") == name and (f.get("category") or "联系人") == cat:
                    for k, v in kwargs.items():
                        if v is not None:
                            f[k] = v
                    friend_wxid = f.get("wxid")
                    friend_remark = f.get("remark")
                    found = True
                    break
            
            if not found:
                new_contact = {
                    "name": name,
                    "category": cat,
                    "index": "#",
                    "syncTime": datetime.now().isoformat()
                }
                for k, v in kwargs.items():
                    if v is not None:
                        new_contact[k] = v
                friends.append(new_contact)
                self._friends[account_id] = friends
                logger.info(f"[ContactsCache] ✨ 盲扫补全联系人: name={name!r} category={cat!r}")
                found = True

        if found:
            logger.info(f"[ContactsCache] ✏ 合并详情: name={name!r} fields={list(kwargs.keys())}")
            if sync_cloud:
                with self._rw_lock:
                    push_data = list(self._friends.get(account_id, []))
                self._async_push("contacts", push_data)
            self._force_save_snapshot()

        if kwargs.get("is_takeover") is False:
            try:
                from app.state import monitor
                if monitor:
                    keys_to_remove = [name]
                    if friend_wxid:
                        keys_to_remove.append(friend_wxid)
                    if friend_remark:
                        keys_to_remove.append(friend_remark)
                    for k in keys_to_remove:
                        monitor._manual_interventions.pop(k, None)
                        if hasattr(monitor, "_human_takeover_sessions"):
                            monitor._human_takeover_sessions.discard(k)
                    try:
                        partition = monitor.get_account_partition()
                        for k in keys_to_remove:
                            partition.suspended_sessions.pop(k, None)
                    except Exception:
                        pass
            except Exception as e:
                logger.error(f"[ContactsCache] 清除人工干预避让状态失败: {e}")

    def get_stats(self, account_id: str = None) -> dict:
        account_id = self._normalize_account_id(account_id) if account_id else None
        return {"friends_count": len(self.get_friends(account_id)), "groups_count": len(self.get_groups(account_id)), "group_members_count": len(self.get_group_members(account_id)), "tags_count": len(self.get_contact_tags(account_id))}
