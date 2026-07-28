"""
朋友圈智能互动巡游 API 路由（已从 moment_api.py 拆分，符合 300 行规范）

GET  /api/moment/auto-comment/status  — 互动状态
GET  /api/moment/interaction-logs     — 互动日志
POST /api/moment/toggle-auto-comment  — 开关互动巡游
POST /api/moment/auto-comment/resume  — 恢复巡游
POST /api/moment/auto-comment/pause   — 暂停巡游
GET  /api/moment/settings             — 互动配置
POST /api/moment/settings             — 保存互动配置
"""
import logging
from fastapi import APIRouter, Request

from src.utils.response import ok, err

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_active_driver():
    try:
        from app.state import account_manager as am
        from src.crm.account_data import get_active_account
        active_wxid = get_active_account()
        if am:
            if active_wxid and active_wxid != "default":
                inst = am.get_instance_by_wxid(active_wxid)
                if inst and inst.driver:
                    return inst.driver
            if am.primary_driver:
                return am.primary_driver
            if am._instances:
                first_inst = next(iter(am._instances.values()), None)
                if first_inst and first_inst.driver:
                    return first_inst.driver
    except Exception:
        pass
    try:
        from src.api.config_api import state as config_state
        return config_state._driver
    except Exception:
        return None



def _get_manager():
    import app.state as app_state
    manager = getattr(app_state, 'moment_interaction_manager', None)
    if manager is None:
        try:
            driver = _get_active_driver()
            if driver:
                from src.api.config_api import state as config_state
                from src.monitor.moment_interaction_manager import MomentInteractionManager
                manager = MomentInteractionManager(driver, config_state._ai_service)
                app_state.moment_interaction_manager = manager
        except Exception as e:
            logger.warning(f"延迟初始化朋友圈互动管理器异常: {e}")
    return manager


@router.get("/api/moment/auto-comment/status")
async def get_auto_comment_status():
    """获取朋友圈互动巡游状态与统计数据"""
    manager = _get_manager()
    if not manager:
        return ok({"status": "stopped", "interactions_count": 0, "pending_tags": 0})
    try:
        return ok(manager.get_status())
    except Exception as e:
        logger.error(f"获取互动状态异常: {e}")
        return err(40000, "操作失败", {"message": str(e)})


@router.get("/api/moment/interaction-logs")
async def get_interaction_logs(limit: int = 50, action_type: str = ""):
    """获取朋友圈互动日志"""
    manager = _get_manager()
    if not manager:
        return ok({"logs": []})
    try:
        logs = manager.get_logs(limit)
        if action_type:
            logs = [log for log in logs if log.get("type") == action_type]
        return ok({"logs": list(reversed(logs))})
    except Exception as e:
        logger.error(f"获取互动日志异常: {e}")
        return err(40000, "操作失败", {"message": str(e)})


@router.post("/api/moment/toggle-auto-comment")
async def toggle_auto_comment(request: Request):
    """开启/关闭朋友圈智能点赞评论"""
    # 🌟 体验版及付费套餐 License 校验
    from src.utils.license_validator import LicenseValidator
    import asyncio
    loop = asyncio.get_running_loop()
    sub = await loop.run_in_executor(None, LicenseValidator.check_subscription)
    if sub.get("status") in ("trial_expired", "expired"):
        return err(40302, "操作失败", {"message": "体验版已到期！部分功能已受限，请选择版本升级。"})
        
    features = await loop.run_in_executor(None, LicenseValidator.check_features)
    if not features.get("moments_auto", False):
        return err(40301, "操作失败", {"message": "当前版本不支持朋友圈互动功能，请升级套餐"})

    # 🌟 AI 额度校验
    ai_limit = features.get("ai_daily_limit", 30)
    if ai_limit > 0:
        from src.utils.daily_counter import DailyCounter
        from src.api.config_api import state as config_state
        account_id = getattr(config_state._driver, 'bot_wxid', 'main') if config_state._driver else 'main'
        current_count = DailyCounter().get_count("auto_reply", account_id)
        if current_count >= ai_limit:
            return err(40303, "操作失败", {"message": f"今日 AI 额度（{ai_limit}次）已用完，无法开启，请升级套餐"})

    driver = _get_active_driver()
    if driver and not driver.is_connected():
        try:
            driver.connect()
        except Exception:
            pass

    if not driver or not driver.is_connected():
        from src.crm.account_data import get_active_account
        active_wxid = get_active_account()
        if not active_wxid or active_wxid == "default":
            return err(40000, "操作失败", {"message": "微信未连接，请先确保微信已登录并连接"})


    manager = _get_manager()
    if not manager:
        return err(40000, "操作失败", {"message": "智能互动组件未加载，请确保微信已登录并连接"})

    try:
        body = await request.json()
        action = body.get("action", "")

        from src.utils.moment_config import get_moment_settings, save_moment_settings
        from src.crm.account_data import get_active_account
        account_id = get_active_account()
        settings = get_moment_settings(account_id)

        if action == "start":
            manager.start()
            settings["enabled"] = True
            save_moment_settings(settings, account_id)
            return ok({"message": "开启成功"})
        elif action == "stop":
            manager.stop()
            settings["enabled"] = False
            save_moment_settings(settings, account_id)
            return ok({"message": "停止成功"})
        else:
            return err(40000, "操作失败", {"message": "无效的动作"})
    except Exception as e:
        logger.error(f"操作自动评论状态异常: {e}")
        return err(40000, "操作失败", {"message": str(e)})

@router.post("/api/moment/auto-comment/resume")
async def resume_auto_comment():
    """恢复运行朋友圈巡游"""
    manager = _get_manager()
    if not manager:
        return err(40000, "操作失败", {"message": "智能互动组件未加载"})
    try:
        manager.resume()
        return ok({"message": "恢复成功"})
    except Exception as e:
        logger.error(f"恢复自动评论异常: {e}")
        return err(40000, "操作失败", {"message": str(e)})


@router.post("/api/moment/auto-comment/pause")
async def pause_auto_comment():
    """暂停朋友圈巡游"""
    manager = _get_manager()
    if not manager:
        return err(40000, "操作失败", {"message": "智能互动组件未加载"})
    try:
        manager.pause()
        return ok({"message": "暂停成功"})
    except Exception as e:
        logger.error(f"暂停自动评论异常: {e}")
        return err(40000, "操作失败", {"message": str(e)})



@router.get("/api/moment/settings")
async def get_moment_settings_endpoint():
    """获取朋友圈互动配置"""
    try:
        from src.utils.moment_config import get_moment_settings
        from src.crm.account_data import get_active_account
        return ok(get_moment_settings(get_active_account()))
    except Exception as e:
        logger.error(f"获取朋友圈配置异常: {e}")
        return err(40000, "操作失败", {"message": str(e)})


@router.post("/api/moment/settings")
async def save_moment_settings_endpoint(request: Request):
    """保存朋友圈互动配置"""
    try:
        body = await request.json()
        from src.utils.moment_config import get_moment_settings, save_moment_settings
        from src.crm.account_data import get_active_account
        account_id = get_active_account()

        current = get_moment_settings(account_id)
        current.update(body)
        save_moment_settings(current, account_id)
        return ok({"message": "保存成功"})
    except Exception as e:
        logger.error(f"保存朋友圈配置异常: {e}")
        return err(40000, "操作失败", {"message": str(e)})
