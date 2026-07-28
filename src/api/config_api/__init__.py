from .state import router

from . import base_config
from . import config_test_api
from . import contacts
from . import friends
from . import tasks
from . import moments
from . import privacy_shield

# 暴露外部引用的配置相关函数（按需导出或通过 __getattr__ 暴露）
from .base_config import _load_configs, _save_configs
from .config_test_api import _reload_ai_service, _reload_friend_request_monitor

def init(driver, ai_service=None):
    """初始化底层驱动及 AI 服务"""
    from . import state
    state._driver = driver
    state._ai_service = ai_service

def __getattr__(name: str):
    """支持外部代码通过 module 动态获取内部的状态（如 _friend_request_monitor 等）"""
    if name in ("_driver", "_ai_service", "_friend_request_monitor", "_moment_interaction_manager"):
        from . import state
        return getattr(state, name)
    raise AttributeError(f"module {__name__} has no attribute {name}")
