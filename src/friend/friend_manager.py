"""
加好友任务管理器 — 移植自 xm-bot4 AddFriendAdapter / AddFriendManager
"""
import time
import logging
import threading
from datetime import datetime
from typing import Optional, Dict, Any, List

from . import friend_list_store

logger = logging.getLogger(__name__)


def sync_validate_mobile(mobile: str) -> bool:
    """同步校验手机号（适配器外部校验接口集成）"""
    try:
        from src.api.customer_api.adapter_factory import CustomerAdapterFactory
        mobile_adapter = CustomerAdapterFactory.get_mobile_adapter()
        if not mobile_adapter:
            return True
        import asyncio
        from app.state import main_loop
        if main_loop and main_loop.is_running():
            future = asyncio.run_coroutine_threadsafe(mobile_adapter.validate(mobile), main_loop)
            return future.result(timeout=10)
        else:
            return asyncio.run(mobile_adapter.validate(mobile))
    except Exception as e:
        logger.error(f"[ValidateMobile] 同步校验手机号失败，默认放行: {e}")
        return True


class FriendManager:
    """加好友任务管理器（对标 V2 AddFriendAdapter）"""

    def __init__(self, driver=None):
        self._driver = driver
        self._running = False
        self._paused = False
        self._executing = False

    # ==================== 任务生命周期 ====================

    def start(self) -> bool:
        if self._running and not self._paused:
            logger.info("FriendManager 已运行，忽略重复启动")
            return True
        self._running = True
        self._paused = False
        logger.info("FriendManager 启动成功")
        return True

    def stop(self) -> bool:
        self._running = False
        self._paused = False
        self._executing = False
        logger.info("FriendManager 停止成功")
        return True

    def pause(self) -> bool:
        self._paused = True
        return True

    def resume(self) -> bool:
        self._paused = False
        return True

    def is_running(self) -> bool:
        return self._running and not self._paused

    def get_status(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "paused": self._paused,
            "executing": self._executing,
        }

    # ==================== 单次添加好友 ====================

    def add_single_friend(
        self,
        wxid: str,
        remark: str = "",
        tags: str = "",
        verify_message: str = "",
    ) -> Dict[str, Any]:
        if not self._driver or not self._driver.is_connected():
            return {"success": False, "message": "微信未连接"}

        if not sync_validate_mobile(wxid):
            logger.warning(f"[ValidateMobile] 手机号/微信号 {wxid} 校验未通过外部合规检查，被拦截！")
            return {"success": False, "message": "被外部黑白名单接口拦截"}

        try:
            from src.uia.add_friend import AddFriendEngine

            engine = AddFriendEngine(self._driver)
            result = engine.add_new_friend(
                wxid=wxid,
                remark=remark or None,
                tags=tags or None,
                verify_message=verify_message or None,
            )

            log_entry = {
                "action": "add_friend",
                "wxid": wxid,
                "remark": remark,
                "tags": tags,
                "verify_message": verify_message,
                "result": result,
                "timestamp": datetime.now().isoformat(),
            }
            friend_list_store.save_log(log_entry)
            self._async_report_log_to_cloud(log_entry)

            if result.get("success"):
                try:
                    from src.utils.counter import DailyCounter
                    counter = DailyCounter()
                    counter.increment_count("main")
                except Exception as e:
                    logger.error(f"更新每日计数失败: {e}")

            return result

        except Exception as e:
            logger.error(f"添加好友失败: {e}")
            return {"success": False, "message": str(e)}

    # ==================== 批量添加好友 ====================

    def execute_batch(
        self,
        max_friends_per_day: int = 20,
        max_process_per_time: int = 3,
        verify_message: str = "",
    ) -> Dict[str, Any]:
        if self._executing:
            return {"success": False, "message": "正在执行中，请稍后"}
        if not self._driver or not self._driver.is_connected():
            return {"success": False, "message": "微信未连接"}

        self._executing = True
        result = {"success": True, "attempted": 0, "succeeded": 0, "failed": 0, "details": []}

        try:
            if friend_list_store.is_rest_time_store("自动加好友"):
                return {"success": False, "message": "当前时间在休息时间段内"}

            from src.utils.counter import DailyCounter
            counter = DailyCounter()
            today_count = counter.get_today_count("main")
            if today_count >= max_friends_per_day:
                return {"success": False, "message": "今日添加好友数量已达上限"}

            pending = friend_list_store.get_pending_friends(limit=max_process_per_time)
            if not pending:
                return {"success": True, "message": "没有待添加的好友", "attempted": 0}

            from src.uia.add_friend import AddFriendEngine
            engine = AddFriendEngine(self._driver)

            for friend in pending:
                if today_count >= max_friends_per_day:
                    logger.info("今日添加好友数量已达上限，停止执行")
                    break

                wxid = friend.get("wxid", "")
                if not wxid:
                    continue

                if not sync_validate_mobile(wxid):
                    logger.warning(f"[ValidateMobile] 批量加好友校验未通过，自动拦截: {wxid}")
                    result["failed"] += 1
                    friend_list_store.update_friend_status(
                        wxid, "blocked", friend.get("nickname", ""), "被外部校验拦截"
                    )
                    continue

                result["attempted"] += 1
                add_result = engine.add_new_friend(
                    wxid=wxid,
                    remark=friend.get("remark") or None,
                    tags=friend.get("tags") or None,
                    verify_message=verify_message or None,
                )

                if add_result.get("success"):
                    result["succeeded"] += 1
                    today_count = counter.increment_count("main")
                    friend_list_store.update_friend_status(wxid, "added", add_result.get("nickname", ""))
                else:
                    result["failed"] += 1
                    status = add_result.get("status", "failed")
                    friend_list_store.update_friend_status(
                        wxid, status, add_result.get("nickname", ""), add_result.get("message", "")
                    )

                result["details"].append(add_result)
                log_entry = {
                    "action": "batch_add_friend",
                    "wxid": wxid,
                    "result": add_result,
                    "timestamp": datetime.now().isoformat(),
                }
                friend_list_store.save_log(log_entry)
                self._async_report_log_to_cloud(log_entry)

                time.sleep(friend_list_store.random_interval(3, 8))

        except Exception as e:
            logger.error(f"批量添加好友异常: {e}")
            result["success"] = False
            result["message"] = str(e)
        finally:
            self._executing = False

        return result

    # ==================== 委托外部名单管理 ====================

    def import_friends(self, friends: List[Dict]) -> Dict[str, Any]:
        return friend_list_store.import_friends_store(friends)

    def get_friend_list(self, status: str = None, limit: int = 100) -> List[Dict]:
        return friend_list_store.get_friend_list_store(status, limit)

    def delete_friend(self, wxid: str) -> bool:
        return friend_list_store.delete_friend_store(wxid)

    def get_add_logs(self, limit: int = 50) -> List[Dict]:
        return friend_list_store.get_add_logs_store(limit)

    def _async_report_log_to_cloud(self, entry: Dict[str, Any]):
        def _push():
            try:
                from src.utils.cloud_sync import get_cloud_client
                get_cloud_client().report_event(
                    "add_friend_task_log",
                    {
                        "action": entry.get("action", ""),
                        "wxid": entry.get("wxid", ""),
                        "result": entry.get("result", {}),
                        "remark": entry.get("remark", ""),
                        "tags": entry.get("tags", ""),
                        "created_at": entry.get("timestamp", datetime.now().isoformat()),
                    },
                )
            except Exception:
                pass

        threading.Thread(target=_push, daemon=True, name="friend-log-cloud").start()
