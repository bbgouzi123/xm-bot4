import logging
from typing import List, Dict
from src.uia.contacts.constants import is_synthetic_placeholder_wxid

logger = logging.getLogger(__name__)

class ContactsCacheHelperMixin:
    @staticmethod
    def _deduplicate_friends(friends: List[dict]) -> List[dict]:
        """对好友列表去重：同名同 category 的记录只保留有真实 wxid 的那条。"""
        by_name_cat: Dict[str, dict] = {}  # key = "name::category"
        by_wxid: Dict[str, dict] = {}
        for f in friends:
            wxid = (f.get("wxid") or "").strip()
            name = (f.get("name") or "").strip()
            cat = (f.get("category") or "联系人").strip()
            key = f"{name}::{cat}"
            if wxid and not is_synthetic_placeholder_wxid(wxid):
                by_wxid[wxid] = f
                by_name_cat[key] = f
            else:
                if key not in by_name_cat:
                    by_name_cat[key] = f
        result_map: Dict[str, dict] = {}
        for f in by_wxid.values():
            result_map[f.get("wxid", id(f))] = f
        for f in by_name_cat.values():
            wxid = (f.get("wxid") or "").strip()
            if wxid and not is_synthetic_placeholder_wxid(wxid):
                result_map[wxid] = f
            else:
                name = (f.get("name") or "").strip()
                cat = (f.get("category") or "联系人").strip()
                key = f"{name}::{cat}"
                has_real = any(
                    (v.get("name") or "").strip() == name
                    and (v.get("category") or "联系人").strip() == cat
                    and not is_synthetic_placeholder_wxid(v.get("wxid") or "")
                    for v in result_map.values()
                )
                if not has_real:
                    result_map[wxid or str(id(f))] = f
        return list(result_map.values())

    def cleanup_synthetic_duplicates(self, account_id: str) -> int:
        """批量清理 uid_xxx 占位记录。返回清理数量。"""
        with self._rw_lock:
            friends = self._friends.get(account_id, [])
            before = len(friends)
            deduped = self._deduplicate_friends(friends)
            self._friends[account_id] = deduped
            removed = before - len(deduped)
        if removed > 0:
            self._async_push("contacts", deduped)
            self._force_save_snapshot()
            logger.info(f"[ContactsCache] 🧹 清理了 {removed} 个重复/占位联系人 (account={account_id})")
        return removed

    def _normalize_account_id(self, account_id: str) -> str:
        if not account_id:
            return "default"
        try:
            from src.crm.account_data import normalize_to_real_wxid, _safe_dirname
            real_id = normalize_to_real_wxid(account_id)
            return _safe_dirname(real_id)
        except Exception:
            return "".join(c for c in account_id if c.isalnum() or c in "-_.")

    def find_wxid_with_db_sync(self, account_id: str, name: str, is_group: bool) -> str:
        """根据名称或备注查找 wxid，如果在缓存中没找到，则实时同步微信数据库再查一次"""
        # 标准化 account_id 防止 key 不一致导致解密锁冷却失效
        if not account_id or account_id in ("main", "default"):
            try:
                from src.crm.account_data import get_active_account
                active_id = get_active_account()
                if active_id and active_id != "default":
                    account_id = active_id
            except Exception:
                pass

        all_friends = self.get_friends(account_id) or []
        all_groups = self.get_groups(account_id) or []
        
        found_wxid = None
        import re
        if is_group:
            clean_name = re.sub(r'[\(（]\d+[\)）]$', '', name).strip()
            for g in all_groups:
                if g.get("name") == name or g.get("name") == clean_name or g.get("wxid") == name:
                    found_wxid = g.get("wxid")
                    break
        else:
            for f in all_friends:
                if f.get("name") == name or f.get("remark") == name or f.get("nickname") == name or f.get("wxid") == name:
                    found_wxid = f.get("wxid")
                    break
                    
        if found_wxid:
            return found_wxid
            
        # 尝试触发解密并实时同步通讯录
        try:
            from src.wechat_4x.db_contact_syncer import sync_contacts_from_db
            from src.wechat_4x.db_match_helper import auto_detect_db_path
            from src.wechat_4x.wcdb_key_extractor import get_wcdb_key_extractor
            import os
            
            from src.utils.wechat_key_store import get_persisted_wechat_key
            hex_key = get_persisted_wechat_key(account_id)
            if not hex_key:
                hex_key = os.environ.get("WCDB_HEX_KEY", "") or os.environ.get("WECHAT_4X_KEY_HEX", "")
            if not hex_key:
                hex_key = get_wcdb_key_extractor().get_key(timeout_s=2.0) or ""
            db_path = auto_detect_db_path(hex_key, account_id) or os.environ.get("WCDB_SESSION_DB_PATH", "") or ""
            
            if db_path and hex_key:
                db_storage_dir = os.path.dirname(os.path.dirname(db_path))
                logger.info(f"[ContactsCache] 未命中缓存，开始实时同步 '{name}' 的数据库记录...")
                sync_contacts_from_db(db_storage_dir, hex_key, account_id)
                
                # 同步完之后再查一次
                all_friends = self.get_friends(account_id) or []
                all_groups = self.get_groups(account_id) or []
                if is_group:
                    clean_name = re.sub(r'[\(（]\d+[\)）]$', '', name).strip()
                    for g in all_groups:
                        if g.get("name") == name or g.get("name") == clean_name or g.get("wxid") == name:
                            found_wxid = g.get("wxid")
                            break
                else:
                    for f in all_friends:
                        if f.get("name") == name or f.get("remark") == name or f.get("nickname") == name or f.get("wxid") == name:
                            found_wxid = f.get("wxid")
                            break
        except Exception as e:
            logger.error(f"[ContactsCache] 实时同步联系人数据库失败: {e}")
            
        return found_wxid

    def find_name_with_db_sync(self, account_id: str, wxid: str, is_group: bool = False) -> str:
        """根据 wxid 查找昵称或备注，如果在缓存中没找到，则实时同步微信数据库再查一次"""
        if not wxid:
            return ""
        # 标准化 account_id 防止 key 不一致导致解密锁冷却失效
        if not account_id or account_id in ("main", "default"):
            try:
                from src.crm.account_data import get_active_account
                active_id = get_active_account()
                if active_id and active_id != "default":
                    account_id = active_id
            except Exception:
                pass

        all_friends = self.get_friends(account_id) or []
        all_groups = self.get_groups(account_id) or []
        
        found_name = None
        if is_group or "@chatroom" in wxid:
            for g in all_groups:
                if g.get("wxid") == wxid:
                    found_name = g.get("name")
                    break
        else:
            for f in all_friends:
                if f.get("wxid") == wxid or f.get("alias") == wxid:
                    found_name = f.get("remark") or f.get("name") or f.get("nickname")
                    break
                    
        if found_name:
            return found_name
            
        # 尝试触发解密并实时同步通讯录
        try:
            from src.wechat_4x.db_contact_syncer import sync_contacts_from_db
            from src.wechat_4x.db_match_helper import auto_detect_db_path
            from src.wechat_4x.wcdb_key_extractor import get_wcdb_key_extractor
            import os
            
            from src.utils.wechat_key_store import get_persisted_wechat_key
            hex_key = get_persisted_wechat_key(account_id)
            if not hex_key:
                hex_key = os.environ.get("WCDB_HEX_KEY", "") or os.environ.get("WECHAT_4X_KEY_HEX", "")
            if not hex_key:
                hex_key = get_wcdb_key_extractor().get_key(timeout_s=2.0) or ""
            db_path = auto_detect_db_path(hex_key, account_id) or os.environ.get("WCDB_SESSION_DB_PATH", "") or ""
            
            if db_path and hex_key:
                db_storage_dir = os.path.dirname(os.path.dirname(db_path))
                logger.info(f"[ContactsCache] 未命中缓存，开始实时同步 wxid '{wxid}' 的数据库记录...")
                sync_contacts_from_db(db_storage_dir, hex_key, account_id)
                
                # 同步完之后再查一次
                all_friends = self.get_friends(account_id) or []
                all_groups = self.get_groups(account_id) or []
                if is_group or "@chatroom" in wxid:
                    for g in all_groups:
                        if g.get("wxid") == wxid:
                            found_name = g.get("name")
                            break
                else:
                    for f in all_friends:
                        if f.get("wxid") == wxid or f.get("alias") == wxid:
                            found_name = f.get("remark") or f.get("name") or f.get("nickname")
                            break
        except Exception as e:
            logger.error(f"[ContactsCache] 实时同步联系人数据库失败: {e}")
            
        return found_name or ""


