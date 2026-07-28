"""
许可证校验模块 — 对接 XM-User 统一授权平台
"""
import logging
from .env import SA_LICENSE_API, license_client
from .machine import MachineMixin
from .storage import StorageMixin
from .network import NetworkMixin
from .subscription import SubscriptionMixin
from .heartbeat import HeartbeatMixin
from .features import FeaturesMixin

logger = logging.getLogger(__name__)

class LicenseValidator(
    SubscriptionMixin,
    HeartbeatMixin,
    FeaturesMixin
):
    """
    许可证与设备校验核心模块 — 对接 XM-User 统一授权平台
    
    由多个职能 Mixin 组合而成：
    - MachineMixin: 机器码生成
    - StorageMixin: 本地缓存与试用逻辑
    - NetworkMixin: HTTP 请求封装
    - SubscriptionMixin: 新版 (V2/V3) 订阅制
    - HeartbeatMixin: 后台心跳校验
    - FeaturesMixin: 功能锁与统一状态
    """
    pass

# 对外导出统一类与变量
__all__ = ["LicenseValidator", "SA_LICENSE_API", "license_client"]
