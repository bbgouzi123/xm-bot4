import logging
from typing import Any, Dict, List, Optional
from .base import _scope_bot_wxid

logger = logging.getLogger(__name__)


class CloudSyncUserExtraMixin:
    """同步服务用户数据额外 Mixin (用于保持单文件行数在 300 行以内)"""

    def sync_follow_tasks(self, tasks: List[dict]) -> bool:
        """批量同步自动跟单任务到同步后端"""
        import uuid
        cloud_tasks = []
        bot_wxid = _scope_bot_wxid() or "default"
        for t in tasks:
            # 去除 targets 重复项以防同步上传冗余记录
            unique_targets = list(dict.fromkeys(t.get("targets", [])))
            for wxid in unique_targets:
                cloud_tasks.append({
                    "account_id": bot_wxid,
                    "friend_wxid": wxid,
                    "friend_name": "",
                    "agent_id": t.get("agent_id", ""),
                    "ai_service_type": "coze" if t.get("use_ai") else "fallback",
                    "follow_scenario": t.get("follow_scenario", ""),
                    "follow_frequency": t.get("follow_frequency", "daily"),
                    "follow_days": int(t.get("follow_days", 7)),
                    "max_executions": int(t.get("max_daily", 50)),
                    "time_range_start": t.get("time_range_start", "09:00"),
                    "time_range_end": t.get("time_range_end", "20:00")
                })
        if not cloud_tasks:
            return True
        CHUNK_SIZE = 100
        for i in range(0, len(cloud_tasks), CHUNK_SIZE):
            chunk = cloud_tasks[i:i + CHUNK_SIZE]
            result = self._post(
                "/api/v1/follow-tasks",
                {"tasks": chunk},
                need_auth=True,
            )
            if result is None:
                return False
        return True

    def pull_follow_tasks(self) -> Optional[List[dict]]:
        """从同步后端拉取跟单任务列表并还原为本地格式"""
        import uuid
        from datetime import datetime
        res = self._get("/api/v1/follow-tasks", need_auth=True)
        if not res:
            return None
        
        groups = {}
        for row in res:
            status = row.get("task_status", "active")
            if status == "deleted":
                continue
            key = (
                row.get("follow_scenario", ""),
                row.get("follow_days", 7),
                row.get("follow_frequency", "daily"),
                row.get("time_range_start", "09:00"),
                row.get("time_range_end", "20:00"),
                row.get("agent_id", ""),
                row.get("ai_service_type", "")
            )
            if key not in groups:
                groups[key] = {
                    "task_id": f"sdr_{uuid.uuid4().hex[:6]}",
                    "targets": [],
                    "follow_days": row.get("follow_days", 7),
                    "follow_frequency": row.get("follow_frequency", "daily"),
                    "time_range_start": row.get("time_range_start", "09:00"),
                    "time_range_end": row.get("time_range_end", "20:00"),
                    "follow_scenario": row.get("follow_scenario", ""),
                    "use_ai": row.get("ai_service_type") == "coze",
                    "fallback_text": "",
                    "max_daily": row.get("max_executions", 50),
                    "status": "active" if status in ["active", "running"] else "stopped",
                    "created_at": row.get("created_at", datetime.now().isoformat())
                }
            # 还原时防止重复 append 形成队列冗余
            wxid = row.get("friend_wxid", "")
            if wxid and wxid not in groups[key]["targets"]:
                groups[key]["targets"].append(wxid)
        
        return list(groups.values())

    def sync_group_add_friend_history(self, history: List[dict]) -> bool:
        """批量同步本地群加好友历史记录到云服务器数据库"""
        result = self._post("/api/v1/group-friend-add-history", {"history": history}, need_auth=True)
        return result is not None

    def pull_group_add_friend_history(self) -> Optional[List[dict]]:
        """从云服务器数据库拉取群加好友历史记录"""
        return self._get("/api/v1/group-friend-add-history", need_auth=True)

    def create_import_batch(self, source_type: str, source_label: str, total_count: int, session_id: str, data_snapshot: Optional[List[dict]] = None) -> bool:
        """同步导入批次到同步后端"""
        params = {
            "bot_wxid": _scope_bot_wxid(),
            "source_type": source_type,
            "source_label": source_label,
            "total_count": total_count,
            "session_id": session_id,
            "data_snapshot": data_snapshot if data_snapshot is not None else []
        }
        result = self._post("/api/v1/import-batches", params, need_auth=True)
        return result is not None

    def _restore_user_data(self):
        """从同步后端恢复用户私有数据到本地内存"""
        if not self.jwt_token:
            logger.debug("[同步服务] 无 JWT token，跳过私有数据恢复")
            return

        try:
            settings = self.pull_settings()
            if not settings:
                logger.debug("[同步服务] 同步后端无用户设置数据")
                return

            from src.utils.config_cache import config_cache
            restored = 0
            for item in settings:
                key = item.get("setting_key", "")
                val = item.get("setting_val", {})
                if not key or not val:
                    continue

                existing = config_cache.get(key)
                if existing is not None:
                    continue

                config_cache.set(key, val, sync_cloud=False)
                restored += 1

            if restored > 0:
                logger.info(f"[同步服务] 🔄 从同步后端恢复 {restored} 项用户配置到内存（换电脑模式）")
            else:
                logger.debug("[同步服务] 本地数据完整，无需恢复")

        except Exception as e:
            logger.warning(f"[同步服务] 恢复用户数据异常: {e}")
