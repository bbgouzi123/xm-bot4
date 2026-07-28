"""
UIA WebSocket 前端通知工具（从 input_guard.py 拆分，保证 300 行规范）

提供 notify_frontend(action, message) 函数，
支持在任意线程（协程线程/子线程）安全地向前端广播 uia_lock 消息。
"""
import asyncio
import logging

logger = logging.getLogger(__name__)


def notify_frontend(action: str, message: str = "") -> None:
    """向前端广播 uia_lock 消息（支持跨线程安全调用）。

    策略优先级：
    1. 当前线程有 running event loop → ensure_future
    2. 子线程 + 全局主 Loop → run_coroutine_threadsafe
    3. 兜底 → 独立子 Loop（仅限无主 Loop 场景）
    """
    # 实时同步状态到 Win32 状态看板 (HUD/HDU 悬浮窗)
    try:
        from src.utils.status_overlay import status_overlay
        if action == "lock":
            status_overlay.update(
                status="物理锁定",
                detail=message or "自动化操作中，将锁定鼠标与键盘",
                friend="系统锁定",
                color=0x00A5FF,
                from_control_center=True,
                task_type="自动回复"
            )
        elif action == "status_update":
            status_overlay.update(
                status="物理锁定",
                detail=message,
                friend="系统锁定",
                color=0x00A5FF,
                from_control_center=True,
                task_type="自动回复"
            )
        elif action == "interrupted":
            status_overlay.update(
                status="已中断",
                detail=message or "操作已被用户中断 (ESC)",
                friend="系统锁定",
                color=0x3C3CFF,
                from_control_center=True,
                task_type="自动回复"
            )
        elif action == "unlock":
            status_overlay.update(
                status="就绪",
                detail=message or "等待系统指令...",
                friend="-",
                color=0x00DC00,
                from_control_center=True,
                task_type="自动回复"
            )
    except Exception as e_overlay:
        logger.debug(f"[UIAWsNotify] 同步更新 HUD 状态失败: {e_overlay}")

    try:
        from src.utils.websocket_manager import ws_manager
        payload = {"type": "uia_lock", "action": action, "message": message}

        loop = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

        if loop and loop.is_running():
            asyncio.ensure_future(ws_manager.broadcast(payload))
            return

        # 子线程：尝试全局主 Loop
        try:
            import app.state as app_state
            main_loop = getattr(app_state, "main_loop", None)
            if main_loop and main_loop.is_running():
                asyncio.run_coroutine_threadsafe(ws_manager.broadcast(payload), main_loop)
                return
        except Exception:
            pass

        # 兜底：独立子 Loop
        try:
            new_loop = asyncio.new_event_loop()
            new_loop.run_until_complete(ws_manager.broadcast(payload))
            new_loop.close()
        except Exception:
            pass
    except Exception as e:
        logger.exception(f"[UIAWsNotify] 通知前端失败: {e}")


def control_hud(action: str) -> None:
    """向前端发送 HUD 窗体控制指令（支持跨线程安全调用）。"""
    try:
        from src.utils.websocket_manager import ws_manager
        payload = {"type": "hud_control", "data": {"action": action}}

        loop = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

        if loop and loop.is_running():
            asyncio.ensure_future(ws_manager.broadcast(payload))
            return

        # 子线程：尝试全局主 Loop
        try:
            import app.state as app_state
            main_loop = getattr(app_state, "main_loop", None)
            if main_loop and main_loop.is_running():
                asyncio.run_coroutine_threadsafe(ws_manager.broadcast(payload), main_loop)
                return
        except Exception:
            pass

        # 兜底：独立子 Loop
        try:
            new_loop = asyncio.new_event_loop()
            new_loop.run_until_complete(ws_manager.broadcast(payload))
            new_loop.close()
        except Exception:
            pass
    except Exception as e:
        logger.exception(f"[UIAWsNotify] HUD控制通知失败: {e}")
