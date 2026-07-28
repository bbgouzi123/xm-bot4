"""
任务调度执行器 API (Phase 10-B)
这是为了替代原先 xm-bot4 中极其复杂的基于 RabbitMQ/Celery/或复杂内置 Scheduler 的多层 Adapters。
我们采用轻量级的 Asyncio Task + UIA run_in_executor 模式，直接驱动 UIA 进行物理机群发操作，
并通过 WebSockets 即时把进度甩给大屏面板。
"""
from fastapi import APIRouter, Request
import asyncio
import logging
import uuid
import random
from datetime import datetime
from typing import List, Dict

from src.utils.db_manager import WeChatDBManager
from src.utils.websocket_manager import ws_manager
from src.utils.response import ok, err, ok_msg

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/task", tags=["task"])

_driver = None

def init(driver):
    global _driver
    _driver = driver
    
    try:
        from src.api import auto_follow_api
        auto_follow_api.init(driver, _active_tasks)
    except Exception as e:
        logger.error(f"[task_api] 初始化 auto_follow_api 共享变量失败: {e}")
    
    # 维度四：开机自动拉起跟单中枢守护与持久化群发自愈
    try:
        from src.task.auto_follow_daemon import init_driver, ensure_daemon_started
        init_driver(driver)
        ensure_daemon_started()
        logger.info("[AutoFollowSDR] 系统启动成功，已自动拉起跟单中枢守护进程")
    except Exception as e:
        logger.error(f"[AutoFollowSDR] 启动跟单中枢守护异常: {e}")

    # 开机自动拉起业务待办承诺任务池 Worker
    try:
        from src.task.promise_task_worker import init_driver as init_promise_driver, start_promise_worker
        init_promise_driver(driver)
        # 异步启动后台 Worker 任务，防止子线程无事件循环
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(start_promise_worker())
        except RuntimeError:
            import app.state as app_state
            main_loop = getattr(app_state, 'main_loop', None)
            if main_loop:
                asyncio.run_coroutine_threadsafe(start_promise_worker(), main_loop)
            else:
                logger.error("[PromiseWorker] 找不到 main_loop 事件循环，无法启动 Worker")
        logger.info("[PromiseWorker] 系统启动成功，已自动拉起业务承诺待办任务池 Worker")
    except Exception as e:
        logger.error(f"[PromiseWorker] 启动业务承诺待办任务池 Worker 异常: {e}")

    # 自动拉起微信操作统一调度器
    try:
        from src.task.wechat_operation_scheduler import get_wechat_scheduler
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(get_wechat_scheduler())
        except RuntimeError:
            import app.state as app_state
            main_loop = getattr(app_state, 'main_loop', None)
            if main_loop:
                asyncio.run_coroutine_threadsafe(get_wechat_scheduler(), main_loop)
            else:
                logger.error("[WeChatUnifiedScheduler] 找不到 main_loop 事件循环，无法启动调度器")
        logger.info("[WeChatUnifiedScheduler] 微信操作统一调度器启动成功")
    except Exception as e:
        logger.error(f"[WeChatUnifiedScheduler] 启动微信操作统一调度器异常: {e}")
        
    try:
        from src.task.mass_sending_core import MassSendingCore
        mass_engine = MassSendingCore(driver)
        mass_engine.resume_all_pending_jobs()
    except Exception as e:
        logger.error(f"[MassSendingCore] 系统启动自检恢复群发任务异常: {e}")

# 全局任务状态锁和进度缓存 (轻量级内存管理)
_active_tasks = {}

from src.utils.task_helpers import resolve_audience_by_tags


async def _run_uia(func, *args):
    """在专用 UIA 单线程池中执行 UIA 操作（COM 线程安全）"""
    from src.utils.uia_task_runner import run_in_uia_thread
    return await run_in_uia_thread(func, *args)

@router.post("/mass-send")
async def start_mass_send(request: Request):
    """新建并下发一个群发大盘任务"""
    if not _driver or not _driver.is_connected():
        return err(40000, "物理控制机未连接微信")

    from src.utils.license_validator import LicenseValidator
    features = LicenseValidator.check_features()
    if not features.get("mass_messaging", False):
        return err(40301, "当前版本不支持智能群发，请升级套餐")

    data = await request.json()
    text = data.get("text", "")
    targets_input = data.get("targets", [])
    target_mode = data.get("target_mode", "tag")
    files = data.get("files", [])
    script_group_id = data.get("script_group_id", "")
    
    media_urls = data.get("media_urls", "")
    if not media_urls and files:
        if isinstance(files, list):
            media_urls = ",".join(str(f) for f in files if f)
        elif isinstance(files, str):
            media_urls = files

    schedule_time = data.get("schedule_time", "")
    
    if not targets_input:
        return err(40000, "缺乏必要的触达对象")
        
    if not text and not media_urls and not script_group_id:
        return err(40000, "缺乏必要的触达文案或多媒体文件/话术组")

    try:
        from src.task.mass_sending_core import MassSendingCore
        mass_engine = MassSendingCore(_driver)
        # 维度四：提交持久化群发队列作业，实现状态断点恢复并传入话术组 ID 与目标类型
        job_ids = mass_engine.submit_task(
            targets_input, 
            "text", 
            text, 
            media_urls=media_urls, 
            schedule_time=schedule_time,
            script_group_id=script_group_id or None,
            target_mode=target_mode
        )
        if job_ids:
            job_id = job_ids[0]
            db = WeChatDBManager()
            queues = db.get_mass_send_queues(job_id)
            _active_tasks[job_id] = {"status": "running", "total": len(queues), "current": 0, "errors": 0, "runs_detected": 0}
            msg = f"已成功提交持久化群发任务，解析出 {len(targets_input)} 个受众标签/人群"
            if schedule_time:
                msg = f"已成功定时提交群发任务，将于 {schedule_time} 触发，解析出 {len(targets_input)} 个受众人群"
                _active_tasks[job_id]["status"] = "paused"
            return ok({"task_id": job_id, "message": msg})
    except Exception as e:
        logger.error(f"[task_api] 提交群发任务异常: {e}")
        return err(50000, f"提交群发任务失败: {e}")
    return err(40000, "提交群发任务失败")


@router.get("/status/{task_id}")
async def get_task_status(task_id: str):
    """查询指定任务状态"""
    info = _active_tasks.get(task_id)
    if not info:
        try:
            db = WeChatDBManager()
            if task_id.startswith("mass_"):
                jobs = db.get_mass_send_jobs()
                job = next((j for j in jobs if j.get("id") == task_id), None)
                if job:
                    db_status = job.get("status", "pending")
                    status = {"pending": "running", "processing": "running", "paused": "paused", "completed": "completed", "cancelled": "completed"}.get(db_status, "running")
                    queues = db.get_mass_send_queues(task_id)
                    current = len([x for x in queues if x.get("status") in ("sent", "completed", "success")])
                    errors = len([x for x in queues if x.get("status") == "failed"])
                    info = {"status": status, "total": len(queues), "current": current, "errors": errors, "runs_detected": 0}
                    _active_tasks[task_id] = info
            else:
                task = db.get_auto_follow_task(task_id)
                if task:
                    db_status = task.get("status", "active")
                    status = {"active": "running", "paused": "paused", "stopped": "completed", "completed": "completed", "cancelled": "completed"}.get(db_status, "running")
                    targets = task.get("targets", [])
                    follow_days = int(task.get("follow_days", 7))
                    exec_state = task.get("execution_state") or {}
                    current = 0
                    if isinstance(exec_state, dict):
                        current = sum(s.get("follow_count", 0) for s in exec_state.values() if isinstance(s, dict))
                    info = {"status": status, "total": len(targets) * follow_days, "current": current, "errors": 0, "runs_detected": 0}
                    _active_tasks[task_id] = info
        except Exception as e:
            logger.error(f"[task_api] 从数据库恢复任务 {task_id} 状态异常: {e}")
    return ok({"data": info}) if info else err(40000, "任务飞地中不存在此指针")


@router.post("/pause/{task_id}")
async def pause_task(task_id: str):
    """暂停任务"""
    if task_id.startswith("sdr_"):
        db = WeChatDBManager()
        db.update_auto_follow_task(task_id, {"status": "paused"})
    elif task_id.startswith("mass_"):
        from src.task.mass_sending_helper import pause_task as helper_pause
        db = WeChatDBManager()
        helper_pause(db, task_id)
    
    if task_id in _active_tasks:
        _active_tasks[task_id]["status"] = "paused"
        
    loop = asyncio.get_event_loop()
    loop.create_task(ws_manager.broadcast_json({
        "type": "task_progress",
        "task_id": task_id,
        "detail": "⏸️ 任务已由总控台安全挂起"
    }))
    return ok({"message": "任务已安全挂起"})


@router.post("/resume/{task_id}")
async def resume_task(task_id: str):
    """恢复任务"""
    if task_id.startswith("sdr_"):
        db = WeChatDBManager()
        db.update_auto_follow_task(task_id, {"status": "active"})
    elif task_id.startswith("mass_"):
        from src.task.mass_sending_helper import resume_task as helper_resume
        db = WeChatDBManager()
        helper_resume(db, task_id)
    
    if task_id in _active_tasks:
        _active_tasks[task_id]["status"] = "running"
        
    loop = asyncio.get_event_loop()
    loop.create_task(ws_manager.broadcast_json({
        "type": "task_progress",
        "task_id": task_id,
        "detail": "▶️ 任务已恢复，继续执行"
    }))
    return ok({"message": "任务已继续执行"})


@router.delete("/cancel/{task_id}")
async def cancel_task(task_id: str):
    """人工紧急熔断某个任务"""
    if task_id.startswith("sdr_"):
        db = WeChatDBManager()
        db.update_auto_follow_task(task_id, {"status": "stopped", "stopped_at": datetime.now().isoformat()})
    elif task_id.startswith("mass_"):
        from src.task.mass_sending_helper import cancel_task as helper_cancel
        db = WeChatDBManager()
        helper_cancel(db, task_id)

    if task_id in _active_tasks:
        _active_tasks[task_id]["status"] = "cancelled"
        
    loop = asyncio.get_event_loop()
    loop.create_task(ws_manager.broadcast_json({
        "type": "sys_alert",
        "level": "warning",
        "message": f"操作总调度台下达了熔断指令，任务 {task_id} 已暴力终止"
    }))
    return ok({"message": "已熔断下发管线"})


@router.get("/promises")
async def get_promise_tasks():
    """获取承诺业务待办任务池中的所有任务"""
    db = WeChatDBManager()
    return ok(db.get_promise_tasks())


@router.post("/promises/{task_id}/retry")
async def retry_promise_task(task_id: str):
    """重试执行失败的承诺任务"""
    db = WeChatDBManager()
    success = db.update_promise_task(task_id, {
        "status": "pending",
        "retry_count": 0,
        "error_message": ""
    })
    if success:
        return ok({"message": "任务已重置，等待后台 Worker 重新消费执行"})
    return err(40000, "未找到该待办任务")


@router.delete("/promises/{task_id}")
async def delete_promise_task(task_id: str):
    """删除指定的承诺任务"""
    db = WeChatDBManager()
    success = db.delete_promise_task(task_id)
    if success:
        return ok({"message": "任务删除成功"})
    return err(40000, "未找到该待办任务")


@router.get("/wechat_scheduler/status")
async def get_wechat_scheduler_status():
    """获取微信操作统一调度器的当前队列和状态"""
    try:
        from src.task.wechat_operation_scheduler import get_wechat_scheduler
        scheduler = await get_wechat_scheduler()
        
        async with scheduler._queue_lock:
            queue_snapshot = []
            for weight, act in scheduler._queue:
                queue_snapshot.append({
                    "action_id": act.action_id,
                    "action_type": act.action_type,
                    "priority": act.priority.name,
                    "target_wxid": act.target_wxid,
                    "created_at": act.created_at.isoformat()
                })
        
        est_wait_time = len(queue_snapshot) * 6
        
        return ok({
            "running": scheduler._running,
            "queue_length": len(queue_snapshot),
            "estimated_wait_seconds": est_wait_time,
            "queue": queue_snapshot
        })
    except Exception as e:
        logger.error(f"[task_api] 获取调度器状态异常: {e}")
        return err(50000, f"获取调度器状态异常: {e}")



