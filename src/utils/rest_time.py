"""
xm-bot4引擎：全局休息时间守卫 — 碾压 xm-bot4 的"局部防线"

xm-bot4 的 _is_rest_time() 只写在了 friend_request_adapter 和 add_friend_adapter 里，
自动回复、朋友圈发布、点赞评论等高风险操作在深夜照跑——这是致命漏洞。

我们的设计：
    - 全局装饰器，一行代码保护所有自动化入口
    - 支持工作日/周末分别配置
    - 支持按操作类型粒度控制（某些操作允许深夜，比如静默加好友）
    - 配置从 SQLite 热加载，前端改了立即生效
"""

import datetime
import json
import logging
import functools
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


# 默认休息时间配置
DEFAULT_REST_CONFIG = {
    "enabled": True,
    # 人工回复后挂起自动回复时长 (分钟)
    "manual_suspend_minutes": 30,
    # 启用防爆免回复词白名单
    "ignore_reply_whitelist": False,
    # 工作日（周一至周五）
    "weekday_start": "23:00",
    "weekday_end": "07:00",
    # 周末（周六至周日）
    "weekend_start": "23:00",
    "weekend_end": "09:00",
    # 哪些操作类型受休息时间约束（全部受约束）
    "protected_actions": [
        "like",             # 朋友圈点赞
        "comment",          # 朋友圈评论
        "moment_post",      # 发朋友圈
        "auto_reply",       # 自动回复
        "add_friend",       # 添加好友
        "friend_request",   # 自动通过好友
        "patrol",           # 朋友圈巡游
    ],
}


def _parse_time(time_str: str) -> datetime.time:
    """解析 HH:MM 时间字符串"""
    try:
        parts = time_str.split(":")
        return datetime.time(int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        return datetime.time(23, 0)  # 解析失败默认23:00


def _load_rest_config(account_id: str = None) -> dict:
    """从内存缓存加载休息时间配置"""
    try:
        from src.utils.config_cache import config_cache
        cached = config_cache.get("rest_time_settings")
        if cached and isinstance(cached, dict):
            return cached
    except Exception:
        pass
    return DEFAULT_REST_CONFIG


def check_nominal_rest_time(action_type: str = None, account_id: str = None) -> bool:
    """仅检查时间范围约束，不受强行唤醒等干预"""
    config = _load_rest_config(account_id)

    # 功能总开关
    if not config.get("enabled", True):
        return False

    # 检查操作类型是否受保护
    if action_type:
        protected = config.get("protected_actions", DEFAULT_REST_CONFIG["protected_actions"])
        if action_type not in protected:
            return False

    now = datetime.datetime.now()
    current_time = now.time()
    is_weekend = now.weekday() >= 5  # 周六=5, 周日=6

    # 根据工作日/周末获取休息区间
    if is_weekend:
        start = _parse_time(config.get("weekend_start", "23:00"))
        end = _parse_time(config.get("weekend_end", "09:00"))
    else:
        start = _parse_time(config.get("weekday_start", "23:00"))
        end = _parse_time(config.get("weekday_end", "07:00"))

    # 跨午夜判断：如 23:00 ~ 07:00
    if start <= end:
        in_rest = start <= current_time <= end
    else:
        in_rest = current_time >= start or current_time <= end

    return in_rest

_last_rest_log_ts = {}

def is_rest_time(action_type: str = None, account_id: str = None, verbose: bool = False) -> bool:
    """检查当前是否在休息时间内
    支持强制唤醒的逻辑覆盖
    """
    try:
        from src.utils.config_cache import config_cache
        force_awake = config_cache.get("force_awake_override", False)
    except:
        force_awake = False

    # 若用户已设置强行唤醒，不论处于任何休息时段，均无条件放行且永不擅自重置
    if force_awake:
        if verbose:
            logger.info(f"[休息守卫] 当前已被强制唤醒，放行操作 {action_type or ''}")
        return False

    in_rest = check_nominal_rest_time(action_type, account_id)
    if in_rest and verbose:
        import time
        now = time.time()
        key = (action_type, account_id)
        last_ts = _last_rest_log_ts.get(key, 0.0)
        if now - last_ts > 300.0:
            _last_rest_log_ts[key] = now
            msg = f"[休息守卫 ⏰] 当前在防封休眠策略限制时间内，操作 {action_type or ''} → 阻拦跳过执行 (如需在深夜调试，请在前端设置中关闭防封休眠或调整休息时段)"
            logger.info(msg)
            print(msg)

    return in_rest


def save_rest_config(config: dict, account_id: str = None):
    """保存休息时间配置（内存缓存 + 同步后端持久化）

    Args:
        config: 配置字典（格式同 DEFAULT_REST_CONFIG）
        account_id: 微信账号标识
    """
    try:
        from src.utils.config_cache import config_cache
        config_cache.set("rest_time_settings", config)
        logger.info(f"[休息守卫] 配置已保存: {config}")
    except Exception as e:
        logger.error(f"[休息守卫] 保存配置失败: {e}")
        raise


def get_rest_config(account_id: str = None) -> dict:
    """获取当前休息时间配置（供前端读取）"""
    return _load_rest_config(account_id)


# ===== 装饰器：一行代码保护自动化函数 =====

def rest_guard(action_type: str):
    """装饰器：为自动化函数添加休息时间保护

    使用方式:
        @rest_guard("like")
        def auto_like_moment(self, ...):
            ...   # 休息时间内不会执行

        @rest_guard("auto_reply")
        async def handle_auto_reply(self, ...):
            ...   # 支持异步函数
    """
    def decorator(func):
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            # 尝试从参数/self中获取 account_id
            acct = _extract_account_id(args, kwargs)
            if is_rest_time(action_type, acct, verbose=True):
                logger.info(
                    f"[休息守卫] {func.__name__} 因休息时间被跳过"
                )
                return None
            return func(*args, **kwargs)

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            acct = _extract_account_id(args, kwargs)
            if is_rest_time(action_type, acct, verbose=True):
                logger.info(
                    f"[休息守卫] {func.__name__} 因休息时间被跳过"
                )
                return None
            return await func(*args, **kwargs)

        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    return decorator


def _extract_account_id(args, kwargs) -> Optional[str]:
    """智能提取 account_id：从 self.bot_wxid / kwargs / 默认值"""
    # 1. 尝试从 kwargs 获取
    if 'account_id' in kwargs:
        return kwargs['account_id']
    # 2. 尝试从 self (第一个参数) 获取
    if args:
        obj = args[0]
        for attr in ('bot_wxid', 'account_id', '_active_wxid'):
            if hasattr(obj, attr):
                return getattr(obj, attr)
        # 尝试从 self.driver 获取
        if hasattr(obj, 'driver') and hasattr(obj.driver, 'bot_wxid'):
            return obj.driver.bot_wxid
    return None
