import asyncio
import logging
from typing import Optional
from fastapi import APIRouter

from src.friend import friend_queue
from src.utils.response import ok, err, ok_msg
from .models import StartTaskRequest

logger = logging.getLogger(__name__)
router = APIRouter()

def __getattr__(name):
    if name == "_driver":
        from . import task_engine
        return task_engine._get_effective_driver()
    if name == "_task_state":
        from . import task_engine
        return task_engine._task_state
    raise AttributeError(f"module {__name__} has no attribute {name}")

def init_driver(d):
    from . import task_engine
    task_engine.init_driver(d)

@router.post("/start")
async def start_task(req: StartTaskRequest):
    from . import task_engine
    _task_state = task_engine._task_state
    if _task_state["running"]:
        return err(40000, "任务已在运行中")

    from src.utils.license_validator import LicenseValidator
    features = LicenseValidator.check_features()
    if not features.get("smart_acquisition", False):
        return err(40301, "当前版本不支持智能加好友，请升级套餐")

    if req.active_warmup and not features.get("active_warmup", False):
        return err(40302, "一键智能养号托管为旗舰版独享功能，请升级至旗舰版套餐")

    reset_count = 0
    if req.retry_failed:
        reset_count += friend_queue.batch_reset_status("failed", "pending")
    if req.retry_unknown:
        reset_count += friend_queue.batch_reset_status("unknown", "pending")

    _task_state["config"] = req.dict()
    _task_state["running"] = True
    _task_state["paused"] = False

    stats = friend_queue.get_queue_stats()
    _task_state["progress"] = {
        "total": stats.get("pending", 0) + stats.get("processing", 0),
        "processed": 0, "succeeded": 0, "failed": 0,
    }

    task_engine.save_task_state_to_db()
    asyncio.create_task(task_engine._run_add_friend_loop())
    
    scope_desc = []
    if req.industry_profile_id: scope_desc.append(f"行业={req.industry_profile_id}")
    if req.tag_filter: scope_desc.append(f"标签={req.tag_filter}")
    if req.import_batch_id: scope_desc.append(f"批次={req.import_batch_id}")

    return ok({
        "message": "加好友任务已启动",
        "config": _task_state["config"],
        "reset_count": reset_count,
        "scope": ", ".join(scope_desc) if scope_desc else "全部队列",
    })

@router.post("/stop")
async def stop_task():
    from . import task_engine
    _task_state = task_engine._task_state
    _task_state["running"] = _task_state["paused"] = False
    task_engine.save_task_state_to_db()
    return ok({"reset_count": friend_queue.reset_processing_to_pending()})

@router.post("/pause")
async def pause_task():
    from . import task_engine
    task_engine._task_state["paused"] = True
    task_engine.save_task_state_to_db()
    return ok_msg("操作成功")

@router.post("/resume")
async def resume_task():
    from . import task_engine
    task_engine._task_state["paused"] = False
    task_engine.save_task_state_to_db()
    return ok_msg("操作成功")

@router.get("/status")
async def get_task_status():
    from . import task_engine
    task_engine.try_restore_task_state()
    _task_state = task_engine._task_state
    return ok({
        "running": _task_state["running"],
        "paused": _task_state["paused"] or _task_state.get("paused_by_reply", False),
        "paused_by_reply": _task_state.get("paused_by_reply", False),
        "config": _task_state["config"],
        "progress": _task_state["progress"],
        "queue_stats": friend_queue.get_queue_stats(),
        "today_added": friend_queue.get_today_count(),
    })

@router.get("/logs")
async def get_task_logs(limit: int = 50, industry_profile_id: str = "", status_filter: str = ""):
    return ok({"logs": friend_queue.get_logs(limit=limit, industry_profile_id=industry_profile_id, status_filter=status_filter)})

@router.post("/reset-status")
async def reset_status(from_status: str, to_status: str = "pending"):
    if from_status not in ('failed', 'unknown', 'processing'):
        return err(40000, "只允许重置 failed / unknown / processing")
    return ok({"reset_count": friend_queue.batch_reset_status(from_status, to_status)})

@router.post("/toggle-warmup")
async def toggle_warmup(req: dict):
    enabled = req.get("enabled", False)
    from src.utils.license_validator import LicenseValidator
    if enabled and not LicenseValidator.check_features().get("active_warmup", False):
        return err(40302, "一键智能养号托管为旗舰版独享功能，请升级至旗舰版套餐")
    
    try:
        from src.api.config_api.base_config import _load_configs, _save_configs
        configs = _load_configs()
        configs["active_warmup"] = enabled
        _save_configs(configs)
        from . import task_engine
        task_engine._task_state["config"]["active_warmup"] = enabled
        asyncio.create_task(task_engine._perform_warmup_actions())
    except Exception as e:
        logger.error(f"切换空闲托管失败: {e}")
    return ok({"enabled": enabled})
