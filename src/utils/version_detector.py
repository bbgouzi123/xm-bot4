"""微信 PC 客户端版本探测（用于 UIA 策略分支）。"""
from __future__ import annotations

import os
from enum import Enum
from typing import Optional

try:
    import uiautomation as auto
except Exception:
    auto = None
try:
    import psutil
except Exception:
    psutil = None
try:
    import win32process
except Exception:
    win32process = None


class WeChatVersion(Enum):
    LEGACY_3_9 = "3.9.x"
    MODERN_4_1 = "4.1.x"


def detect_version(window_handle: Optional[int] = None) -> WeChatVersion:
    """
    Best-effort WeChat version detection.

    - 环境变量 ``WECHAT_AUTOMATION_MODE`` 为 ``legacy`` / ``pyweixin`` 时优先采用。
    - 有窗口句柄时尝试根据进程名与「会话」列表控件判断。
    - 否则根据全局进程扫描或主窗口探测。
    """
    mode = (os.environ.get("WECHAT_AUTOMATION_MODE") or "").strip().lower()
    if mode == "legacy":
        return WeChatVersion.LEGACY_3_9
    if mode == "pyweixin":
        return WeChatVersion.MODERN_4_1

    if window_handle is not None and win32process is not None and psutil is not None:
        try:
            _, pid = win32process.GetWindowThreadProcessId(int(window_handle))
            if pid:
                name = (psutil.Process(pid).name() or "").lower()
                if "wechat.exe" in name:
                    return WeChatVersion.LEGACY_3_9
                if "weixin.exe" in name:
                    return WeChatVersion.MODERN_4_1
        except Exception:
            pass

        if auto is not None:
            try:
                wnd = auto.ControlFromHandle(window_handle)
                if wnd and wnd.Exists(0, 0):
                    session_list = wnd.ListControl(Name="会话")
                    if session_list and session_list.Exists(0, 0):
                        return WeChatVersion.LEGACY_3_9
            except Exception:
                pass
        return WeChatVersion.MODERN_4_1

    if psutil is not None:
        found_weixin = False
        found_wechat = False
        for proc in psutil.process_iter(["name"]):
            try:
                name = (proc.info.get("name") or "").lower()
            except Exception:
                continue
            if not name:
                continue
            if "weixin.exe" in name:
                found_weixin = True
            if "wechat.exe" in name:
                found_wechat = True
        if found_weixin and not found_wechat:
            return WeChatVersion.MODERN_4_1
        if found_wechat and not found_weixin:
            return WeChatVersion.LEGACY_3_9

    if auto is None:
        return WeChatVersion.MODERN_4_1
    try:
        wnd = auto.WindowControl(ClassName="WeChatMainWndForPC")
        if not wnd or not wnd.Exists(0, 0):
            return WeChatVersion.MODERN_4_1
        session_list = wnd.ListControl(Name="会话")
        if session_list and session_list.Exists(0, 0):
            return WeChatVersion.LEGACY_3_9
    except Exception:
        pass
    return WeChatVersion.MODERN_4_1
