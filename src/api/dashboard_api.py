"""
Dashboard 聚合接口 — 将首页所有数据源合并为单次请求
替代前端分别调用 /safety/daily-stats、/safety/rest-time、
/subscription/status、/subscription/features、/scheduler/status 的 5 次往返
"""
import asyncio
import logging

from fastapi import APIRouter
from src.utils.response import ok

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _collect_daily_stats() -> dict:
    """收集今日操作安全统计（复用 safety_api 逻辑）"""
    from src.api.safety_api import get_daily_stats_api
    try:
        resp = get_daily_stats_api()
        # get_daily_stats_api 返回的是 ok(stats) 格式
        if hasattr(resp, "body"):
            import json
            body = json.loads(resp.body)
            return body.get("data", {})
        return resp.get("data", {}) if isinstance(resp, dict) else {}
    except Exception as e:
        logger.warning(f"[Dashboard] daily_stats 采集失败: {e}")
        return {}


def _collect_rest_time() -> dict:
    """收集休息时间配置"""
    try:
        from src.utils.rest_time import get_rest_config, check_nominal_rest_time
        from src.crm.account_data import get_active_account
        account_id = get_active_account()
        config = get_rest_config(account_id)
        config["currently_resting"] = check_nominal_rest_time(account_id=account_id)
        try:
            from src.utils.config_cache import config_cache
            config["force_awake"] = config_cache.get("force_awake_override", False)
        except Exception:
            config["force_awake"] = False
        return config
    except Exception as e:
        logger.warning(f"[Dashboard] rest_time 采集失败: {e}")
        return {}


def _collect_subscription() -> dict:
    """收集订阅状态（同步 HTTP → xm-user，需线程池）"""
    try:
        from src.utils.license_validator import LicenseValidator
        return LicenseValidator.check_subscription()
    except Exception as e:
        logger.warning(f"[Dashboard] subscription 采集失败: {e}")
        return {}


def _collect_features() -> dict:
    """收集功能锁/配额信息"""
    try:
        from src.utils.license_validator import LicenseValidator
        return LicenseValidator.check_features()
    except Exception as e:
        logger.warning(f"[Dashboard] features 采集失败: {e}")
        return {}


def _collect_scheduler() -> dict:
    """收集调度器状态"""
    try:
        from src.scheduler.automation_scheduler import AutomationScheduler
        scheduler = AutomationScheduler.get_instance()
        return scheduler.get_status()
    except Exception as e:
        logger.warning(f"[Dashboard] scheduler 采集失败: {e}")
        return {}


def _collect_instances_count() -> int:
    """收集在线微信实例数"""
    try:
        from src.utils.instance_manager import InstanceManagerV2
        instances = InstanceManagerV2.get_instance().get_all_instances()
        return len(instances)
    except Exception as e:
        logger.debug(f"[Dashboard] instances_count 采集失败: {e}")
        return 0


@router.get("/overview")
async def dashboard_overview():
    """Dashboard 首页聚合接口：一次请求返回全部首页数据

    后端并行采集各子模块数据，对外网依赖（subscription/features）
    设独立超时降级，确保单模块故障不阻塞整个接口。
    """
    loop = asyncio.get_running_loop()

    # 将所有 CPU/IO 密集型任务提交到线程池并行执行
    # subscription 和 features 需要 HTTP→xm-user，是最慢的部分
    tasks = {
        "daily_stats": loop.run_in_executor(None, _collect_daily_stats),
        "rest_time": loop.run_in_executor(None, _collect_rest_time),
        "subscription": loop.run_in_executor(None, _collect_subscription),
        "features": loop.run_in_executor(None, _collect_features),
        "scheduler": loop.run_in_executor(None, _collect_scheduler),
        "instances_count": loop.run_in_executor(None, _collect_instances_count),
    }

    results = {}
    for key, task in tasks.items():
        try:
            # 每个子查询独立超时 5s，防止某个模块拖垮整体
            results[key] = await asyncio.wait_for(task, timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning(f"[Dashboard] {key} 采集超时(5s)，降级为空")
            results[key] = {} if key != "instances_count" else 0
        except Exception as e:
            logger.warning(f"[Dashboard] {key} 采集异常: {e}")
            results[key] = {} if key != "instances_count" else 0

    return ok(results)
