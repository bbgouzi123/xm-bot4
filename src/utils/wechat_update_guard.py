"""
wechat_update_guard.py
微信自动更新弹窗拦截守护

功能：
  在后台以低频轮询（默认每 15 秒）扫描是否存在微信弹出的"新版本"升级对话框，
  一旦检测到，立即通过 UIA Invoke 点击"忽略本次更新"按钮，无需物理鼠标移动。

设计原则：
  - 零侵入：独立后台线程，不占用 UIA 线程池，不影响正在进行的聊天回复流程
  - 低频轮询：仅枚举顶层窗口句柄（纯 Win32 API，无需刷新 UIA 树），轻量到可忽略
  - 安全点击：使用 UIA InvokePattern，不移动物理鼠标，不产生键盘事件
  - 幂等：多次检测同一弹窗只点击一次（通过 hwnd 去重），防止误操作
  - 自愈：出现任何异常均静默忽略，不影响主流程
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import threading
import time
from typing import Optional, Set

logger = logging.getLogger(__name__)

# ─── Windows API 常量 ────────────────────────────────────────────────────────
_ENUM_WINDOWS_PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
_GW_CHILD = 5

# 微信更新弹窗的 Win32 窗口类名（从 UIA dump 得到）
_WECHAT_UPDATE_WINDOW_CLASS = "Qt51514QWindowIcon"

# 弹窗标题文本（Name 属性）和目标按钮文本
_UPDATE_DIALOG_TITLE_TEXT = "新版本"
_IGNORE_BUTTON_TEXT      = "忽略本次更新"

# 守护线程扫描间隔（秒）—— 15s 一次完全无感知
_SCAN_INTERVAL_SEC = 15.0

# 防重复点击冷却（秒）
_DISMISS_COOLDOWN_SEC = 60.0


class WeChatUpdateGuard:
    """
    微信自动更新弹窗静默拦截守护服务。

    调用 start() 在后台启动，调用 stop() 停止。
    """

    def __init__(self, scan_interval: float = _SCAN_INTERVAL_SEC):
        self._interval = scan_interval
        self._running = False
        self._thread: Optional[threading.Thread] = None
        # 已处理过的弹窗 hwnd 集合（附带时间戳，用于冷却到期后清理）
        self._dismissed_hwnds: dict[int, float] = {}
        self._lock = threading.Lock()

    # ─── 公共接口 ─────────────────────────────────────────────────────────────

    def start(self):
        """启动守护线程"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="WeChatUpdateGuard"
        )
        self._thread.start()
        logger.info("[更新弹窗守护] 已启动 (扫描间隔 %.0fs)", self._interval)

    def stop(self):
        """停止守护线程"""
        self._running = False
        logger.info("[更新弹窗守护] 已停止")

    # ─── 后台循环 ─────────────────────────────────────────────────────────────

    def _run_loop(self):
        # 延迟首次扫描，等待程序完全就绪
        time.sleep(10.0)
        while self._running:
            try:
                self._scan_and_dismiss()
            except Exception as e:
                logger.debug("[更新弹窗守护] 扫描异常（已忽略）: %s", e)
            time.sleep(self._interval)

    def _scan_and_dismiss(self):
        """枚举顶层窗口，查找微信更新弹窗并点击忽略"""
        candidate_hwnds = self._find_update_dialog_hwnds()
        if not candidate_hwnds:
            return

        now = time.time()
        for hwnd in candidate_hwnds:
            # 冷却检查：同一弹窗在冷却期内不重复操作
            with self._lock:
                last_dismiss = self._dismissed_hwnds.get(hwnd, 0.0)
                if now - last_dismiss < _DISMISS_COOLDOWN_SEC:
                    continue

            dismissed = self._click_ignore_button(hwnd)
            if dismissed:
                with self._lock:
                    self._dismissed_hwnds[hwnd] = now
                    # 清理过期的冷却记录，防止字典无限增长
                    expired = [h for h, t in self._dismissed_hwnds.items()
                               if now - t > _DISMISS_COOLDOWN_SEC * 2]
                    for h in expired:
                        self._dismissed_hwnds.pop(h, None)

    # ─── 窗口枚举 ─────────────────────────────────────────────────────────────

    def _find_update_dialog_hwnds(self) -> list[int]:
        """
        通过 EnumWindows 枚举所有顶层窗口，
        筛选出属于微信更新弹窗的 hwnd 列表。

        判断标准（双重保险）：
          1. 窗口类名为 Qt51514QWindowIcon
          2. 窗口内存在名为"新版本"的 UIA TextControl（轻量 FindFirst 即可）
        """
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # 先获取当前所有微信进程的 PID 集合
        wechat_pids = self._get_wechat_pids()
        if not wechat_pids:
            return []

        candidates = []

        def _enum_cb(hwnd: int, _lp: int) -> bool:
            try:
                # 1. 快速过滤：检查窗口是否可见
                if not user32.IsWindowVisible(hwnd):
                    return True

                # 2. 检查 PID 是否属于微信进程
                pid = wt.DWORD(0)
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value not in wechat_pids:
                    return True

                # 3. 检查窗口类名
                buf = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, buf, 256)
                if buf.value != _WECHAT_UPDATE_WINDOW_CLASS:
                    return True

                # 4. 用 UIA 轻量检测：查找"新版本"文本控件
                if self._has_new_version_text(hwnd):
                    candidates.append(hwnd)
            except Exception:
                pass
            return True

        cb = _ENUM_WINDOWS_PROC(_enum_cb)
        user32.EnumWindows(cb, 0)
        return candidates

    @staticmethod
    def _get_wechat_pids() -> Set[int]:
        """获取所有微信进程的 PID 集合（支持多开）"""
        try:
            import subprocess
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=3,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            pids: Set[int] = set()
            for line in result.stdout.splitlines():
                parts = line.split(",")
                if len(parts) >= 2:
                    pid_str = parts[1].replace('"', '').strip()
                    if pid_str.isdigit():
                        pids.add(int(pid_str))
            return pids
        except Exception:
            return set()

    @staticmethod
    def _has_new_version_text(hwnd: int) -> bool:
        """
        用 UIA 轻量查找：该窗口是否包含 Name="新版本" 的 TextControl。
        使用 searchDepth=6 限制遍历深度，防止触发全树扫描卡死。
        """
        try:
            import uiautomation as uia
            # 以 hwnd 为根，不依赖全局 desktop 树，避免全树刷新
            wnd_ctrl = uia.ControlFromHandle(hwnd)
            if not wnd_ctrl:
                return False
            # FindFirst：Name="新版本" 的文本控件，深度限制 6
            text_ctrl = wnd_ctrl.TextControl(Name=_UPDATE_DIALOG_TITLE_TEXT, searchDepth=6)
            return text_ctrl.Exists(0.05)
        except Exception:
            return False

    # ─── 按钮点击 ─────────────────────────────────────────────────────────────

    def _click_ignore_button(self, hwnd: int) -> bool:
        """
        在指定弹窗 hwnd 中找到"忽略本次更新"文本控件，
        优先使用 UIA InvokePattern（无物理移动），
        兜底使用父级 ButtonControl 的 Click()。

        返回 True 表示成功点击。
        """
        try:
            import uiautomation as uia

            wnd_ctrl = uia.ControlFromHandle(hwnd)
            if not wnd_ctrl:
                logger.warning("[更新弹窗守护] 无法从 hwnd=0x%X 获取 UIA 控件", hwnd)
                return False

            # 策略 1：直接找 Name="忽略本次更新" 的文本控件（从 UIA dump 确认存在）
            #         该控件的 InvokePattern.IsAvailable = True，可直接调用
            ignore_ctrl = wnd_ctrl.TextControl(
                Name=_IGNORE_BUTTON_TEXT, searchDepth=8
            )
            if ignore_ctrl.Exists(0.1):
                try:
                    ignore_ctrl.GetInvokePattern().Invoke()
                    logger.info(
                        "[更新弹窗守护] ✅ 已通过 InvokePattern 点击'忽略本次更新' "
                        "(hwnd=0x%X)", hwnd
                    )
                    return True
                except Exception as invoke_ex:
                    logger.debug("[更新弹窗守护] InvokePattern 失败: %s，尝试父级按钮", invoke_ex)

            # 策略 2：找父级 ButtonControl（Ancestors 显示 Text → 按钮），再对按钮调用 Click
            btn_ctrl = wnd_ctrl.ButtonControl(searchDepth=6)
            while btn_ctrl.Exists(0.05):
                if _IGNORE_BUTTON_TEXT in (btn_ctrl.Name or ""):
                    btn_ctrl.Click(simulateMove=False)
                    logger.info(
                        "[更新弹窗守护] ✅ 已通过父级 ButtonControl.Click() 点击'忽略本次更新' "
                        "(hwnd=0x%X)", hwnd
                    )
                    return True
                btn_ctrl = btn_ctrl.GetNextSiblingControl()

            # 策略 3：兜底 —— 用 LegacyIAccessible.DoDefaultAction（日志显示 DefaultAction="按"）
            ignore_ctrl2 = wnd_ctrl.TextControl(Name=_IGNORE_BUTTON_TEXT, searchDepth=8)
            if ignore_ctrl2.Exists(0.1):
                try:
                    acc = ignore_ctrl2.GetLegacyIAccessiblePattern()
                    if acc:
                        acc.DoDefaultAction()
                        logger.info(
                            "[更新弹窗守护] ✅ 已通过 LegacyIAccessible.DoDefaultAction 点击'忽略本次更新' "
                            "(hwnd=0x%X)", hwnd
                        )
                        return True
                except Exception:
                    pass

            logger.warning(
                "[更新弹窗守护] ⚠️ 在 hwnd=0x%X 中未找到可点击的'忽略本次更新'控件", hwnd
            )
            return False

        except Exception as e:
            logger.debug("[更新弹窗守护] 点击异常: %s", e)
            return False


# ─── 全局单例 ────────────────────────────────────────────────────────────────

_guard_instance: Optional[WeChatUpdateGuard] = None


def get_update_guard() -> WeChatUpdateGuard:
    """获取全局守护单例"""
    global _guard_instance
    if _guard_instance is None:
        _guard_instance = WeChatUpdateGuard()
    return _guard_instance


def start_update_guard():
    """启动微信更新弹窗自动拦截守护（幂等，可重复调用）"""
    get_update_guard().start()


def stop_update_guard():
    """停止守护"""
    if _guard_instance:
        _guard_instance.stop()
