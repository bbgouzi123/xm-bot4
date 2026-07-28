import time
import uuid
from enum import Enum
from typing import Dict, Optional, List, Any, Callable
from dataclasses import dataclass, field
from datetime import datetime

class TaskType(str, Enum):
    ADD_FRIEND = "add_friend"
    MASS_SEND = "mass_send"
    MOMENT_POST = "moment_post"
    CHAT_MONITOR = "chat_monitor"
    CUSTOM = "custom"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ForegroundRequirement(str, Enum):
    """前台需求级别"""
    REQUIRED = "required"        # 必须在前台（点击、输入等）
    PREFERRED = "preferred"      # 最好在前台（提高成功率）
    NOT_NEEDED = "not_needed"    # 不需要前台（扫描会话列表等）


@dataclass
class ScheduledTask:
    """调度任务"""
    task_id: str = field(default_factory=lambda: f"task_{uuid.uuid4().hex[:8]}")
    task_type: TaskType = TaskType.CUSTOM
    instance_id: str = ""           # 绑定的微信实例 ID
    foreground: ForegroundRequirement = ForegroundRequirement.REQUIRED

    # 进度
    status: TaskStatus = TaskStatus.PENDING
    total: int = 0
    processed: int = 0
    succeeded: int = 0
    failed: int = 0

    # 时间
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None

    # 配置
    config: Dict[str, Any] = field(default_factory=dict)
    
    # 执行回调（由具体任务类型设置）
    _execute_batch: Optional[Callable] = field(default=None, repr=False)

    # 日志
    logs: List[Dict] = field(default_factory=list)

    def add_log(self, message: str, level: str = "info"):
        self.logs.append({
            "time": datetime.now().isoformat(),
            "level": level,
            "message": message,
        })
        # 只保留最近 200 条
        if len(self.logs) > 200:
            self.logs = self.logs[-100:]

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type.value,
            "instance_id": self.instance_id,
            "status": self.status.value,
            "total": self.total,
            "processed": self.processed,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "config": self.config,
            "progress_pct": round(self.processed / max(self.total, 1) * 100, 1),
        }
