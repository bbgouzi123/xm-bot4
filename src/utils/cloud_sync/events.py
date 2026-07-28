import logging
import uuid
import datetime
from typing import List, Optional

logger = logging.getLogger(__name__)


class CloudSyncEventsMixin:
    """同步服务事件日志 Mixin (Category C)"""

    def report_event(self, event_type: str, event_data: dict,
                     app_version: str = "") -> bool:
        """上报单个事件（异步队列）"""
        from src.crm.account_data import get_active_account
        from src.utils.trace_context import get_trace_id

        if "created_at" not in event_data:
            event_data["created_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        _aid = get_active_account() or "main"
        if not event_data.get("account_id"):
            event_data["account_id"] = _aid

        trace_id = event_data.get("trace_id") or get_trace_id() or f"trc_{uuid.uuid4().hex}"
        event = {
            "event_id": f"evt_{uuid.uuid4().hex}",
            "event_type": event_type,
            "schema_version": "v1",
            "product_id": "xm-bot4",
            "tenant_id": "default",
            "account_id": _aid,
            "trace_id": trace_id,
            "level": event_data.get("level", "info"),
            "app_version": app_version,
            "created_at": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "event_data": event_data,
        }

        with self._queue_lock:
            self._queue.append(event)
            self._save_queue_to_disk()

        # 企业审计影子上报：仅关键操作事件同步到 Boss Dashboard
        _AUDIT_EVENT_TYPES = {
            "friend_request", "moment_post", "mass_send",
            "auto_follow", "ui_bus_command", "chat_log", "group_message"
        }
        if event_type in _AUDIT_EVENT_TYPES and hasattr(self, "report_audit_event"):
            try:
                target = event_data.get("wxid") or event_data.get("phone") or event_data.get("target", "")
                
                # 尽量透传关键详情数据到企业审计日志
                audit_detail = {"trace_id": trace_id}
                if event_type in ("chat_log", "group_message"):
                    audit_detail.update({
                        "message": event_data.get("message", ""),
                        "reply": event_data.get("reply", "")
                    })
                elif event_type == "friend_request":
                    audit_detail.update({
                        "result": event_data.get("result", ""),
                        "error": event_data.get("error", "")
                    })
                elif event_type == "moment_post":
                    audit_detail.update({
                        "content": event_data.get("content", ""),
                        "status": event_data.get("status", ""),
                        "error": event_data.get("error", "")
                    })
                elif event_type == "mass_send":
                    audit_detail.update({
                        "target_count": event_data.get("target_count", 0),
                        "success_count": event_data.get("success_count", 0)
                    })

                self.report_audit_event(
                    action=event_type,
                    target=str(target)[:100],
                    detail=audit_detail,
                )
            except Exception:
                pass  # 审计上报失败不影响主流程

        # 启动异步线程：先立刻将刚才暂存的事件推送到云端，推送成功后再向前端发广播
        def _async_flush_and_broadcast():
            try:
                # 1. 尝试立刻把刚才在队列里的事件推送到同步云端，免去定时器的等待
                if hasattr(self, "_flush_event_queue"):
                    getattr(self, "_flush_event_queue")()
                
                # 2. 推送成功后，再广播给前端重新请求以实现真正的零延迟刷新
                from src.utils.websocket_manager import ws_manager
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                
                payload = {"type": "stats_changed", "data": {"event_type": event_type}}
                if loop.is_running():
                    asyncio.run_coroutine_threadsafe(ws_manager.broadcast(payload), loop)
                else:
                    loop.run_until_complete(ws_manager.broadcast(payload))
                logger.info(f"[事件上报] 成功推送云端并广播 stats_changed: event_type={event_type}")
            except Exception as ex:
                logger.debug(f"[事件上报] 异步推送或广播 stats_changed 异常: {ex}")

        import threading
        threading.Thread(target=_async_flush_and_broadcast, daemon=True, name="async-event-flusher").start()

        return True

    def report_chat_log(self, wxid: str, message: str, reply: str):
        """上报聊天日志"""
        self.report_event("chat_log", {
            "wxid": wxid, "message": message, "reply": reply
        })

    def report_moment_post(self, content: str, status: str, error: str = ""):
        """上报朋友圈发送日志"""
        self.report_event("moment_post", {
            "content": content[:200], "status": status, "error": error
        })

    def report_friend_request(self, phone: str, result: str, error: str = ""):
        """上报好友请求日志"""
        self.report_event("friend_request", {
            "phone": phone, "result": result, "error": error
        })

    def report_mass_send(self, target_count: int, success_count: int):
        """上报群发日志"""
        self.report_event("mass_send", {
            "target_count": target_count, "success_count": success_count
        })

    def report_auto_follow(self, friend_wxid: str, action: str, result: str):
        """上报跟单日志"""
        self.report_event("auto_follow", {
            "friend_wxid": friend_wxid, "action": action, "result": result
        })

    def report_usage(self, account_id: str = "main") -> bool:
        """上报今日各维度操作用量到同步后端"""
        try:
            from src.utils.daily_counter import DailyCounter
            from src.utils.license_validator import LicenseValidator
            import platform

            counter = DailyCounter()
            stats = counter.get_all_stats(account_id)

            features = LicenseValidator.check_features()
            ai_limit = features.get("ai_daily_limit", 30)

            payload = {
                "account_id": account_id,
                "date": datetime.datetime.now().strftime("%Y-%m-%d"),
                "device": platform.node(),
                "degraded": LicenseValidator.is_degraded(),
                "plan_features": {
                    "ai_daily_limit": ai_limit,
                    "auto_chat": features.get("auto_chat", False),
                },
                "dimensions": {},
            }

            for dim, stat in stats.items():
                payload["dimensions"][dim] = {
                    "count": stat["count"],
                    "limit": ai_limit if dim == "auto_reply" and ai_limit > 0 else stat["limit"],
                    "percentage": stat["percentage"],
                }

            result = self._post("/api/v1/usage/report", payload, need_auth=True)
            if result is not None:
                logger.info(f"[同步服务] 用量上报成功: {len(stats)} 个维度")
                return True
            return False
        except Exception as e:
            logger.warning(f"[同步服务] 用量上报异常: {e}")
            return False

    def pull_today_usage(self, account_id: str) -> Optional[dict]:
        """拉取同步后端今日用量"""
        try:
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            path = f"/api/v1/usage/history?since={today}&until={today}"
            res = self._get(path, need_auth=True)
            if not res:
                return None
            for report in res:
                if report.get("account_id") == account_id:
                    logger.info(f"[同步服务] 成功拉取同步后端用量恢复 {account_id}")
                    return report.get("dimensions", {})
            return None
        except Exception as e:
            logger.debug(f"[同步服务] 拉取今日用量失败: {e}")
            return None

    def peek_pending_events(self, event_type: str, account_id: str = "") -> List[dict]:
        """未成功上报的事件队列快照"""
        with self._queue_lock:
            snap: List[dict] = [ev for ev in self._queue]
        if not event_type:
            return snap
        out: List[dict] = []
        for ev in snap:
            if ev.get("event_type") != event_type:
                continue
            if account_id and ev.get("account_id") != account_id:
                continue
            out.append(ev)
        return out
