"""
好友与通讯录 UIA 深度同步 API（从原 friend_api.py 拆分以对齐单文件 300 行限额）
"""
import logging
import asyncio
import time
from typing import Optional
from fastapi import APIRouter, Request, BackgroundTasks
from pydantic import BaseModel

from src.utils.instance_manager import InstanceManagerV2
from src.utils.response import ok, err
from src.utils.uia_lock import UIATaskPriority
from src.utils.uia_task_runner import run_uia_task_func, run_uia_with_timeout
from src.utils.stop_signal import stop_signal

logger = logging.getLogger(__name__)
router = APIRouter()


from .friend_sync_helper import _get_driver, _build_resume_snapshot


class SyncRequest(BaseModel):
    category: Optional[str] = None
    force_resync: bool = False


@router.post("/api/friend/sync")
async def sync_friends(req: SyncRequest, background_tasks: BackgroundTasks):
    """驱动 UIA 抓取好友列表 → 内存 + 同步后端"""
    driver = _get_driver()
    if not driver or not driver.is_connected():
        return err(40000, "当前未连接微信")

    manager = InstanceManagerV2.get_instance()
    active_id = manager.get_active_instance_id() or "default"

    # ── 优先走 WCDB 数据库解密同步（秒级获取），仅在不可用时才降级为 UIA 物理点击 ──
    try:
        from src.utils.wechat_key_store import get_persisted_wechat_key
        hex_key = get_persisted_wechat_key(active_id)
        if hex_key and len(hex_key) == 64:
            from src.wechat_4x.db_match_helper import auto_detect_db_path
            expected_wx = active_id if (active_id and not active_id.startswith("account_") and active_id != "default") else None
            db_path = auto_detect_db_path(hex_key, expected_wx)
            import os
            if db_path and os.path.exists(db_path):
                logger.info(f"[手动同步] 成功匹配到可用数据通道及密钥，通过 contact.db 极速同步... (active_id={active_id})")
                from src.wechat_4x.db_contact_syncer import sync_contacts_from_db
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, lambda: sync_contacts_from_db(db_path, hex_key, active_id))
                try:
                    from src.utils.websocket_manager import ws_manager
                    await ws_manager.broadcast({"type": "contact_progress", "data": {"status": "completed", "total": 0, "new": 0}})
                except Exception: pass
                return ok({"type": "db", "message": "通讯录已通过数据库成功同步上线"})
    except Exception as e_db:
        logger.warning(f"[手动同步] 尝试通过数据库解密同步通讯录失败: {e_db}")

    # 主动手动同步时，清空该账号已删除的联系人黑名单，允许重新从微信全量拉回
    try:
        from src.utils.contacts_cache.delete_store import clear_deleted_contacts
        clear_deleted_contacts(active_id)
    except Exception as e:
        logger.warning(f"手动同步清空被删黑名单失败: {e}")

    main_loop = asyncio.get_running_loop()

    async def _do_sync():
        stop_signal.reset()
        try:
            from src.uia.contacts import ContactSync
            from src.utils.websocket_manager import ws_manager
            
            def _sync_cb(ev, data):
                payload = dict(data)
                payload["status"] = ev
                try:
                    from src.uia.input_guard import uia_lock
                    if ev == "progress":
                        uia_lock.update_status(f"正在同步: 已扫描 {data.get('total', 0)} 人 (新增 {data.get('new', 0)} 人)")
                    elif ev == "contact_added":
                        contact = data.get("contact", {})
                        c_name = contact.get("display_name") or contact.get("name") or "未知"
                        uia_lock.update_status(f"正在同步: {c_name} (新增 {data.get('new', 0)} 人 / 共 {data.get('total', 0)} 人)")
                    elif ev == "resumed":
                        uia_lock.update_status(f"正在续跑同步: 已同步 {data.get('total', 0)} 人")
                    elif ev == "completed":
                        uia_lock.update_status(f"同步完成 (共同步 {data.get('total', 0)} 人)")
                except Exception as ex:
                    logger.debug(f"[Sync] 物理锁遮罩状态推送异常: {ex}")

                asyncio.run_coroutine_threadsafe(
                    ws_manager.broadcast({"type": "contact_progress", "data": payload}), main_loop
                )

            syncer = ContactSync(driver)

            def _run_sync_job():
                syncer.sync_all(
                    target_category=req.category,
                    callback=_sync_cb,
                    already_locked=True,
                )
                # 同时也抓下标签页
                syncer.sync_tags(already_locked=True)

            await run_uia_with_timeout(
                run_uia_task_func,
                190.0,
                _run_sync_job,
                task_name=f"通讯录全量同步API#{req.category or '全部'}",
                priority=UIATaskPriority.HIGH,
                timeout=180,
                pause_background_tasks=True,
                use_physical_lock=True,
            )
        except Exception as e:
            logger.error(f"后台深度同步失败: {e}")
        finally:
            try:
                from src.utils.websocket_manager import ws_manager
                asyncio.run_coroutine_threadsafe(
                    ws_manager.broadcast({
                        "type": "contact_progress",
                        "data": {
                            "status": "completed",
                            "total": 0,
                            "new": 0
                        }
                    }), main_loop
                )
            except Exception:
                pass

    background_tasks.add_task(_do_sync)
    return ok({
        "message": "已成功下发后台遍历抓取指令",
        "resume": _build_resume_snapshot(req.category, details=False),
    })



@router.post("/api/friend/sync_details")
async def sync_details(req: SyncRequest, background_tasks: BackgroundTasks):
    """驱动 UIA 逐个抓取联系人详情及头像 → 传图到同步后端并更新库"""
    driver = _get_driver()
    if not driver or not driver.is_connected():
        return err(40000, "当前未连接微信")

    main_loop = asyncio.get_running_loop()
    from src.api.friend_sync_helper import run_sync_details_in_background
    background_tasks.add_task(run_sync_details_in_background, req.category, req.force_resync, main_loop)
    return ok({
        "message": "详情抓取指令下发成功",
        "resume": _build_resume_snapshot(req.category, details=True),
    })


@router.post("/api/friend/sync/pause")
async def sync_friends_pause():
    """停止通讯录全量同步任务"""
    from src.uia.contacts import request_contact_sync_pause
    request_contact_sync_pause()
    logger.info("通讯录同步暂停请求已接收（网页端）")
    return ok({"message": "已请求暂停通讯录同步"})


@router.post("/api/friend/sync_details/pause")
async def sync_details_pause():
    """详情同步暂停"""
    from src.uia.contacts import request_contact_sync_pause
    request_contact_sync_pause()
    logger.info("详情同步暂停请求已接收（网页端）")
    return ok({"message": "已请求暂停详情同步"})


class SyncSingleRequest(BaseModel):
    name: str
    category: Optional[str] = None


@router.post("/api/friend/sync_single")
async def sync_single_contact(req: SyncSingleRequest, background_tasks: BackgroundTasks):
    """驱动 UIA 同步单个联系人的头像及详情"""
    driver = _get_driver()
    if not driver or not driver.is_connected():
        return err(40000, "当前未连接微信")

    main_loop = asyncio.get_running_loop()
    from src.api.friend_sync_helper import run_sync_single_in_background
    background_tasks.add_task(run_sync_single_in_background, req.name, req.category, main_loop)
    return ok({"message": f"已下发「{req.name}」同步指令"})

