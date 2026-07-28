"""
微信智能启动流程 — startup_flow
===================================

统一入口，覆盖所有启动场景分支：

  ① 环境变量检测/注入
  ② 微信进程检测
  ③ 窗口可见性判断
  ④ 登录/主界面判断
  ⑤ "进入微信"按钮 / 扫码等待
  ⑥ 窗口置前（force_focus_window）
  ⑦ Qt Accessibility 树刷新
  ⑧ 导航栏查找
  ⑨ 账号信息提取（由 driver 负责）

用法:
    from src.uia.startup_flow import ensure_wechat_ready
    hwnd = ensure_wechat_ready()   # 返回已就绪的微信主窗口 hwnd，或 None
"""
from .flow import ensure_wechat_ready
from .flow_multi import ensure_all_wechat_ready
from .version import detect_wechat_version, is_wechat_version_compatible, format_version
from .env import check_qt_accessibility_injected, inject_qt_accessibility
from .state import detect_wechat_state
from .window_ops import force_focus_window, simulate_wechat_show_hotkey, nudge_window
from .login import handle_login_window
from .process import exit_wechat_via_tray, kill_wechat, wait_wechat_exit
from .launch import launch_wechat
from .refresh import force_accessibility_refresh, _we_set_screen_reader
from .narrator import start_narrator, stop_narrator
from .toolbar import find_nav_toolbar

__all__ = [
    "ensure_wechat_ready",
    "ensure_all_wechat_ready",
    "detect_wechat_version",
    "is_wechat_version_compatible",
    "format_version",
    "check_qt_accessibility_injected",
    "inject_qt_accessibility",
    "detect_wechat_state",
    "force_focus_window",
    "simulate_wechat_show_hotkey",
    "nudge_window",
    "handle_login_window",
    "exit_wechat_via_tray",
    "kill_wechat",
    "wait_wechat_exit",
    "launch_wechat",
    "force_accessibility_refresh",
    "_we_set_screen_reader",
    "start_narrator",
    "stop_narrator",
    "find_nav_toolbar",
]
