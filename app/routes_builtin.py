"""Previously inline FastAPI routes from main.py."""
from fastapi import APIRouter
from app.routes.avatar import router as avatar_router
from app.routes.bot import router as bot_router
from app.routes.multi_account import router as multi_account_router
from app.routes.takeover import router as takeover_router
from app.routes.wechat import router as wechat_router

router = APIRouter()

router.include_router(avatar_router)
router.include_router(bot_router)
router.include_router(multi_account_router)
router.include_router(takeover_router)
router.include_router(wechat_router)


def __getattr__(name: str):
    if name == '_bot_automation_running':
        import app.state as app_state
        return app_state._bot_automation_running
    elif name == '_start_bot_core':
        from app.routes.bot import _start_bot_core
        return _start_bot_core
    raise AttributeError(f"module {__name__} has no attribute {name}")
