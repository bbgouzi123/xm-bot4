import asyncio
import logging
import time
import os
import json
from typing import Dict, Optional, List, Any, Callable

from .types import TaskType, TaskStatus, ForegroundRequirement, ScheduledTask
from .worker import InstanceWorker

logger = logging.getLogger(__name__)


class AutomationScheduler:
    """
    自动化并行调度器（单例）
    1. 管理多个微信实例的任务队列
    2. 控制前台窗口令牌（前台互斥）
    3. 轮询调度各实例的任务执行，支持崩溃高可用恢复 (SDR/群发自愈)
    """

    _instance = None

    def __init__(self):
        self._workers: Dict[str, InstanceWorker] = {}
        self._foreground_lock = asyncio.Lock()
        self._running = False
        self._scheduler_task: Optional[asyncio.Task] = None
        self._task_executors: Dict[TaskType, Callable] = {}
        self._time_slice_seconds = 30
        self._round_interval = 2.0
        self._idle_sleep = 5.0

    @classmethod
    def get_instance(cls) -> 'AutomationScheduler':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register_task_executor(self, task_type: TaskType, executor: Callable):
        self._task_executors[task_type] = executor
        logger.info(f"[调度器] 注册任务执行器: {task_type.value}")
        for worker in self._workers.values():
            for task in worker.tasks.values():
                if task.task_type == task_type and not task._execute_batch:
                    task._execute_batch = executor
                    task.add_log("自愈机制：动态补齐绑定任务执行器")

    def _get_snapshot_path(self) -> str:
        appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
        snapshot_dir = os.path.join(appdata, "xm-bot4", "state")
        os.makedirs(snapshot_dir, exist_ok=True)
        return os.path.join(snapshot_dir, "scheduler_tasks.json")

    def _save_pending_tasks_snapshot(self):
        try:
            snapshot_data = []
            for worker in self._workers.values():
                for task in worker.tasks.values():
                    if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.PAUSED):
                        if task.task_type in (TaskType.MASS_SEND, TaskType.MOMENT_POST, TaskType.ADD_FRIEND):
                            snapshot_data.append({
                                "task_id": task.task_id, "task_type": task.task_type.value,
                                "instance_id": task.instance_id, "foreground": task.foreground.value,
                                "status": task.status.value, "total": task.total, "processed": task.processed,
                                "succeeded": task.succeeded, "failed": task.failed, "config": task.config,
                                "created_at": task.created_at
                            })
            with open(self._get_snapshot_path(), "w", encoding="utf-8") as f:
                json.dump(snapshot_data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logger.error(f"[调度器] 保存任务快照失败: {e}")

    async def restore_pending_tasks(self):
        try:
            snapshot_path = self._get_snapshot_path()
            if not os.path.exists(snapshot_path):
                return
            with open(snapshot_path, "r", encoding="utf-8") as f:
                snapshot_data = json.load(f)
            if not snapshot_data:
                return
            
            restored_count = 0
            for item in snapshot_data:
                inst_id = item.get("instance_id")
                worker = self._workers.get(inst_id)
                task_id = item.get("task_id")
                try:
                    task_type = TaskType(item.get("task_type"))
                    fg = ForegroundRequirement(item.get("foreground", "required"))
                    status = TaskStatus(item.get("status", "pending"))
                except ValueError:
                    continue
                
                task = ScheduledTask(
                    task_id=task_id, task_type=task_type, instance_id=inst_id,
                    foreground=fg, status=status, total=item.get("total", 0),
                    processed=item.get("processed", 0), succeeded=item.get("succeeded", 0),
                    failed=item.get("failed", 0), config=item.get("config", {}),
                    created_at=item.get("created_at", time.time())
                )
                
                executor = self._task_executors.get(task_type)
                if executor:
                    task._execute_batch = executor
                    task.add_log("自愈恢复：成功恢复任务并绑定执行器")
                else:
                    task.add_log("自愈恢复警告：暂无匹配的执行器绑定，等待延迟绑定")
                
                if worker and task_id not in worker.tasks:
                    worker.tasks[task_id] = task
                    restored_count += 1
            if restored_count > 0:
                logger.info(f"[调度器] 成功恢复已安排的任务数: {restored_count}")
        except Exception as e:
            logger.error(f"[调度器] 恢复快照任务失败: {e}")

    def register_instance(self, instance_id: str, driver: Any, hwnd: int = 0, nickname: str = "", wxid: str = "") -> InstanceWorker:
        if instance_id in self._workers:
            worker = self._workers[instance_id]
            worker.driver, worker.hwnd = driver, hwnd
            if nickname: worker.nickname = nickname
            if wxid: worker.wxid = wxid
            worker.is_online = True
        else:
            worker = InstanceWorker(instance_id=instance_id, driver=driver, hwnd=hwnd, nickname=nickname, wxid=wxid)
            self._workers[instance_id] = worker
        return worker

    def unregister_instance(self, instance_id: str):
        if instance_id in self._workers:
            worker = self._workers.pop(instance_id)
            for task in worker.tasks.values():
                if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
                    task.status = TaskStatus.CANCELLED

    def get_worker(self, instance_id: str) -> Optional[InstanceWorker]:
        return self._workers.get(instance_id)

    def submit_task(self, instance_id: str, task: ScheduledTask) -> Optional[str]:
        worker = self._workers.get(instance_id)
        if not worker:
            return None
        task.instance_id = instance_id
        if not task._execute_batch:
            executor = self._task_executors.get(task.task_type)
            if executor:
                task._execute_batch = executor
                task.add_log("已绑定对应类型的任务执行器")
        worker.tasks[task.task_id] = task
        self._save_pending_tasks_snapshot()
        return task.task_id

    def cancel_task(self, task_id: str) -> bool:
        for worker in self._workers.values():
            if task_id in worker.tasks:
                task = worker.tasks[task_id]
                if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.PAUSED):
                    task.status = TaskStatus.CANCELLED
                    task.add_log("任务已被手动取消", "warning")
                    self._save_pending_tasks_snapshot()
                    return True
        return False

    def pause_task(self, task_id: str) -> bool:
        for worker in self._workers.values():
            if task_id in worker.tasks:
                task = worker.tasks[task_id]
                if task.status == TaskStatus.RUNNING:
                    task.status = TaskStatus.PAUSED
                    task.add_log("任务已暂停")
                    self._save_pending_tasks_snapshot()
                    return True
        return False

    def resume_task(self, task_id: str) -> bool:
        for worker in self._workers.values():
            if task_id in worker.tasks:
                task = worker.tasks[task_id]
                if task.status == TaskStatus.PAUSED:
                    task.status = TaskStatus.RUNNING
                    task.add_log("任务已恢复")
                    self._save_pending_tasks_snapshot()
                    return True
        return False

    async def start(self):
        if self._running:
            return
        self._running = True
        await self.restore_pending_tasks()
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    async def stop(self):
        self._running = False
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass

    async def _scheduler_loop(self):
        while self._running:
            try:
                has_work = False
                for instance_id, worker in list(self._workers.items()):
                    if not self._running: break
                    if not worker.is_online: continue
                    pending_tasks = worker.get_pending_tasks()
                    if not pending_tasks: continue
                    has_work = True
                    for task in pending_tasks:
                        if not self._running: break
                        if task.status == TaskStatus.PAUSED: continue
                        if task.foreground == ForegroundRequirement.REQUIRED:
                            async with self._foreground_lock:
                                await self._execute_task_slice(worker, task)
                        else:
                            await self._execute_task_slice(worker, task)
                    await asyncio.sleep(self._round_interval)
                if not has_work:
                    await asyncio.sleep(self._idle_sleep)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[调度器] 调度异常: {e}")
                await asyncio.sleep(5)

    async def _execute_task_slice(self, worker: InstanceWorker, task: ScheduledTask):
        if task.status == TaskStatus.PENDING:
            task.status = TaskStatus.RUNNING
            task.started_at = time.time()
            task.add_log(f"任务开始执行 (实例: {worker.nickname})")
            self._save_pending_tasks_snapshot()

        if task.foreground == ForegroundRequirement.REQUIRED and worker.hwnd:
            try:
                import ctypes
                user32 = ctypes.windll.user32
                user32.ShowWindow(worker.hwnd, 9)
                await asyncio.sleep(0.2)
                user32.SetForegroundWindow(worker.hwnd)
                await asyncio.sleep(0.3)
            except Exception as e:
                logger.warning(f"[调度器] 置前窗口失败: {e}")

        if task._execute_batch:
            try:
                slice_start = time.time()
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: task._execute_batch(worker, task)
                )
                worker.last_active = time.time()
                task.add_log(f"时间片执行完成 ({time.time() - slice_start:.1f}s)")
                self._save_pending_tasks_snapshot()
            except Exception as e:
                logger.error(f"[调度器] 任务执行异常: {e}")
                task.add_log(f"执行异常: {e}", "error")
                task.failed += 1
                self._save_pending_tasks_snapshot()

        if task.processed >= task.total and task.total > 0:
            task.status = TaskStatus.COMPLETED
            task.completed_at = time.time()
            task.add_log(f"任务完成: 成功 {task.succeeded}/{task.total}, 失败 {task.failed}")
            self._save_pending_tasks_snapshot()

    def get_status(self) -> dict:
        total, running, pending = 0, 0, 0
        for w in self._workers.values():
            for t in w.tasks.values():
                total += 1
                if t.status == TaskStatus.RUNNING: running += 1
                elif t.status == TaskStatus.PENDING: pending += 1
        return {
            "running": self._running, "instances": len(self._workers),
            "instances_online": sum(1 for w in self._workers.values() if w.is_online),
            "total_tasks": total, "running_tasks": running, "pending_tasks": pending, "time_slice": self._time_slice_seconds,
        }

    def get_instance_status(self, instance_id: str) -> Optional[dict]:
        w = self._workers.get(instance_id)
        return w.to_dict() if w else None

    def get_all_instances_status(self) -> List[dict]:
        return [w.to_dict() for w in self._workers.values()]

    def get_task_status(self, task_id: str) -> Optional[dict]:
        for w in self._workers.values():
            if task_id in w.tasks:
                return w.tasks[task_id].to_dict()
        return None

    def get_dashboard(self) -> dict:
        instances = []
        for worker in self._workers.values():
            inst_data = worker.get_stats()
            inst_data["active_tasks"] = [
                t.to_dict() for t in worker.tasks.values()
                if t.status in (TaskStatus.RUNNING, TaskStatus.PENDING, TaskStatus.PAUSED)
            ]
            instances.append(inst_data)
        return {"scheduler": self.get_status(), "instances": instances}
