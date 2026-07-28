"""xm-bot4 优雅退出清理模块
========================

退出前必须还原的系统级修改：
1. SPI_SETSCREENREADER — 进程内设为 True，退出时必须设回 False，
   否则微信检测到"无主"的屏幕阅读器标志会触发风控。
2. UIBus worker 线程 — 停止命令总线。
3. 隐私遮罩 — 销毁覆盖在微信窗口上的 Win32 透明窗口。
4. 同步后端数据 — 抢救上报未发送的事件。
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_cleanup_done = False
_cleanup_lock = threading.Lock()


def reset_screen_reader_flag() -> None:
    """将 SPI_SETSCREENREADER 恢复为 False。

    startup_flow.py 的 force_accessibility_refresh 会设 True 让 Qt 吐出控件树，
    但这是系统全局标志，不还原会让微信误以为仍有辅助工具在监控。

    同时使用 SPIF_UPDATEINIFILE | SPIF_SENDCHANGE (3) 彻底清理：
    - 清除注册表/用户配置中的持久化值（老版本代码用 flags=3 写入过）
    - 广播变更通知所有进程
    """
    try:
        from src.uia.startup_flow import _we_set_screen_reader
        if not _we_set_screen_reader:
            return
    except (ImportError, AttributeError):
        # 导入失败时保守执行还原
        pass

    try:
        import ctypes
        SPI_SETSCREENREADER = 0x0047
        # flags=3: SPIF_UPDATEINIFILE(1) | SPIF_SENDCHANGE(2)
        # 彻底清理注册表持久值 + 广播通知
        ctypes.windll.user32.SystemParametersInfoW(
            SPI_SETSCREENREADER, False, None, 3
        )
        logger.info("[清理] SPI_SETSCREENREADER 已还原为 False")
    except Exception as e:
        logger.warning(f"[清理] 还原 SPI_SETSCREENREADER 失败: {e}")


def cleanup_uia_bus() -> None:
    """停止 UIBus worker 线程。"""
    try:
        from src.orchestrator.ui_bus import ui_bus
        ui_bus.stop(timeout=2.0)
        logger.info("[清理] UIBus worker 已停止")
    except Exception as e:
        logger.debug(f"[清理] UIBus 清理异常: {e}")


def cleanup_privacy_shield() -> None:
    """销毁隐私遮罩覆盖层。"""
    try:
        from src.uia.privacy_shield import get_privacy_shield
        shield = get_privacy_shield()
        if shield and getattr(shield, 'enabled', False):
            shield.destroy()
            logger.info("[清理] 隐私遮罩已销毁")
    except Exception as e:
        logger.debug(f"[清理] 隐私遮罩清理异常（可忽略）: {e}")


def graceful_cleanup() -> None:
    """主清理入口 — 退出前必须调用。

    幂等设计：多次调用只执行一次（防止 atexit + close_app 重复调用）。
    """
    global _cleanup_done
    with _cleanup_lock:
        if _cleanup_done:
            return
        _cleanup_done = True

    print("[清理] 正在执行优雅退出清理...")

    # 1. 最关键：还原系统级标志位（防止微信风控）
    reset_screen_reader_flag()

    # 2. 同步后端数据抢救上报
    try:
        from app.bootstrap import flush_cloud_before_exit
        flush_cloud_before_exit(max_batches=20)
    except Exception as e:
        logger.debug(f"[清理] 同步后端抢救上报异常: {e}")

    # 3. 停止 UIBus
    cleanup_uia_bus()

    # 4. 销毁隐私遮罩
    cleanup_privacy_shield()

    # 5. 强制垃圾回收以释放所有 COM/UIA 对象引用
    try:
        import gc
        gc.collect()
    except Exception:
        pass

    print("[清理] ✓ 优雅退出清理完成")
