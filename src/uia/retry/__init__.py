"""UI 操作重试与窗口控制公共能力（拆分自原 retry.py）"""

from .avatar_clipboard import capture_avatar_via_clipboard
from .clicks import (
    click_at_absolute,
    exists_with_timeout,
    physical_click,
    physical_double_click,
    physical_long_press,
    physical_right_click,
    random_delay,
    smooth_click_at,
    try_click,
    try_click_element,
    try_right_click,
)
from .scrolls import human_scroll
from .dpi import get_dpi_scale
from .keyboard import is_escape_pressed, is_shift_pressed
from .restart import exit_wechat_via_tray, restart_wechat_for_accessibility
from .tray import click_wechat_tray_icon
from .taskbar import click_wechat_taskbar_button
from .waits import wait_for_element, wait_for_window
from .window_ops import (
    close_wechat_ghost_windows,
    ensure_wechat_foreground,
    ensure_wechat_visible_for_automation,
    fix_white_screen_after_show,
    force_foreground,
    position_wechat_window,
    try_bring_wechat_to_front,
)

__all__ = [
    "capture_avatar_via_clipboard",
    "click_at_absolute",
    "click_wechat_taskbar_button",
    "click_wechat_tray_icon",
    "close_wechat_ghost_windows",
    "ensure_wechat_foreground",
    "ensure_wechat_visible_for_automation",
    "exists_with_timeout",
    "exit_wechat_via_tray",
    "fix_white_screen_after_show",
    "force_foreground",
    "get_dpi_scale",
    "human_scroll",
    "is_escape_pressed",
    "is_shift_pressed",
    "physical_click",
    "physical_double_click",
    "physical_long_press",
    "physical_right_click",
    "position_wechat_window",
    "random_delay",
    "restart_wechat_for_accessibility",
    "smooth_click_at",
    "try_bring_wechat_to_front",
    "try_click",
    "try_click_element",
    "try_right_click",
    "wait_for_element",
    "wait_for_window",
]

