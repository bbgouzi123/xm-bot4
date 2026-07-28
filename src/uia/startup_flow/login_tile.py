"""
登录后窗口平铺工具（login_tile.py）

职责：扫码/快捷登录成功后，收集所有已存在的微信主界面 hwnd，
统一调用 tile_all_wechat_windows 重新铺排，消除窗口重叠。

本模块不依赖 UIA，只依赖 win32gui + window_utils，保持单一职责。
"""

import time
import win32gui

from .state import _enum_wechat_windows
from .utils import _log


def get_existing_main_hwnds(exclude_login_hwnd: int = 0) -> set:
    """获取当前所有已存在的主界面微信 hwnd 集合（用于后续排除旧窗口）。

    主界面标准：可见 + 宽 ≥ 500 + 高 ≥ 400
    """
    wins = _enum_wechat_windows()
    return {
        h for h, w, ht, vis in wins
        if vis and w >= 500 and ht >= 400 and h != exclude_login_hwnd
    }


def tile_windows_after_login(new_hwnd: int):
    """登录成功后重新平铺所有微信窗口，消除重叠。

    等待 1.5 秒让新窗口 DWM 完全渲染，再从 account_manager 收集全部已注册的
    可见主界面 hwnd（包括本次新登录的），统一调用 tile_all_wechat_windows。

    两级数据源：
    1. account_manager._instances（最精确，热注册的实例）
    2. 枚举系统窗口兜底（account_manager 可能尚未注册新实例）
    """
    try:
        time.sleep(1.5)  # 等待新窗口 DWM 完全渲染

        # ── 优先从 account_manager 拿最新 hwnds ──────────────────────────
        live_hwnds = []
        try:
            from app.state import account_manager as am
            live_hwnds = [
                h for h, inst in am._instances.items()
                if inst.driver.is_connected() and win32gui.IsWindowVisible(h)
            ]
        except Exception:
            pass

        # ── 兜底：枚举系统窗口 ────────────────────────────────────────────
        if not live_hwnds:
            wins = _enum_wechat_windows()
            live_hwnds = [h for h, w, ht, vis in wins if vis and w >= 500 and ht >= 400]

        # ── 确保本次新窗口在列表中 ────────────────────────────────────────
        if new_hwnd and new_hwnd not in live_hwnds:
            try:
                r = win32gui.GetWindowRect(new_hwnd)
                w, h = r[2] - r[0], r[3] - r[1]
                if w >= 500 and h >= 400:
                    live_hwnds.append(new_hwnd)
            except Exception:
                pass

        # ── 执行平铺 ──────────────────────────────────────────────────────
        if len(live_hwnds) > 1:
            from src.utils.window_utils import tile_all_wechat_windows
            tile_all_wechat_windows(live_hwnds)
            _log("登录", f"✅ 登录完成，已重新平铺 {len(live_hwnds)} 个微信窗口")
        elif len(live_hwnds) == 1:
            _log("登录", "ℹ️ 仅有 1 个主界面微信，无需平铺")

    except Exception as e:
        _log("登录", f"⚠️ 登录后平铺异常: {e}")
