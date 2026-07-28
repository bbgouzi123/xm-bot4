import time
from typing import Dict, Optional, List, Any
from dataclasses import dataclass, field
from .types import TaskType, TaskStatus, ScheduledTask

@dataclass
class InstanceWorker:
    """单个微信实例的工作器"""
    instance_id: str
    driver: Any = None              # WeChatDriver 实例
    hwnd: int = 0

    # 实例信息
    nickname: str = ""
    wxid: str = ""

    # 任务队列
    tasks: Dict[str, ScheduledTask] = field(default_factory=dict)

    # 状态
    is_online: bool = True
    last_active: float = field(default_factory=time.time)

    def get_active_task(self, task_type: TaskType) -> Optional[ScheduledTask]:
        """获取指定类型正在运行的任务"""
        for task in self.tasks.values():
            if task.task_type == task_type and task.status == TaskStatus.RUNNING:
                return task
        return None

    def get_pending_tasks(self) -> List[ScheduledTask]:
        """获取所有待执行任务"""
        return [t for t in self.tasks.values()
                if t.status in (TaskStatus.PENDING, TaskStatus.RUNNING)]

    def get_stats(self) -> dict:
        """获取实例统计"""
        running = sum(1 for t in self.tasks.values() if t.status == TaskStatus.RUNNING)
        pending = sum(1 for t in self.tasks.values() if t.status == TaskStatus.PENDING)
        completed = sum(1 for t in self.tasks.values() if t.status == TaskStatus.COMPLETED)

        return {
            "instance_id": self.instance_id,
            "nickname": self.nickname,
            "wxid": self.wxid,
            "hwnd": self.hwnd,
            "is_online": self.is_online,
            "tasks_running": running,
            "tasks_pending": pending,
            "tasks_completed": completed,
            "tasks_total": len(self.tasks),
            "last_active": self.last_active,
        }

    def to_dict(self) -> dict:
        return {
            **self.get_stats(),
            "tasks": {tid: t.to_dict() for tid, t in self.tasks.items()},
        }
