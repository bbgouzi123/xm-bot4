"""
调度器 API — 多实例并行自动化控制
"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, List
import logging

from src.scheduler.automation_scheduler import (
    AutomationScheduler, ScheduledTask, TaskType,
    ForegroundRequirement, TaskStatus
)
from src.utils.response import ok, err, ok_msg

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])


# ==================== 请求模型 ====================

class SubmitTaskRequest(BaseModel):
    """提交任务请求"""
    instance_id: str
    task_type: str  # "add_friend" | "mass_send" | "moment_post"
    config: dict = {}
    total: int = 0


class TaskControlRequest(BaseModel):
    """任务控制请求"""
    task_id: str


# ==================== 调度器状态 ====================

@router.get("/status")
async def get_scheduler_status():
    """获取调度器全局状态"""
    scheduler = AutomationScheduler.get_instance()
    return ok(scheduler.get_status())


@router.get("/dashboard")
async def get_dashboard():
    """获取完整仪表盘数据（供前端多实例大盘使用）"""
    scheduler = AutomationScheduler.get_instance()
    return ok(scheduler.get_dashboard())


@router.get("/instances")
async def get_all_instances():
    """获取所有实例状态"""
    scheduler = AutomationScheduler.get_instance()
    return ok({"instances": scheduler.get_all_instances_status()})


@router.get("/instances/{instance_id}")
async def get_instance_status(instance_id: str):
    """获取单个实例状态"""
    scheduler = AutomationScheduler.get_instance()
    status = scheduler.get_instance_status(instance_id)
    if not status:
        return err(40004, "实例不存在")
    return ok(status)


# ==================== 任务管理 ====================

@router.post("/tasks/submit")
async def submit_task(req: SubmitTaskRequest):
    """向指定实例提交任务"""
    scheduler = AutomationScheduler.get_instance()

    try:
        task_type = TaskType(req.task_type)
    except ValueError:
        return err(40000, f"不支持的任务类型: {req.task_type}")

    task = ScheduledTask(
        task_type=task_type,
        total=req.total,
        config=req.config,
        foreground=ForegroundRequirement.REQUIRED,
    )

    task_id = scheduler.submit_task(req.instance_id, task)
    if not task_id:
        return err(40004, "实例不存在，无法提交任务")

    return ok({
        "task_id": task_id,
        "instance_id": req.instance_id,
        "message": f"任务已提交到 {req.instance_id}",
    })


@router.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    """获取单个任务状态"""
    scheduler = AutomationScheduler.get_instance()
    status = scheduler.get_task_status(task_id)
    if not status:
        return err(40004, "任务不存在")
    return ok(status)


@router.post("/tasks/cancel")
async def cancel_task(req: TaskControlRequest):
    """取消任务"""
    scheduler = AutomationScheduler.get_instance()
    success = scheduler.cancel_task(req.task_id)
    if not success:
        return err(40004, "任务不存在或已完成")
    return ok_msg("任务已取消")


@router.post("/tasks/pause")
async def pause_task(req: TaskControlRequest):
    """暂停任务"""
    scheduler = AutomationScheduler.get_instance()
    success = scheduler.pause_task(req.task_id)
    if not success:
        return err(40004, "任务不存在或未在运行")
    return ok_msg("任务已暂停")


@router.post("/tasks/resume")
async def resume_task(req: TaskControlRequest):
    """恢复任务"""
    scheduler = AutomationScheduler.get_instance()
    success = scheduler.resume_task(req.task_id)
    if not success:
        return err(40004, "任务不存在或未暂停")
    return ok_msg("任务已恢复")


# ==================== 调度器控制 ====================

@router.post("/start")
async def start_scheduler():
    """启动调度器"""
    scheduler = AutomationScheduler.get_instance()
    await scheduler.start()
    return ok_msg("调度器已启动")


@router.post("/stop")
async def stop_scheduler():
    """停止调度器"""
    scheduler = AutomationScheduler.get_instance()
    await scheduler.stop()
    return ok_msg("调度器已停止")
