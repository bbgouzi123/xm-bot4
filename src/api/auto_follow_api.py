"""
SDR 自动跟单策略子路由 (由 task_api 拆分以满足单文件 300 行有效代码限制)
"""
from fastapi import APIRouter, Request
import logging
import uuid
from datetime import datetime

from src.utils.db_manager import WeChatDBManager
from src.utils.response import ok, err
from src.api.task_api import resolve_audience_by_tags


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/task", tags=["task"])

_driver = None
_active_tasks = None

def init(driver, active_tasks):
    global _driver, _active_tasks
    _driver = driver
    _active_tasks = active_tasks


@router.post("/auto-follow/start")
async def start_auto_follow(request: Request):
    """装载并启动长程自动跟单策略 (SDR CRM 引擎)"""
    if not _driver or not _driver.is_connected():
        return err(40000, "物理控制机未连接微信")

    # 强制校验是否在账号设置中开启了自动跟单
    try:
        from src.crm.account_data import get_active_account, get_account_settings
        account_id = get_active_account()
        settings = get_account_settings(account_id)
        if not settings.get("reply", {}).get("auto_follow", False):
            return err(40003, "启动失败：请先在「自动化任务控制台」中开启「自动跟单」全局功能开关。")
    except Exception as e:
        logger.error(f"[auto_follow_api] 验证跟单全局设置异常: {e}")

    data = await request.json()
    raw = data.get("targets") or data.get("tags") or []
    if isinstance(raw, str):
        raw = [raw]
    targets_input = [str(t).strip() for t in raw if str(t).strip()]
    targets_input = list(dict.fromkeys(targets_input))
    if not targets_input:
        return err(40004, "请先选择跟单范围人群或标签。")

    target_mode = data.get("target_mode", "tag")
    try:
        resolved_targets = resolve_audience_by_tags(targets_input, for_mass_send=False, target_mode=target_mode, bot_wxid=account_id)
    except Exception as e:
        logger.error(f"SDR 受众解析异常: {e}")
        resolved_targets = list(targets_input)
    resolved_targets = list(dict.fromkeys(resolved_targets))

    if not resolved_targets:
        return err(
            40004,
            "该标签群体下暂无可用触达对象。请先在微信侧为客户打好对应标签并同步通讯录，"
            "或确认客户档案中的 AI 意向标签与所选群体一致（可在客户档案中查看）。",
        )
    
    task_id = f"sdr_{uuid.uuid4().hex[:6]}"
    
    task_payload = {
        "task_id": task_id,
        "targets": resolved_targets,
        "follow_days": int(data.get("follow_days", 7)),
        "follow_frequency": data.get("follow_frequency", "daily"),
        "time_range_start": data.get("time_range_start", "09:00"),
        "time_range_end": data.get("time_range_end", "20:00"),
        "follow_scenario": data.get("follow_scenario", ""),
        "use_ai": data.get("use_ai", False),
        "fallback_text": data.get("fallback_text", ""),
        "max_daily": int(data.get("max_daily", 50)),
        "status": "active",
        "created_at": datetime.now().isoformat()
    }
    
    db = WeChatDBManager()
    db.add_auto_follow_task(task_payload)
    
    _active_tasks[task_id] = {
        "status": "running", 
        "total": len(resolved_targets) * int(data.get("follow_days", 1)), 
        "current": 0, 
        "errors": 0,
        "runs_detected": 0
    }
    
    from src.task.auto_follow_daemon import ensure_daemon_started
    ensure_daemon_started()
    
    return ok({"task_id": task_id, "message": f"SDR防断连雷达已上线，设定为生命周期 {task_payload['follow_days']} 天的长期跟踪。"})


@router.get("/auto-follow/status")
async def auto_follow_status():
    """查询是否有运行中的自动跟单（SDR）任务"""
    try:
        from src.crm.account_data import get_active_account, get_account_settings
        account_id = get_active_account()
        settings = get_account_settings(account_id)
        if not settings.get("reply", {}).get("auto_follow", False):
            return ok({"running": False, "count": 0, "task_ids": []})
    except Exception as e:
        logger.error(f"[auto_follow_api] 获取全局跟单配置异常: {e}")

    tasks = [t for t in WeChatDBManager().get_auto_follow_tasks() if t.get("status", "active") == "active"]
    return ok({"running": len(tasks) > 0, "count": len(tasks), "task_ids": [t.get("task_id") for t in tasks if t.get("task_id")]})


@router.post("/auto-follow/stop-all")
async def auto_follow_stop_all():
    """停止所有运行中的自动跟单任务"""
    try:
        from src.crm.account_data import get_active_account, get_account_settings, save_account_settings
        account_id = get_active_account()
        settings = get_account_settings(account_id)
        if "reply" not in settings:
            settings["reply"] = {}
        settings["reply"]["auto_follow"] = False
        save_account_settings(account_id, settings)
    except Exception as e:
        logger.error(f"[auto_follow_api] 停止所有任务时重置全局配置异常: {e}")

    db = WeChatDBManager()
    ids = [t.get("task_id") for t in db.get_auto_follow_tasks() if t.get("status", "active") == "active"]
    n = db.stop_all_active_auto_follow_tasks()
    
    from src.task.scheduler import get_global_scheduler
    sched = get_global_scheduler()
    for tid in [i for i in ids if i]:
        if tid in _active_tasks:
            _active_tasks[tid]["status"] = "cancelled"
        
        # 💡 同步从调度器中真正移除该任务下所有的作业
        task = db.get_auto_follow_task(tid)
        if task:
            targets = task.get("targets", [])
            for target in targets:
                job_id = f"sdr_{tid}_{target}"
                try:
                    if sched.get_job(job_id):
                        sched.remove_job(job_id)
                        logger.info(f"[auto_follow_api] 停止所有任务时已移除 Job: {job_id}")
                except Exception as ex:
                    logger.error(f"[auto_follow_api] 停止所有任务时移除 Job {job_id} 异常: {ex}")
                    
    return ok({"stopped": n, "message": f"已停止 {n} 个自动跟单任务"}) if n else ok({"stopped": 0, "message": "当前没有运行中的跟单任务"})


@router.post("/auto-follow/batch-agent")
async def batch_change_agent(request: Request):
    """批量切换跟单任务中的智能体ID"""
    try:
        data = await request.json()
        agent_id = data.get("agent_id")
        task_ids = data.get("task_ids")
        if not agent_id:
            return err(40000, "缺乏必要的智能体ID (agent_id)")
        if not task_ids or not isinstance(task_ids, list):
            return err(40000, "缺乏必要的任务ID列表 (task_ids)")
        
        from src.task.auto_follow_daemon import batch_switch_follow_agent
        updated_count = batch_switch_follow_agent(agent_id, task_ids)
        return ok({"updated": updated_count, "message": f"成功批量切换 {updated_count} 个任务的跟单智能体"})
    except Exception as e:
        logger.error(f"[auto_follow_api] 批量切换跟单智能体异常: {e}")
        return err(50000, f"批量切换失败: {e}")


@router.get("/auto-follow/jobs")
async def get_auto_follow_jobs():
    """获取所有跟单任务下的具体好友执行状态及下一次预计触达时间"""
    import asyncio
    from src.task.auto_follow_daemon import get_scheduler
    sched = get_scheduler()
    db = WeChatDBManager()
    
    # 异步在线程池中批量获取所有 jobs，避免阻塞主事件循环并减少数据库查询
    try:
        loop = asyncio.get_running_loop()
        jobs = await loop.run_in_executor(None, sched.get_jobs)
        job_map = {job.id: job for job in jobs}
    except Exception as e:
        logger.error(f"[auto_follow_api] 获取 jobs 列表异常: {e}")
        job_map = {}
        
    tasks = db.get_auto_follow_tasks()
    job_details = []
    
    for task in tasks:
        task_id = task.get("task_id")
        targets = task.get("targets", [])
        execution_state = task.get("execution_state") or {}
        follow_days = int(task.get("follow_days", 7))
        status = task.get("status", "active")
        
        for target in targets:
            t_state = execution_state.get(target) or {}
            follow_count = t_state.get("follow_count", 0)
            last_follow_time = t_state.get("last_follow_time", "")
            
            job_id = f"sdr_{task_id}_{target}"
            job = job_map.get(job_id)
            next_run = ""
            if job and job.next_run_time:
                next_run = job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
            
            job_details.append({
                "task_id": task_id,
                "target": target,
                "follow_count": follow_count,
                "follow_days": follow_days,
                "last_follow_time": last_follow_time,
                "next_run_time": next_run,
                "status": status if follow_count < follow_days else "completed",
                "scenario": task.get("follow_scenario", "")
            })
            
    return ok({"jobs": job_details})


@router.get("/auto-follow/logs")
async def get_auto_follow_logs(limit: int = 100):
    """获取本地 SDR 跟单的触达记录审计日志"""
    import os
    import json
    log_file = "data/sdr_logs/sdr_execute.jsonl"
    if not os.path.exists(log_file):
        return ok({"logs": []})
        
    logs = []
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    logs.append(json.loads(line.strip()))
        logs = logs[::-1][:limit]
    except Exception as e:
        logger.error(f"读取跟单日志异常: {e}")
        
    return ok({"logs": logs})
