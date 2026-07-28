from fastapi import APIRouter
from src.friend.webhook_pull import WebhookPullManager
from src.api.config_api import _load_configs
from src.utils.response import ok, err

router = APIRouter()

@router.get("/webhook-pull/status")
async def webhook_pull_status():
    """获取企业 IT 系统对接（Webhook 拉取）的状态与日志"""
    manager = WebhookPullManager.get_instance()
    state = manager.get_state()
    return ok(state)

@router.post("/webhook-pull/trigger")
async def webhook_pull_trigger():
    """手动触发一次 Webhook 拉取"""
    configs = _load_configs()
    settings = configs.get("webhook_pull_settings", {})
    if not settings:
        return err(40000, "尚未配置企业 IT 系统对接参数")
    
    url = settings.get("url")
    if not url:
        return err(40000, "接口地址不能为空")

    manager = WebhookPullManager.get_instance()
    # 异步或同步触发？同步触发可以直接返回最新状态
    await manager.trigger_pull_sync(settings)
    return ok(manager.get_state())
