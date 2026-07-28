import asyncio
import logging
import uuid
from datetime import datetime
from typing import List, Dict

from src.utils.db_manager import WeChatDBManager
from .executor import MassSendingExecutorMixin

logger = logging.getLogger(__name__)

class MassSendingCore(MassSendingExecutorMixin):
    """大面积安全群发核心执行引擎 (Phase 10-C)"""
    
    def __init__(self, driver=None):
        self.driver = driver
        self._db = WeChatDBManager()

    async def _run_uia(self, func, *args):
        """在专用 UIA 单线程池中执行 UIA 操作（COM 线程安全）"""
        from src.utils.uia_task_runner import run_in_uia_thread
        return await run_in_uia_thread(func, *args)

    def submit_task(self, targets: List[str], msg_type: str, content: str, media_urls: str = "", schedule_time: str = "", script_group_id: str = None, target_mode: str = "tag") -> List[str]:
        """提交群发大盘任务"""
        job_id = f"mass_{uuid.uuid4().hex[:6]}"
        
        job = {
            "id": job_id,
            "content": content,
            "media_urls": media_urls, 
            "target_tags": ",".join(targets),
            "status": "scheduled" if schedule_time else "pending",
            "schedule_time": schedule_time or None,
            "script_group_id": script_group_id or None,
            "created_at": datetime.now().isoformat()
        }
        self._db.add_mass_send_job(job)
        
        try:
            from src.utils.task_helpers import resolve_audience_by_tags
            drv_wxid = None
            if self.driver:
                drv_wxid = getattr(self.driver, "bot_wxid", None) or getattr(self.driver, "_wxid", None)
            resolved_targets = resolve_audience_by_tags(targets, for_mass_send=True, target_mode=target_mode, bot_wxid=drv_wxid)
        except Exception as e:
            logger.error(f"[MassSendingCore] 受众解析异常: {e}")
            resolved_targets = list(targets)

        if not resolved_targets:
            self._db.update_mass_send_job(job_id, {"status": "completed"})
            raise ValueError("未能解析出任何有效的群发受众好友，请确认所选标签或好友是否有效且已同步通讯录")

        queue_items = []
        for friend in resolved_targets:
            queue_items.append({
                "id": f"item_{uuid.uuid4().hex[:6]}",
                "job_id": job_id,
                "friend_wxid": friend,
                "status": "pending",
                "sent_at": None,
                "error_msg": None
            })
        self._db.add_mass_send_queues(queue_items)

        if schedule_time:
            try:
                from src.task.scheduler import get_global_scheduler
                from src.task.mass_sending_helper import trigger_mass_send_job
                run_date = datetime.strptime(schedule_time, "%Y-%m-%d %H:%M:%S")
                
                if run_date <= datetime.now():
                    asyncio.create_task(self._execute_job(job_id))
                else:
                    scheduler = get_global_scheduler()
                    scheduler.add_job(
                        trigger_mass_send_job,
                        trigger='date',
                        run_date=run_date,
                        args=[job_id],
                        id=f"mass_job_{job_id}",
                        replace_existing=True
                    )
                    if not scheduler.running:
                        scheduler.start()
                    logger.info(f"[MassSendingCore] 已成功将群发任务 {job_id} 定时注册到 APScheduler，触发时间: {schedule_time}")
            except Exception as e:
                logger.error(f"[MassSendingCore] 定时任务注册异常 ({schedule_time})，将自动回退为立即发送: {e}")
                asyncio.create_task(self._execute_job(job_id))
        else:
            asyncio.create_task(self._execute_job(job_id))
        
        return [job_id]

    def get_all_tasks(self) -> List[Dict]:
        """获取所有群发大盘任务及其最新状态"""
        jobs = self._db.get_mass_send_jobs()
        formatted_jobs = []
        for job in jobs:
            queues = self._db.get_mass_send_queues(job["id"])
            total = len(queues)
            current = len([x for x in queues if x["status"] in ("sent", "failed")])
            errors = len([x for x in queues if x["status"] == "failed"])
            
            formatted_jobs.append({
                "id": job["id"],
                "content": job["content"],
                "status": job["status"],
                "total": total,
                "current": current,
                "errors": errors,
                "media_urls": job.get("media_urls", ""),
                "schedule_time": job.get("schedule_time"),
                "created_at": job["created_at"]
            })
        return formatted_jobs

    def cancel_task(self, task_id: str) -> bool:
        from src.task.mass_sending_helper import cancel_task
        return cancel_task(self._db, task_id)

    def cancel_all(self) -> int:
        from src.task.mass_sending_helper import cancel_all
        return cancel_all(self._db)

    def pause_task(self, task_id: str) -> bool:
        from src.task.mass_sending_helper import pause_task
        return pause_task(self._db, task_id)

    def resume_task(self, task_id: str) -> bool:
        from src.task.mass_sending_helper import resume_task
        success = resume_task(self._db, task_id)
        if success:
            asyncio.create_task(self._execute_job(task_id))
        return success

    def resume_all_pending_jobs(self):
        from src.task.mass_sending_helper import resume_all_pending_jobs
        resume_all_pending_jobs(self)
