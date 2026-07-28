from .calendar import CalendarMixin
from .generator import GeneratorMixin
from .runner import RunnerMixin
from .plan_group import PlanGroupMixin

class MomentPlannerService(CalendarMixin, GeneratorMixin, RunnerMixin, PlanGroupMixin):
    """
    xm-core（xm-bot4）· 朋友圈全托管内容工厂 (日历引擎)
    数据存储：内存 + 本地 JSON 快照 (~/.xm-ai-bot/moment_schedules_snapshot.json) + 同步服务，零 SQLite
    """
    def __init__(self, account_id: str, ai_service=None):
        self.account_id = account_id
        self.ai_service = ai_service
