"""
UIA 引擎熔断与状态管理模块
"""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

_state_lock = threading.Lock()
_session_fail_count: dict[str, int] = {}
_session_fused_time: dict[str, float] = {}  # 记录会话进入熔断的时间戳
_global_fail_count = 0
_engine_suspended = False


def report_uia_success(session_id: str = None):
    """重置局部与全局异常熔断计数"""
    global _global_fail_count
    with _state_lock:
        _global_fail_count = 0
        if session_id:
            if session_id in _session_fail_count:
                _session_fail_count[session_id] = 0
            if session_id in _session_fused_time:
                _session_fused_time.pop(session_id, None)


def report_uia_failure(session_id: str = None) -> bool:
    """增加失败计数，达到阀值时激活熔断并进行 WS 前端警报广播"""
    global _global_fail_count, _engine_suspended
    with _state_lock:
        _global_fail_count += 1
        if session_id:
            _session_fail_count[session_id] = _session_fail_count.get(session_id, 0) + 1

        # 1. 检测全局熔断（引擎暂停挂起）
        if _global_fail_count >= 5 and not _engine_suspended:
            _engine_suspended = True
            logger.critical("[UIA熔断] 全局 UIA 连续失败达到 5 次！主动挂起整个自动回复与跟进引擎")
            asyncio.create_task(_broadcast_warning("global_suspended", "全局 UIA 控制已连续失败 5 次，微信或物理窗口疑遭遇不可恢复卡死/断线，系统已挂起引擎。"))

        # 2. 检测会话级熔断
        if session_id and _session_fail_count.get(session_id, 0) >= 3:
            if session_id not in _session_fused_time:
                _session_fused_time[session_id] = datetime.now().timestamp()
            logger.warning(f"[UIA熔断] 会话 {session_id} 连续失败达到 3 次，该客户回复触发临时拦截过滤")
            asyncio.create_task(_broadcast_warning("session_fused", f"客户 {session_id} 连续发送失败 3 次已临时熔断屏蔽"))
            return True
        return False


def is_engine_suspended() -> bool:
    """引擎是否被挂起保护"""
    with _state_lock:
        return _engine_suspended


def is_session_fused(session_id: str) -> bool:
    """单会话是否被熔断屏蔽"""
    with _state_lock:
        return _session_fail_count.get(session_id, 0) >= 3


def resume_engine():
    """人工从前端或API重启引擎并清除计数状态"""
    global _global_fail_count, _engine_suspended
    with _state_lock:
        _global_fail_count = 0
        _engine_suspended = False
        _session_fail_count.clear()
        _session_fused_time.clear()
        logger.info("[UIA熔断] 全局引擎人工重置恢复成功")
        asyncio.create_task(_broadcast_warning("engine_resumed", "UIA 引擎状态已重置恢复正常运作"))


def suspend_engine(reason: str = "manual"):
    """挂起自动回复与跟进引擎"""
    global _engine_suspended
    with _state_lock:
        _engine_suspended = True
        logger.warning(f"[UIA熔断] 全局引擎已手动挂起: {reason}")
        asyncio.create_task(_broadcast_warning("global_suspended", f"引擎已通过指令手动挂起: {reason}"))


def get_fused_sessions() -> list[dict]:
    """获取当前所有被熔断的会话列表"""
    with _state_lock:
        results = []
        for session_id, count in list(_session_fail_count.items()):
            if count >= 3:
                fused_at = _session_fused_time.get(session_id, 0.0)
                results.append({
                    "session_id": session_id,
                    "fail_count": count,
                    "fused_at": fused_at
                })
        return results


def reset_session_fuse(session_id: str):
    """清除指定会话的熔断状态"""
    global _global_fail_count
    with _state_lock:
        _global_fail_count = 0
        if session_id in _session_fail_count:
            _session_fail_count[session_id] = 0
        if session_id in _session_fused_time:
            _session_fused_time.pop(session_id, None)
        logger.info(f"[UIA熔断] 会话 {session_id} 熔断状态已被手动重置")
        asyncio.create_task(_broadcast_warning("session_resumed", f"会话 {session_id} 熔断已恢复"))


async def _broadcast_warning(alert_type: str, message: str):
    """广播断线与卡死熔断预警到前端"""
    try:
        from src.utils.websocket_manager import ws_manager
        await ws_manager.broadcast_json({
            "type": "uia_warning",
            "alert_type": alert_type,
            "message": message,
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"[UIA熔断] 广播预警信息失败: {e}")

