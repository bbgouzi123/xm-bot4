import logging
from .core import MassSendingCore

logger = logging.getLogger(__name__)

# 动态引入并执行注册
try:
    from src.task.mass_sending_helper import register_manager_adapter
    register_manager_adapter()
except Exception as e:
    logger.error(f"[MassSendingCore] 执行生命周期注册异常: {e}")
