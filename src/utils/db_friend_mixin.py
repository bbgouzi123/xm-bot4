import copy
import logging
from datetime import datetime
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

class FriendMixin:
    """WeChatDBManager 的好友与获客名单管理混入类 (Mixin)"""

    def save_account(self, nickname: str, account_id: str):
        """保存账号信息 → 内存 + 同步后端"""
        return 1

    def save_friends(self, account_id: str, friends_data: List[Dict[str, Any]], is_incremental: bool = False):
        """保存好友列表 → 内存 + 异步推同步后端"""
        from src.utils.contacts_cache import contacts_cache
        now = datetime.now().isoformat()
        friends = []
        for f in friends_data:
            wxid = f.get("wxid", f.get("wx_id", ""))
            name = f.get("name", "")
            if not wxid:
                if not name:
                    continue
                wxid = name  
            
            tags = f.get("tags", [])
            tag_str = ",".join(tags) if isinstance(tags, list) else str(tags)
            friends.append({
                "wxid": wxid,
                "name": name,
                "nickname": f.get("nickname") or f.get("display_name", ""),
                "remark": f.get("remark") or f.get("remark_name", ""),
                "tag": tag_str,
                "is_new": 0,
                "created_at": now,
            })

        if is_incremental:
            existing = contacts_cache.get_friends(account_id)
            final_existing = {id(e): e for e in existing}
            existing_by_wxid = {e.get("wxid"): e for e in existing if e.get("wxid")}
            existing_by_name = {e.get("name"): e for e in existing if e.get("name")}
            
            for nf in friends:
                target = None
                if nf["wxid"] in existing_by_wxid:
                    target = existing_by_wxid[nf["wxid"]]
                elif nf["name"] in existing_by_name:
                    target = existing_by_name[nf["name"]]
                    
                if target:
                    old_wxid = target.get("wxid", "")
                    for k, v in nf.items():
                        if k == "wxid" and old_wxid:  
                            continue
                        if v:
                            target[k] = v
                else:
                    final_existing[id(nf)] = nf
                    existing_by_wxid[nf["wxid"]] = nf
                    existing_by_name[nf["name"]] = nf

            contacts_cache.set_friends(account_id, list(final_existing.values()))
        else:
            contacts_cache.set_friends(account_id, friends)
        return True

    def get_friends(self, account_id: str, tag: str = None) -> List[Dict]:
        """获取好友列表（纯内存）"""
        from src.utils.contacts_cache import contacts_cache
        return contacts_cache.get_friends(account_id, tag=tag)

    def save_groups(self, account_id: str, groups_data: List[Dict[str, Any]]):
        """保存群聊列表 → 内存 + 异步推同步后端"""
        from src.utils.contacts_cache import contacts_cache
        groups = []
        for g in groups_data:
            name = g.get("name", "")
            if name:
                groups.append({
                    "name": name,
                    "tag": g.get("tag", ""),
                    "last_updated": datetime.now().isoformat(),
                })
        contacts_cache.set_groups(account_id, groups)
        return True

    def get_groups(self, account_id: str) -> List[Dict]:
        """获取群聊列表（纯内存）"""
        from src.utils.contacts_cache import contacts_cache
        return contacts_cache.get_groups(account_id)

    # ==================== 获客名单（friend_list）====================

    def add_friend_list(self, friend_list: List[Dict]):
        """批量添加获客名单 → 内存 + 异步推同步后端"""
        for f in friend_list:
            wxid = f.get("wxid", "")
            if wxid:
                exists = any(q["wxid"] == wxid for q in self._friend_queue)
                if not exists:
                    self._friend_queue.append({
                        "wxid": wxid,
                        "remark": f.get("remark", ""),
                        "tags": f.get("tags", ""),
                        "status": "pending",
                        "nickname": "",
                    })
        self._sync_queue_to_cloud()
        return True

    def get_pending_friends(self, limit: int = 1) -> List[Dict]:
        """获取待添加好友"""
        pending = [q for q in self._friend_queue if q.get("status") == "pending"]
        return pending[:limit]

    def bulk_import_leads(self, leads: List[Dict]) -> int:
        """批量导入拓客线索"""
        count = 0
        for lead in leads:
            wxid = lead.get("wxid", "")
            if not wxid:
                continue
            exists = any(q["wxid"] == wxid for q in self._friend_queue)
            if exists:
                for q in self._friend_queue:
                    if q["wxid"] == wxid:
                        if lead.get("tags"):
                            q["tags"] = q.get("tags", "") + "," + lead["tags"] if q.get("tags") else lead["tags"]
                        if lead.get("remark") and not q.get("remark"):
                            q["remark"] = lead["remark"]
                        break
            else:
                self._friend_queue.append({
                    "wxid": wxid,
                    "remark": lead.get("remark", ""),
                    "tags": lead.get("tags", ""),
                    "status": "pending",
                    "nickname": "",
                })
            count += 1
        self._sync_queue_to_cloud()
        return count

    def get_pending_leads(self, limit: int = 100) -> List[Dict]:
        """获取待处理拓客线索"""
        return self.get_pending_friends(limit)

    def update_friend_status(self, wxid: str, status: str, nickname: str = None, error: str = None, account_id: str = None):
        """更新好友添加状态"""
        for q in self._friend_queue:
            if q["wxid"] == wxid:
                q["status"] = status
                if nickname:
                    q["nickname"] = nickname
                if error:
                    q["error"] = error
                break
        self._sync_queue_to_cloud()
        return True

    def _sync_queue_to_cloud(self):
        """先落盘本地全量状态，再异步推送获客名单到同步后端"""
        import threading
        self._persist_snapshot()
        data = list(self._friend_queue)
        def _push():
            try:
                from src.utils.cloud_sync import get_cloud_client
                get_cloud_client().sync_friend_queue(data)
            except Exception as e:
                logger.debug(f"[获客名单] 同步后端推送失败: {e}")
        threading.Thread(target=_push, daemon=True, name="friend-queue-push").start()
