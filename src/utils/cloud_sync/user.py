import logging
from typing import Any, Dict, List, Optional
from .base import _scope_bot_wxid
from .user_extra import CloudSyncUserExtraMixin

logger = logging.getLogger(__name__)


class CloudSyncUserMixin(CloudSyncUserExtraMixin):
    """同步服务用户数据 Mixin (Category B)"""

    def sync_settings(self, settings: Dict[str, Any]) -> bool:
        """批量同步用户设置到同步后端"""
        result = self._post("/api/v1/settings", {"settings": settings}, need_auth=True)
        return result is not None

    def pull_settings(self) -> Optional[List[dict]]:
        """从同步后端拉取用户设置"""
        return self._get("/api/v1/settings", need_auth=True)

    def save_setting(self, key: str, value: Any) -> bool:
        """保存单个设置到同步后端"""
        result = self._put(f"/api/v1/settings/{key}", {"value": value}, need_auth=True)
        return result is not None

    def sync_crm_profiles(self, profiles: List[dict]) -> bool:
        """批量同步 CRM 画像"""
        result = self._post(
            "/api/v1/crm/profiles",
            {"bot_wxid": _scope_bot_wxid(), "profiles": profiles},
            need_auth=True,
        )
        if result:
            logger.info(f"[同步服务] CRM 画像同步: {result.get('synced', 0)} 条")
        return result is not None

    def sync_contacts(self, contacts: List[dict]) -> bool:
        """批量同步通讯录"""
        mapped = []
        for c in contacts:
            mapped.append({
                "contact_type": c.get("category", "联系人"),
                "wxid": c.get("wxid", ""),
                "name": c.get("name", ""),
                "remark": c.get("remark", ""),
                "tags": [c.get("tag")] if c.get("tag") else [],
                "is_new": False
            })
        if not mapped:
            return True
        CHUNK_SIZE = 200
        for i in range(0, len(mapped), CHUNK_SIZE):
            chunk = mapped[i:i + CHUNK_SIZE]
            result = self._post(
                "/api/v1/contacts",
                {"bot_wxid": _scope_bot_wxid(), "contacts": chunk},
                need_auth=True,
            )
            if result is None:
                return False
        return True

    def sync_moment_interactions(self, interactions: List[dict]) -> bool:
        """批量同步朋友圈互动日志到同步后端"""
        if not interactions:
            return True
        result = self._post("/api/v1/moment-interactions", {"interactions": interactions}, need_auth=True)
        if result:
            logger.info(f"[同步服务] 互动日志同步: {result.get('synced', 0)} 条")
        return result is not None

    def sync_add_friend_logs(self, logs: List[dict]) -> bool:
        """批量同步加好友操作日志到同步后端"""
        if not logs:
            return True
        result = self._post("/api/v1/add-friend-logs", {"logs": logs}, need_auth=True)
        if result:
            logger.info(f"[同步服务] 加好友日志同步: {result.get('synced', 0)} 条")
        return result is not None

    def sync_friend_queue(self, items: List[dict] = None) -> bool:
        """同步获客名单到同步后端"""
        if items is None:
            try:
                from src.friend.friend_queue import get_queue_list
                result = get_queue_list(page=1, page_size=200)
                items = result.get("items", [])
            except Exception as e:
                logger.warning(f"[同步服务] 读取本地队列失败: {e}")
                return False
        if not items:
            return True
        result = self._post(
            "/api/v1/friend-queue",
            {"bot_wxid": _scope_bot_wxid(), "items": items},
            need_auth=True,
        )
        if result:
            logger.info(f"[同步服务] 获客名单同步: {len(items)} 条")
        return result is not None

    def sync_moment_schedules(self, schedules: List[dict]) -> bool:
        """批量同步朋友圈排期"""
        import json
        from src.crm.moment_planner_service.state import _parse_schedule_datetime
        mapped_schedules = []
        for s in schedules:
            item = dict(s)
            t_str = item.get("scheduled_time")
            if t_str:
                dt = _parse_schedule_datetime(t_str)
                if dt:
                    item["scheduled_time"] = dt.astimezone().isoformat()
            media = item.get("media_urls", [])
            if isinstance(media, str):
                try:
                    media = json.loads(media)
                except Exception:
                    if media.startswith('[') or media.startswith('{'):
                        media = []
                    else:
                        media = [media] if media else []
            if not isinstance(media, list):
                media = []
            item["media_urls"] = media
            mapped_schedules.append(item)

        result = self._post(
            "/api/v1/moments/schedules",
            {"bot_wxid": _scope_bot_wxid(), "schedules": mapped_schedules},
            need_auth=True,
        )
        return result is not None

    def sync_groups(self, groups: List[dict]) -> bool:
        """批量同步群聊列表"""
        mapped = []
        for g in groups:
            mapped.append({
                "contact_type": g.get("category", "群聊"),
                "wxid": g.get("wxid", ""),
                "name": g.get("name", ""),
                "remark": g.get("remark", ""),
                "tags": [g.get("tag")] if g.get("tag") else [],
                "is_new": False
            })
        if not mapped:
            return True
        CHUNK_SIZE = 200
        for i in range(0, len(mapped), CHUNK_SIZE):
            chunk = mapped[i:i + CHUNK_SIZE]
            result = self._post(
                "/api/v1/contacts",
                {"bot_wxid": _scope_bot_wxid(), "contacts": chunk},
                need_auth=True,
            )
            if result is None:
                return False
        return True

    def sync_group_members(self, members: List[dict]) -> bool:
        """批量同步群成员"""
        if not members:
            return True
        CHUNK_SIZE = 200
        for i in range(0, len(members), CHUNK_SIZE):
            chunk = members[i:i + CHUNK_SIZE]
            result = self._post(
                "/api/v1/group-members",
                {"bot_wxid": _scope_bot_wxid(), "members": chunk},
                need_auth=True,
            )
            if result is None:
                return False
        return True

    def sync_contact_tags(self, tags: List[dict]) -> bool:
        """批量同步微信标签"""
        result = self._post(
            "/api/v1/contact-tags",
            {"bot_wxid": _scope_bot_wxid(), "tags": tags},
            need_auth=True,
        )
        return result is not None

    def sync_chat_history(self, messages: List[dict]) -> bool:
        """批量同步聊天记录"""
        if not messages:
            return True
        CHUNK_SIZE = 100
        for i in range(0, len(messages), CHUNK_SIZE):
            chunk = messages[i:i + CHUNK_SIZE]
            result = self._post(
                "/api/v1/chat/history",
                {"bot_wxid": _scope_bot_wxid(), "messages": chunk},
                need_auth=True,
            )
            if result is None:
                return False
        return True

    def sync_ai_sessions(self, sessions: List[dict]) -> bool:
        """同步 AI 会话映射"""
        result = self._post(
            "/api/v1/ai/sessions",
            {"bot_wxid": _scope_bot_wxid(), "sessions": sessions},
            need_auth=True,
        )
        return result is not None


