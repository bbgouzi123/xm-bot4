import asyncio
import ctypes
import ctypes.wintypes
import logging
import threading
import time
from contextlib import contextmanager, asynccontextmanager
from typing import Optional
from src.uia.uia_ws_notify import notify_frontend

logger = logging.getLogger(__name__)

from src.uia.win32_input_defs import *

class UIAInterruptError(Exception):
    """当用户按下 ESC 键中断自动化时抛出的异常"""
    pass

class UIAInputGuard:
    """全局 UIA 输入锁定服务（单例）
    
    统一规范：凡是自动化物理操作必须使用 with uia_lock(...) 包裹。
    支持功能：
    1. 低级钩子锁定键鼠（放行 ESC 键）。
    2. WebSocket 通知前端弹窗提示。
    3. ESC 键直接由钩子捕获，立即解锁并终止当前任务。
    """

    _instance: Optional["UIAInputGuard"] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    inst = super().__new__(cls)
                    inst._active, inst._depth = False, 0
                    inst._reentrant_lock = threading.RLock()
                    inst._interrupt_requested = False
                    inst._hook_thread = inst._kb_hook = inst._mouse_hook = inst._hook_thread_id = None
                    inst._last_screenshot_time = 0.0
                    cls._instance = inst
        return cls._instance

    # ==================== 低级钩子实现 ====================

    def _install_hooks(self):
        """在专用线程中安装低级钩子并运行消息泵"""
        if self._hook_thread and self._hook_thread.is_alive():
            return

        ready_event = threading.Event()

        def _hook_thread_func():
            # 键盘钩子回调：放行 ESC 和软件注入的按键，阻断其他物理按键
            # 【安全关键】回调中的任何未捕获异常会导致 Windows 强制终止进程
            @HOOKPROC
            def keyboard_proc(nCode, wParam, lParam):
                try:
                    if nCode == HC_ACTION:
                        # KBDLLHOOKSTRUCT: vkCode(DWORD) + scanCode(DWORD) + flags(DWORD)
                        ptr = ctypes.cast(lParam, ctypes.POINTER(ctypes.c_ulong * 3))
                        vk_code = ptr.contents[0]
                        flags = ptr.contents[2]
                        is_injected = bool(flags & 0x10)  # LLKHF_INJECTED
                        
                        if is_injected:
                            return user32.CallNextHookEx(self._kb_hook, nCode, wParam, lParam)
                        
                        if vk_code == VK_ESCAPE and wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                            self._on_esc_pressed()
                            return user32.CallNextHookEx(self._kb_hook, nCode, wParam, lParam)

                        # 放行 PrintScreen 截屏键 (0x2C)
                        if vk_code == 0x2C:
                            self._last_screenshot_time = time.time()
                            logger.info(f"[InputGuard] 物理 PrintScreen 键按下，记录截图触发时间，放行截屏事件")
                            return user32.CallNextHookEx(self._kb_hook, nCode, wParam, lParam)

                        # 放行 Alt 键 (0x12 / 0xA4 / 0xA5)
                        if vk_code in (0x12, 0xA4, 0xA5):
                            return user32.CallNextHookEx(self._kb_hook, nCode, wParam, lParam)

                        # 放行 Alt + A (0x41 且 Alt 按下)
                        is_alt_down = bool(flags & 0x20)
                        if vk_code == 0x41 and is_alt_down:
                            self._last_screenshot_time = time.time()
                            logger.info(f"[InputGuard] 物理 Alt + A 快捷键按下，记录截图触发时间，放行截屏事件")
                            return user32.CallNextHookEx(self._kb_hook, nCode, wParam, lParam)

                        return 1
                except Exception:
                    pass  # 绝不让异常泄漏到 Windows 消息泵
                return user32.CallNextHookEx(self._kb_hook, nCode, wParam, lParam)

            # 鼠标钩子回调：只阻断物理鼠标输入，放行软件注入的鼠标事件
            # 【安全关键】回调中的任何未捕获异常会导致 Windows 强制终止进程
            @HOOKPROC
            def mouse_proc(nCode, wParam, lParam):
                try:
                    if nCode == HC_ACTION:
                        # MSLLHOOKSTRUCT: pt.x(LONG) + pt.y(LONG) + mouseData(DWORD) + flags(DWORD)
                        ptr = ctypes.cast(lParam, ctypes.POINTER(ctypes.c_ulong * 4))
                        flags = ptr.contents[3]
                        is_injected = bool(flags & 0x01)  # LLMHF_INJECTED
                        
                        if is_injected:
                            return user32.CallNextHookEx(self._mouse_hook, nCode, wParam, lParam)
                        return 1
                except Exception:
                    pass  # 绝不让异常泄漏到 Windows 消息泵
                return user32.CallNextHookEx(self._mouse_hook, nCode, wParam, lParam)

            # 保存回调引用防止 GC
            self._kb_proc_ref = keyboard_proc
            self._mouse_proc_ref = mouse_proc

            # 安装钩子
            self._kb_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, ctypes.cast(keyboard_proc, ctypes.c_void_p), None, 0)
            self._mouse_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, ctypes.cast(mouse_proc, ctypes.c_void_p), None, 0)

            self._hook_thread_id = kernel32.GetCurrentThreadId()

            if self._kb_hook and self._mouse_hook:
                logger.info("[InputGuard] 低级钩子已安装（键盘: 放行ESC, 鼠标: 全部拦截）")
            else:
                logger.warning("[InputGuard] 低级钩子安装失败，回退到 BlockInput")
                user32.BlockInput(True)

            ready_event.set()

            # 消息泵
            msg = ctypes.wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

        self._hook_thread = threading.Thread(target=_hook_thread_func, daemon=True, name="input-guard-hooks")
        self._hook_thread.start()
        ready_event.wait(timeout=2.0)

    def _uninstall_hooks(self):
        """卸载低级钩子"""
        if self._kb_hook:
            user32.UnhookWindowsHookEx(self._kb_hook)
            self._kb_hook = None
        if self._mouse_hook:
            user32.UnhookWindowsHookEx(self._mouse_hook)
            self._mouse_hook = None

        if self._hook_thread_id:
            user32.PostThreadMessageW(self._hook_thread_id, 0x0012, 0, 0)  # WM_QUIT
            self._hook_thread_id = None

        try:
            user32.BlockInput(False)
        except Exception:
            pass
        logger.info("[InputGuard] 低级钩子已卸载，键鼠已解锁")

    def _on_esc_pressed(self):
        """ESC 被钩子捕获时的处理"""
        if self._interrupt_requested:
            return

        self._interrupt_requested = True
        print(f"\n[InputGuard] 检测到 ESC 按键，正在紧急停止自动化任务...")
        self._uninstall_hooks()
        self._active = False
        notify_frontend("unlock", "已通过 ESC 紧急停止")
        try:
            from src.utils.stop_signal import stop_signal
            stop_signal.request_stop("用户按下 ESC")
        except Exception:
            pass

    def force_release(self):
        """强行解除所有锁定，重置嵌套深度，确保物理键鼠恢复响应"""
        with self._reentrant_lock:
            self._depth = 0
            self._active = False
            self._interrupt_requested = False
            try:
                self._block_input(False)
            except Exception as e:
                logger.error(f"[InputGuard] 强行释放输入锁定异常: {e}")
            try:
                notify_frontend("unlock", "由于操作超时已强行解锁")
            except Exception:
                pass

    # ==================== 接口方法 ====================

    def _block_input(self, block: bool):
        if block:
            self._install_hooks()
        else:
            self._uninstall_hooks()

    def update_status(self, status_line: str):
        """在锁定期间实时推送进度文字到前端弹窗（不改变锁定/解锁状态）。

        调用规范：仅在 with uia_lock(...) 上下文内调用，否则静默忽略。
        """
        if not self._active:
            return
        notify_frontend("status_update", status_line)

    def _wait_if_screenshot_active(self):
        """如果最近触发过物理截屏快捷键，则挂起当前线程进行物理模拟避让"""
        from .screenshot_guard import wait_if_screenshot_active
        wait_if_screenshot_active(self)

    def check_interrupt(self):
        from src.utils.stop_signal import stop_signal
        if self._interrupt_requested or stop_signal.is_stopped:
            self._interrupt_requested = False
            raise UIAInterruptError("用户按下 ESC 键或系统发出停止信号，中断操作")

        self._wait_if_screenshot_active()



    @contextmanager
    def __call__(self, message: str = "自动化操作中，将锁定鼠标与键盘", hwnd: Optional[int] = None):
        from src.utils.stop_signal import stop_signal
        if self._interrupt_requested or stop_signal.is_stopped:
            self._interrupt_requested = False
            raise UIAInterruptError("检测到全局停止信号或ESC键，拒绝锁定")

        with self._reentrant_lock:
            self._depth += 1
            is_outermost = (self._depth == 1)

        try:
            if is_outermost:
                self._active = True
                self._interrupt_requested = False
                
                # 记录当前鼠标位置，以便任务结束后恢复
                try:
                    import win32api
                    self._saved_cursor_pos = win32api.GetCursorPos()
                    logger.info(f"[InputGuard] 记录初始鼠标位置: {self._saved_cursor_pos}")
                except Exception as e:
                    logger.warning(f"[InputGuard] 记录鼠标位置失败: {e}")
                    self._saved_cursor_pos = None

                try:
                    notify_frontend("lock", message)
                except Exception as notify_err:
                    logger.warning(f"[InputGuard] 广播锁定状态失败: {notify_err}")

                from src.uia.retry.window_ops import ensure_wechat_visible_fallback
                ensure_wechat_visible_fallback(hwnd)
                time.sleep(0.15)
                self._block_input(True)

            yield
        except UIAInterruptError:
            raise
        except Exception as e:
            logger.error(f"[InputGuard] 任务运行异常: {e}")
            raise
        finally:
            with self._reentrant_lock:
                self._depth -= 1
                is_outermost_exit = (self._depth == 0)

            if is_outermost_exit:
                try:
                    self._block_input(False)
                except Exception as unblock_err:
                    logger.error(f"[InputGuard] 释放锁定钩子异常: {unblock_err}")
                
                # 恢复鼠标位置
                if getattr(self, "_saved_cursor_pos", None):
                    try:
                        import win32api
                        win32api.SetCursorPos(self._saved_cursor_pos)
                        logger.info(f"[InputGuard] 恢复鼠标位置到: {self._saved_cursor_pos}")
                    except Exception as e:
                        logger.warning(f"[InputGuard] 恢复鼠标位置失败: {e}")
                    finally:
                        self._saved_cursor_pos = None

                self._active = False
                if not self._interrupt_requested:
                    try:
                        notify_frontend("unlock")
                    except Exception as unlock_notify_err:
                        logger.warning(f"[InputGuard] 广播解锁状态失败: {unlock_notify_err}")
                self._interrupt_requested = False

    @asynccontextmanager
    async def async_guard(self, message: str = "自动化操作中，将锁定鼠标与键盘", hwnd: Optional[int] = None):
        from src.utils.stop_signal import stop_signal
        if self._interrupt_requested or stop_signal.is_stopped:
            self._interrupt_requested = False
            raise UIAInterruptError("检测到全局停止信号或ESC键，拒绝锁定")

        with self._reentrant_lock:
            self._depth += 1
            is_outermost = (self._depth == 1)

        try:
            if is_outermost:
                self._active = True
                self._interrupt_requested = False
                
                # 记录当前鼠标位置，以便任务结束后恢复
                try:
                    import win32api
                    self._saved_cursor_pos = win32api.GetCursorPos()
                    logger.info(f"[InputGuard] 异步记录初始鼠标位置: {self._saved_cursor_pos}")
                except Exception as e:
                    logger.warning(f"[InputGuard] 异步记录鼠标位置失败: {e}")
                    self._saved_cursor_pos = None

                try:
                    notify_frontend("lock", message)
                except Exception as notify_err:
                    logger.warning(f"[InputGuard] 异步广播锁定状态失败: {notify_err}")

                from src.uia.retry.window_ops import ensure_wechat_visible_fallback
                ensure_wechat_visible_fallback(hwnd)
                await asyncio.sleep(0.15)
                self._block_input(True)

            yield
        finally:
            with self._reentrant_lock:
                self._depth -= 1
                is_outermost_exit = (self._depth == 0)

            if is_outermost_exit:
                try:
                    self._block_input(False)
                except Exception as unblock_err:
                    logger.error(f"[InputGuard] 异步释放锁定钩子异常: {unblock_err}")
                
                # 恢复鼠标位置
                if getattr(self, "_saved_cursor_pos", None):
                    try:
                        import win32api
                        win32api.SetCursorPos(self._saved_cursor_pos)
                        logger.info(f"[InputGuard] 异步恢复鼠标位置到: {self._saved_cursor_pos}")
                    except Exception as e:
                        logger.warning(f"[InputGuard] 异步恢复鼠标位置失败: {e}")
                    finally:
                        self._saved_cursor_pos = None

                self._active = False
                if not self._interrupt_requested:
                    try:
                        notify_frontend("unlock")
                    except Exception as unlock_notify_err:
                        logger.warning(f"[InputGuard] 异步广播解锁状态失败: {unlock_notify_err}")
                self._interrupt_requested = False

    @property
    def is_locked(self) -> bool:
        return self._active

uia_lock = UIAInputGuard()
