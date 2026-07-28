"""
朋友圈巡游特有辅助工具函数（防范单文件超过 300 行规范）。
"""
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


def _is_rest_time(settings: dict) -> bool:
    """检查当前时刻是否在设定的休息时段内（15分钟精度）。

    对齐竞品 MomentCommentTask._is_rest_time 逻辑：
    - 将一天切分为 96 个时段（每 15 分钟一格）
    - start_interval / end_interval 以格为单位（0~95）
    - 跨午夜的时段（start > end）同样支持
    """
    from datetime import datetime
    try:
        rest_enabled = settings.get("rest_time_enabled", False)
        if not rest_enabled:
            return False
        # start/end 以「15分钟格」为单位（0-95），与竞品完全一致
        start_interval = int(settings.get("rest_time_start", 0))
        end_interval   = int(settings.get("rest_time_end", 28))
        now = datetime.now()
        current_interval = now.hour * 4 + now.minute // 15
        if start_interval <= end_interval:
            return start_interval <= current_interval <= end_interval
        else:
            # 跨午夜：如 20:00(80) ~ 08:00(32)
            return current_interval >= start_interval or current_interval <= end_interval
    except Exception as e:
        logger.debug(f"[休息时间] 检测失败，默认不限制: {e}")
        return False


def _parse_patrol_settings(settings: dict, account_id: str) -> dict:
    """解析并提取朋友圈巡游的所有控制与概率参数，防范 moment_interactor.py 代码超行数。"""
    daily_like_limit    = int(settings.get("daily_like_limit", 30))
    daily_comment_limit = int(settings.get("daily_comment_limit", 10))
    try:
        from src.utils.daily_counter import DailyCounter
        DailyCounter().update_limits({
            "like":    daily_like_limit,
            "comment": daily_comment_limit,
        })
    except Exception:
        pass

    like_enabled    = settings.get("like_enabled", True)
    comment_enabled = settings.get("comment_enabled", True)
    skip_self       = settings.get("skip_self_moments", True)
    skip_ads        = settings.get("skip_ads", True)

    like_prob    = float(settings.get("like_probability", 0.3))
    comment_prob = float(settings.get("comment_probability", 0.15))

    per_friend_limit = int(settings.get("per_friend_limit", settings.get("perFriendLimit", 3)))
    interaction_mode = settings.get("interactionMode", "")
    if interaction_mode:
        should_like    = interaction_mode in ("like_only", "like_and_comment")
        should_comment = interaction_mode in ("comment_only", "like_and_comment")
    else:
        should_like    = like_enabled
        should_comment = comment_enabled

    comment_limit = int(settings.get("comment_limit", settings.get("commentLimit", daily_comment_limit)))
    max_screens = int(settings.get("max_screens", settings.get("scroll_count", 50)))

    return {
        "should_like": should_like,
        "should_comment": should_comment,
        "like_prob": like_prob,
        "comment_prob": comment_prob,
        "daily_like_limit": daily_like_limit,
        "daily_comment_limit": daily_comment_limit,
        "per_friend_limit": per_friend_limit,
        "comment_limit": comment_limit,
        "max_screens": max_screens,
        "skip_self": skip_self,
        "skip_ads": skip_ads,
    }


def _check_and_reply_interactions_safe(manager: Any, sns_window: Any, settings: dict, account_id: str):
    """检查并回复互动消息，包裹异常保护以防止污染 moment_interactor.py 的行数。"""
    try:
        from src.uia.input_guard import uia_lock
        uia_lock.update_status("💬 检查是否有需要回复的互动消息...")
        from src.monitor.moment_reply_monitor import check_and_reply_interactions
        replied = check_and_reply_interactions(
            manager, sns_window, settings, account_id
        )
        if replied:
            logger.info(f"[互动回复] 本轮回复了 {replied} 条互动消息")
    except Exception as e:
        logger.warning(f"[互动回复] 互动消息回复异常: {e}")



def _find_toast(sns_window):
    """查找赞/评论浮层面板。

    新版微信: mmui::TimelineFloatMenu（顶层 WindowControl，全局搜索）
    旧版微信: SnsLikeToastWnd（PaneControl，从 sns_window 搜索）

    使用 safe_exists / safe_walk_control 防止 COM 断连导致进程崩溃。
    """
    import uiautomation as uia
    from src.utils.safe_uia import safe_exists, safe_walk_control
    # 新版微信（实测 ClassName = mmui::TimelineFloatMenu）
    tw = uia.WindowControl(ClassName='mmui::TimelineFloatMenu')
    if safe_exists(tw, 2.0):
        return tw
    # 旧版兼容
    tw = sns_window.PaneControl(ClassName='SnsLikeToastWnd')
    if safe_exists(tw, 1.0):
        return tw
    # 兜底：遍历找含"赞"按钮的控件（使用安全遍历，防止 COM 崩溃）
    try:
        for p, _ in safe_walk_control(sns_window, max_depth=4):
            if getattr(p, 'ControlTypeName', '') in ('PaneControl', 'WindowControl'):
                if safe_exists(p.ButtonControl(Name='赞'), 0.2):
                    return p
    except Exception:
        pass
    return tw


def trigger_risk_alert_safe(account_id: str, err: Exception):
    """发送防封风险报警"""
    try:
        from src.utils.alert_notifier import alert_notifier
        import win32gui, asyncio
        hwnd = win32gui.FindWindow(None, '朋友圈') or win32gui.FindWindow('WeChatMainWndForPC', None) or 0
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(
            alert_notifier.trigger_risk_alert(
                machine_code="Local-PC",
                account_id=account_id,
                reason=f"朋友圈连续发生严重异常。报错: {err}",
                is_fatal=True,
                hwnd=hwnd
            )
        )
        loop.close()
    except Exception as ae:
        logger.error(f"[朋友圈风控] 发送风险告警失败: {ae}")


def open_moments(driver) -> Any:
    """打开朋友圈并置顶最大化"""
    import win32gui as _w32
    from src.monitor.moment_utils import maximize_moment_window
    
    # 优先复用驱动底座的高兼容性朋友圈打开零件，避免在巡游流程中重复手写 UIA 侧边栏按钮查找逻辑
    sns_window = driver._open_moments_window()
    if sns_window:
        hwnd = getattr(sns_window, 'NativeWindowHandle', 0)
        if not hwnd or not _w32.IsWindow(hwnd):
            hwnd = _w32.FindWindow('mmui::SNSWindow', None) or _w32.FindWindow(None, '朋友圈')
        if hwnd and _w32.IsWindow(hwnd):
            try:
                _w32.SetForegroundWindow(hwnd)
                maximize_moment_window(hwnd)
            except Exception:
                pass
            import time as _t
            _t.sleep(0.3)
        return sns_window
    return None

