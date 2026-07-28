"""
xm-bot4 · 数据同步客户端 (cloud_sync/)

职责与自适应机制：
- 本模块虽然在代码中常被称为“cloud”，但实际上是自适应后端同步客户端：
  * 本地开发环境（源码运行）：连接本地运行的 Rust 后端服务 `xm-bot4-cloud`（127.0.0.1:42040）。
  * 生产打包环境：自动切换连接线上部署的 `xm-bot4-cloud` 后端服务网关。
- 启动时拉取同步资源（如朋友圈排期、用户配置等）
- 本地数据变更后异步/同步推送到对应的后端服务中（本地的 `xm-bot4-cloud` 后端或线上的 `xm-bot4-cloud` 后端）
- 统一事件日志上报
- 用户设置双向同步
"""


from .base import CloudSyncBaseMixin
from .helpers import load_cloud_cache_fast
from .public import CloudSyncPublicMixin
from .user import CloudSyncUserMixin
from .events import CloudSyncEventsMixin
from .worker import CloudSyncWorkerMixin
from .enterprise import CloudSyncEnterpriseMixin


class CloudSyncClient(
    CloudSyncPublicMixin,
    CloudSyncUserMixin,
    CloudSyncEventsMixin,
    CloudSyncWorkerMixin,
    CloudSyncEnterpriseMixin,
    CloudSyncBaseMixin,
):
    """后端服务数据同步客户端（多继承组合版）

    环境自适应策略：
    - 本地开发环境（源码运行）：优先连接本地启动的 Rust 后端服务 `xm-bot4-cloud` (42040 端口)
    - 生产运行态（PyInstaller 打包）：自动切换连接线上部署的 `xm-bot4-cloud` 后端服务网关
    """
    pass


def get_cloud_client() -> CloudSyncClient:
    """获取全局后端服务数据同步客户端单例"""
    return CloudSyncClient()


__all__ = ["CloudSyncClient", "get_cloud_client", "load_cloud_cache_fast"]
