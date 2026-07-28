import sys
import os
import threading
from .service import MomentPlannerService
from .bootstrap import (
    _bootstrap_schedules,
    reload_schedules_from_cloud_for_active_bot,
    expire_stale_pending_moments_and_collect_due,
)

def _is_build_env() -> bool:
    if "PyInstaller" in sys.modules or "setuptools" in sys.modules:
        return True
    main_file = os.path.basename(sys.argv[0]).lower() if sys.argv else ""
    if any(x in main_file for x in ["pyinstaller", "setup.py", "build_protected"]):
        return True
    return False

# 启动时异步加载排期数据，对标原逻辑（构建/打包时不启动线程避免死锁与外部请求）
if not _is_build_env():
    from .bootstrap import bootstrap_schedules_lazy
    bootstrap_schedules_lazy()

def __getattr__(name: str):
    """支持外部代码通过 module 动态获取内部的排期状态变量（如 _schedules、_schedule_lock 等）"""
    if name in ("_schedules", "_schedule_lock", "_executed_ids", "_next_id"):
        from . import state
        return getattr(state, name)
    raise AttributeError(f"module {__name__} has no attribute {name}")
