import asyncio
import logging

logger = logging.getLogger(__name__)

# 全局驱动与任务状态
_driver = None
_task_state = {
    "running": False,
    "paused": False,
    "config": {},
    "progress": {"total": 0, "processed": 0, "succeeded": 0, "failed": 0},
}

_loaded_wxids = set()

def _get_effective_wxid() -> str:
    try:
        from src.crm.account_data import get_active_account
        active_wxid = get_active_account()
        if active_wxid and active_wxid != "default":
            return active_wxid
    except Exception:
        pass
    return "default"

def save_task_state_to_db():
    try:
        from src.utils.config_cache import config_cache
        wxid = _get_effective_wxid()
        key = f"add_friend_task_state_{wxid}"
        config_cache.set(key, _task_state, sync_cloud=True)
        logger.info(f"[一键加人] 成功保存状态到 config_cache, key={key}")
    except Exception as e:
        logger.error(f"[一键加人] 保存状态到 config_cache 异常: {e}")

def try_restore_task_state():
    global _loaded_wxids
    wxid = _get_effective_wxid()
    if wxid in _loaded_wxids:
        return
    _loaded_wxids.add(wxid)

    try:
        from src.utils.config_cache import config_cache
        key = f"add_friend_task_state_{wxid}"
        
        try:
            config_cache.load_from_cloud(clear_before_load=False)
        except Exception as e:
            logger.debug(f"[一键加人] 拉取云端配置失败: {e}")
            
        saved = config_cache.get(key)
        if saved and isinstance(saved, dict) and saved.get("running"):
            logger.info(f"[一键加人] 发现并成功恢复云端未完成的任务状态: {saved}")
            _task_state.clear()
            _task_state.update(saved)
            
            cfg = saved.get("config", {})
            if "group_name" in cfg:
                from src.api.add_friend_api.group_task_service import _run_group_add_friend_loop
                from src.api.add_friend_api.models import StartGroupTaskRequest
                req = StartGroupTaskRequest(**cfg)
                asyncio.create_task(_run_group_add_friend_loop(req))
            else:
                from .task_engine import _run_add_friend_loop
                asyncio.create_task(_run_add_friend_loop())
    except Exception as e:
        logger.error(f"[一键加人] 恢复云端任务状态异常: {e}")

def _get_effective_driver():
    global _driver
    try:
        from src.crm.account_data import get_active_account
        from app.state import account_manager
        active_wxid = get_active_account()
        if active_wxid and active_wxid != "default":
            inst = account_manager.get_instance_by_wxid(active_wxid)
            if inst and inst.driver:
                return inst.driver
    except Exception as e:
        logger.warning(f"[一键加人] 动态获取活动微信驱动失败: {e}")
    return _driver

def init_driver(d):
    global _driver
    _driver = d
    try:
        from src.api.config_api.base_config import _load_configs
        configs = _load_configs()
        enabled = configs.get("active_warmup", False)
        _task_state["config"]["active_warmup"] = enabled
        logger.info(f"[一键托管] 初始化加载配置: active_warmup={enabled}")
    except Exception as e:
        logger.error(f"[一键托管] 初始化加载配置异常: {e}")
    try:
        from .warmup_service import ensure_warmup_idle_daemon_started
        from .schedule_publish_daemon import ensure_schedule_publish_daemon_started
        ensure_warmup_idle_daemon_started(_driver, _task_state)
        ensure_schedule_publish_daemon_started(_driver)
    except Exception as e:
        logger.error(f"启动空闲托管守护失败: {e}")
