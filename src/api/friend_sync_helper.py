"""
好友与通讯录同步辅助函数，解耦以确保 friend_sync_api.py 控制在 300 行限额以下
"""
import time
import logging
import asyncio
from typing import Optional

logger = logging.getLogger("FriendSyncHelper")

def _get_driver():
    try:
        from src.api.friend_api import get_driver
        return get_driver()
    except Exception:
        return None


def _get_active_account_for_resume() -> str:
    try:
        from src.crm.account_data import get_active_account
        return get_active_account() or "default"
    except Exception:
        return "default"


def _build_resume_snapshot(category: Optional[str] = None, details: bool = False) -> dict:
    from src.utils.contact_sync_checkpoint import ContactSyncCheckpointStore
    account_id = _get_active_account_for_resume()
    store = ContactSyncCheckpointStore()
    records = store.list_records(account_id)
    target_key = f"{account_id}::{'details::' if details else ''}{category or '__all__'}"
    current = records.get(target_key, {}) if isinstance(records, dict) else {}
    summary = ContactSyncCheckpointStore.summarize(records)
    summary["account_id"] = account_id
    summary["target_key"] = target_key
    summary["target"] = current if isinstance(current, dict) else {}
    summary["updated_at"] = int(time.time())
    return summary


def run_sync_details_in_background(category: Optional[str], force_resync: bool, main_loop):
    """把 sync_details 的 UIA 后台任务移到 helper 解决主 api 行数超标"""
    from src.utils.stop_signal import stop_signal
    from src.utils.websocket_manager import ws_manager
    from src.utils.uia_lock import UIATaskPriority
    from src.utils.uia_task_runner import run_uia_task_func, run_uia_with_timeout

    stop_signal.reset()
    try:
        from src.uia.contacts import ContactSync

        def _sync_cb(ev, data):
            payload = dict(data)
            try:
                from src.uia.input_guard import uia_lock
                if ev == "progress":
                    curr_name = data.get("current_name") or "未知"
                    uia_lock.update_status(f"正在抓取详情: {curr_name} ({data.get('current', 0)}/{data.get('total', 0)})")
                elif ev == "completed":
                    uia_lock.update_status(f"详情抓取完成 (共 {data.get('total', 0)} 人)")
            except Exception as ex:
                logger.debug(f"[SyncDetails] 物理锁遮罩状态推送异常: {ex}")

            if ev == "auto_sync_contacts_completed":
                payload["status"] = "completed"
                asyncio.run_coroutine_threadsafe(
                    ws_manager.broadcast({"type": "contact_progress", "data": payload}), main_loop
                )
                return
                
            payload["status"] = ev
            asyncio.run_coroutine_threadsafe(
                ws_manager.broadcast({"type": "avatar_progress", "data": payload}), main_loop
            )

        driver = _get_driver()
        if not driver:
            return
        syncer = ContactSync(driver)

        def _run_details_job():
            syncer.sync_details(
                target_category=category,
                callback=_sync_cb,
                already_locked=True,
                force_resync=force_resync,
            )

        async def _run():
            await run_uia_with_timeout(
                run_uia_task_func,
                190.0,
                _run_details_job,
                task_name=f"通讯录详情同步API#{category or '全部'}",
                priority=UIATaskPriority.HIGH,
                timeout=180,
                pause_background_tasks=True,
                use_physical_lock=True,
            )
        asyncio.run(_run())
    except Exception as e:
        logger.error(f"后台深度查详情失败: {e}")
        try:
            asyncio.run_coroutine_threadsafe(
                ws_manager.broadcast({
                    "type": "avatar_progress",
                    "data": {"status": "completed", "success": False, "errors": [str(e)], "current": 0, "total": 0},
                }), main_loop
            )
        except Exception: pass
    finally:
        try:
            asyncio.run_coroutine_threadsafe(
                ws_manager.broadcast({
                    "type": "avatar_progress",
                    "data": {"status": "completed", "success": True, "current": 0, "total": 0}
                }), main_loop
            )
        except Exception: pass


def run_sync_single_in_background(name: str, category: Optional[str], main_loop):
    """把 sync_single 的 UIA 后台任务移到 helper 解决主 api 行数超标"""
    from src.utils.stop_signal import stop_signal
    from src.utils.websocket_manager import ws_manager
    from src.utils.uia_lock import UIATaskPriority
    from src.utils.uia_task_runner import run_uia_task_func, run_uia_with_timeout

    stop_signal.reset()
    try:
        from src.uia.contacts import ContactSync

        def _sync_cb(ev, data):
            payload = dict(data)
            payload["status"] = ev
            try:
                from src.uia.input_guard import uia_lock
                if ev == "progress":
                    curr_name = data.get("current_name") or name
                    uia_lock.update_status(f"正在同步单联系人详情: {curr_name}")
                elif ev == "completed":
                    uia_lock.update_status(f"单联系人详情同步完成")
            except Exception as ex:
                logger.debug(f"[SyncSingle] 物理锁遮罩状态推送异常: {ex}")

            asyncio.run_coroutine_threadsafe(
                ws_manager.broadcast({"type": "avatar_progress", "data": payload}), main_loop
            )

        driver = _get_driver()
        if not driver:
            return
        syncer = ContactSync(driver)

        def _run_single_job():
            syncer.sync_details(
                target_category=category,
                callback=_sync_cb,
                already_locked=True,
                force_resync=True,
                single_contact_name=name,
            )

        async def _run():
            await run_uia_with_timeout(
                run_uia_task_func,
                70.0,
                _run_single_job,
                task_name=f"单联系人同步#{name}",
                priority=UIATaskPriority.HIGH,
                timeout=60,
                pause_background_tasks=True,
                use_physical_lock=True,
            )
        asyncio.run(_run())
    except Exception as e:
        logger.error(f"单联系人同步失败: {e}")
        try:
            asyncio.run_coroutine_threadsafe(
                ws_manager.broadcast({
                    "type": "avatar_progress",
                    "data": {"status": "completed", "success": False, "errors": [str(e)], "current": 0, "total": 0},
                }), main_loop
            )
        except Exception: pass
