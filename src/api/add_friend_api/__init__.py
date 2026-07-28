"""
加好友 API — 模块化封装
路由前缀: /api/add-friend
"""
from fastapi import APIRouter
from . import import_service, enterprise_service, queue_service, task_service, webhook_pull_service, group_task_service, group_dispatch_service

# 创建统一路由
router = APIRouter(prefix="/api/add-friend", tags=["add-friend"])

# 挂载各子模块路由
router.include_router(import_service.router)
router.include_router(enterprise_service.router)
router.include_router(queue_service.router)
router.include_router(task_service.router)
router.include_router(webhook_pull_service.router)
router.include_router(group_task_service.router)
router.include_router(group_dispatch_service.router)

# 对外暴露初始化接口
def init(driver):
    """全局驱动初始化"""
    task_service.init_driver(driver)
