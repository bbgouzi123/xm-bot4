"""
自动跟单 — config_api 中 /api/tasks/auto-follow* 使用的门面类。

与 task_api 使用同一 WeChatDBManager 自动跟单队列，避免双套状态机。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Union

from src.utils.db_manager import WeChatDBManager
from src.utils.response import err, ok
from src.task.scheduler import get_global_scheduler

logger = logging.getLogger(__name__)


class AutoFollowTask:
    """包装持久化队列，供 config_api 创建/查询/取消跟单任务。"""

    def __init__(self, driver, ai_service=None):
        self._driver = driver
        self._ai = ai_service
        self._db = WeChatDBManager()

    def create_task(self, body: Dict[str, Any]) -> dict:
        if not isinstance(body, dict):
            return err(40000, "请求体无效")
        raw_targets = body.get("targets") or body.get("tags") or []
        if isinstance(raw_targets, str):
            targets: List[str] = [raw_targets] if raw_targets.strip() else []
        else:
            targets = [str(t).strip() for t in raw_targets if str(t).strip()]
        # 💡 强去重处理，防止同一批 targets 产生重复项
        targets = list(dict.fromkeys(targets))
        task_id = body.get("task_id") or f"afl_{uuid.uuid4().hex[:8]}"
        payload = {
            "task_id": task_id,
            "targets": targets,
            "follow_days": int(body.get("follow_days", 7)),
            "follow_frequency": body.get("follow_frequency", "daily"),
            "time_range_start": body.get("time_range_start", "09:00"),
            "time_range_end": body.get("time_range_end", "20:00"),
            "follow_scenario": body.get("follow_scenario", ""),
            "use_ai": bool(body.get("use_ai", False)),
            "fallback_text": body.get("fallback_text", ""),
            "max_daily": int(body.get("max_daily", 50)),
            "status": "active",
            "created_at": datetime.now().isoformat(),
        }
        self._db.add_auto_follow_task(payload)
        logger.info("[AutoFollowTask] 已创建任务 %s", task_id)
        return ok({"task_id": task_id, "message": "已创建跟单任务"})

    def create_batch_tasks(self, body: Dict[str, Any]) -> dict:
        raw: Union[List[Any], None] = body.get("tasks") or body.get("batch")
        if not raw:
            return err(40000, "batch 为空")
        n = 0
        for it in raw:
            sub = it if isinstance(it, dict) else {"targets": it}
            r = self.create_task(sub)
            if r.get("code") == 20000:
                n += 1
        return ok({"created": n})

    def get_tasks_by_account(self, _account_id: str = "main") -> dict:
        tasks = self._db.get_auto_follow_tasks()
        return ok({"tasks": tasks, "count": len(tasks)})

    def cancel_task(self, task_id: str) -> dict:
        if not task_id:
            return err(40000, "task_id 为空")
        task = self._db.get_auto_follow_task(task_id)
        ok_up = self._db.update_auto_follow_task(
            task_id,
            {"status": "cancelled", "cancelled_at": datetime.now().isoformat()},
        )
        if ok_up:
            # 💡 同时也从调度器中真正移除该任务下所有的作业
            if task:
                targets = task.get("targets", [])
                sched = get_global_scheduler()
                for target in targets:
                    job_id = f"sdr_{task_id}_{target}"
                    try:
                        if sched.get_job(job_id):
                            sched.remove_job(job_id)
                            logger.info(f"[AutoFollowTask] 已成功移除 Job: {job_id}")
                    except Exception as ex:
                        logger.error(f"[AutoFollowTask] 移除 Job {job_id} 时异常: {ex}")
            return ok({"task_id": task_id, "message": "已取消"})
        return err(40400, "任务不存在")

    def pause_task(self, task_id: str) -> dict:
        if not task_id:
            return err(40000, "task_id 为空")
        task = self._db.get_auto_follow_task(task_id)
        if self._db.update_auto_follow_task(
            task_id,
            {"status": "paused", "paused_at": datetime.now().isoformat()},
        ):
            # 💡 同时也从调度器中暂停对应的作业
            if task:
                targets = task.get("targets", [])
                sched = get_global_scheduler()
                for target in targets:
                    job_id = f"sdr_{task_id}_{target}"
                    try:
                        if sched.get_job(job_id):
                            sched.pause_job(job_id)
                            logger.info(f"[AutoFollowTask] 已成功暂停 Job: {job_id}")
                    except Exception as ex:
                        logger.error(f"[AutoFollowTask] 暂停 Job {job_id} 时异常: {ex}")
            return ok({"task_id": task_id, "status": "paused"})
        return err(40400, "任务不存在")

    def resume_task(self, task_id: str) -> dict:
        if not task_id:
            return err(40000, "task_id 为空")
        task = self._db.get_auto_follow_task(task_id)
        if self._db.update_auto_follow_task(
            task_id,
            {"status": "active", "resumed_at": datetime.now().isoformat()},
        ):
            # 💡 同时也从调度器中恢复执行对应的作业
            if task:
                targets = task.get("targets", [])
                sched = get_global_scheduler()
                for target in targets:
                    job_id = f"sdr_{task_id}_{target}"
                    try:
                        if sched.get_job(job_id):
                            sched.resume_job(job_id)
                            logger.info(f"[AutoFollowTask] 已成功恢复 Job: {job_id}")
                    except Exception as ex:
                        logger.error(f"[AutoFollowTask] 恢复 Job {job_id} 时异常: {ex}")
            return ok({"task_id": task_id, "status": "active"})
        return err(40400, "任务不存在")
