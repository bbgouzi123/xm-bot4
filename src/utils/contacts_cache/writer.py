import os
import json
import logging
from typing import List
from src.crm.account_data import get_contacts_path
from .delete_store import record_deleted_contacts

logger = logging.getLogger(__name__)

class ContactsCacheWriterMixin:
    def remove_friends_by_match(self, account_id: str, wxids: List[str], name_categories: List[dict]) -> int:
        """批量删除联系人：优先按 wxid，其次按 name+category（兼容无 wxid 记录）。"""
        wxid_targets = {str(x).strip() for x in (wxids or []) if str(x).strip()}
        nc_targets = set()
        for item in (name_categories or []):
            if not isinstance(item, dict):
                continue
            name = (item.get("name") or "").strip()
            category = (item.get("category") or "联系人").strip() or "联系人"
            if name:
                nc_targets.add((name, category))

        if not wxid_targets and not nc_targets:
            return 0

        with self._rw_lock:
            friends = self._friends.get(account_id, [])
            before = len(friends)

            def _should_delete(f: dict) -> bool:
                wxid = (f.get("wxid") or "").strip()
                if wxid and wxid in wxid_targets:
                    return True
                name = (f.get("name") or "").strip()
                display_name = (f.get("display_name") or "").strip()
                remark = (f.get("remark") or "").strip()
                category = (f.get("category") or "联系人").strip() or "联系人"
                for t_name, t_cat in nc_targets:
                    if name == t_name or display_name == t_name or remark == t_name:
                        if category == t_cat or t_cat in ("联系人", "群聊", ""):
                            return True
                return False

            friends = [f for f in friends if not _should_delete(f)]
            self._friends[account_id] = friends
            removed = before - len(friends)

        if removed > 0:
            self._async_push("contacts", self._friends.get(account_id, []))
            self._force_save_snapshot()
            self._delete_contact_from_local_files(account_id, wxids=wxids, name_categories=name_categories)
            logger.info(f"[ContactsCache] 🗑 批量删除联系人（含无wxid匹配）{removed} 个 (account={account_id})")
        return removed

    def _delete_contact_from_local_files(self, account_id: str, wxids: List[str] = None, name_categories: List[dict] = None):
        """从本地物理 contacts.json 文件中删除指定的联系人，确保彻底删除"""
        # 记录到被删黑名单
        record_deleted_contacts(account_id, wxids=wxids, name_categories=name_categories)

        wxid_targets = {str(x).strip() for x in (wxids or []) if str(x).strip()}
        nc_targets = set()
        for item in (name_categories or []):
            if isinstance(item, dict):
                name = (item.get("name") or "").strip()
                category = (item.get("category") or "联系人").strip() or "联系人"
                if name:
                    nc_targets.add((name, category))

        if not wxid_targets and not nc_targets:
            return

        def _should_delete(c: dict) -> bool:
            wxid = (c.get("wxid") or "").strip()
            if wxid and wxid in wxid_targets:
                return True
            name = (c.get("name") or "").strip()
            display_name = (c.get("display_name") or "").strip()
            remark = (c.get("remark") or "").strip()
            category = (c.get("category") or "联系人").strip() or "联系人"
            for t_name, t_cat in nc_targets:
                if name == t_name or display_name == t_name or remark == t_name:
                    if category == t_cat or t_cat in ("联系人", "群聊", ""):
                        return True
            return False

        # 1. 更新账号隔离 of contacts.json
        local_path = get_contacts_path(account_id)
        if local_path and os.path.exists(local_path):
            try:
                with open(local_path, "r", encoding="utf-8") as f:
                    contacts = json.load(f)
                if isinstance(contacts, list):
                    filtered = [c for c in contacts if not _should_delete(c)]
                    with open(local_path, "w", encoding="utf-8") as f:
                        json.dump(filtered, f, ensure_ascii=False, indent=2)
                    logger.info(f"[ContactsCache] 已从隔离的 contacts.json 删除 {len(contacts) - len(filtered)} 个联系人")
            except Exception as e:
                logger.warning(f"[ContactsCache] 更新隔离 contacts.json 失败: {e}")

        # 2. 更新全局 ~/.xm-ai-bot/contacts.json
        global_path = os.path.expanduser("~/.xm-ai-bot/contacts.json")
        if os.path.exists(global_path):
            try:
                with open(global_path, "r", encoding="utf-8") as f:
                    contacts = json.load(f)
                if isinstance(contacts, list):
                    filtered = [c for c in contacts if not _should_delete(c)]
                    with open(global_path, "w", encoding="utf-8") as f:
                        json.dump(filtered, f, ensure_ascii=False, indent=2)
                    logger.info(f"[ContactsCache] 已从全局 contacts.json 删除 {len(contacts) - len(filtered)} 个联系人")
            except Exception as e:
                logger.warning(f"[ContactsCache] 更新全局 contacts.json 失败: {e}")

    def _sync_to_account_contacts_json(self, account_id: str):
        """将当前账号的最新 friends 和 groups 合并写入到该账号的专属 contacts.json 物理文件中"""
        try:
            account_id = self._normalize_account_id(account_id)
            from src.crm.account_data import get_contacts_path
            import json
            import os

            local_path = get_contacts_path(account_id)
            if not local_path:
                return

            with self._rw_lock:
                friends = list(self._friends.get(account_id, []))
                groups = list(self._groups.get(account_id, []))

            merged = []
            for f in friends:
                f_copy = dict(f)
                f_copy["contact_type"] = "friend"
                if "category" not in f_copy:
                    f_copy["category"] = "联系人"
                merged.append(f_copy)
            for g in groups:
                g_copy = dict(g)
                g_copy["contact_type"] = "group"
                if "category" not in g_copy:
                    g_copy["category"] = "群聊"
                merged.append(g_copy)

            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            with open(local_path, "w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False, indent=2)
            logger.info(f"[ContactsCache] 已同步写入账号专属 contacts.json ({len(merged)} 个联系人)")
        except Exception as e:
            logger.warning(f"[ContactsCache] 同步写入账号专属 contacts.json 失败: {e}")
