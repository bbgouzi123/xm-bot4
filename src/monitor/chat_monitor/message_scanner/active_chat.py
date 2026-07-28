import hashlib
import logging
import asyncio
from src.utils.uia_task_runner import run_uia_with_timeout

logger = logging.getLogger(__name__)

class ActiveChatMixin:
    """活跃聊天检测与无红点消息穿透预检"""

    async def _check_active_chat(self) -> tuple:
        active_name = ""
        active_last_msgs = []
        active_chat_fp = ""
        user_active_now = False
        try:
            # 🌟 【UIA 排他锁避让】若当前其它前台交互动作（如模拟按键录音）已开启排他锁，
            # 立即跳过 UIA 读取，彻底杜绝在物理按键按下期间因调用 UIA 导致系统挂起超时的 Bug！
            try:
                from src.uia.input_guard import uia_lock
                if uia_lock.is_locked:
                    logger.debug("[监控] 检测到 UIA 排他锁已开启，跳过活跃窗口检测以避让物理流程")
                    return "", [], "", False
            except Exception as e_lock:
                logger.debug(f"[监控] 避让检测 UIA 锁状态异常: {e_lock}")

            # 🌟 【数据库监控避让调整】
            # 不再在 WCDB 在线时完全关闭活跃窗口 UIA 检测。因为活动窗口消息在微信在前台时未读数为 0，
            # 数据库同步器感知不到。我们仅在微信窗口处于前台且被激活时（GetForegroundWindow == self.driver.hwnd）
            # 才通过 UIA 扫描活跃窗口消息，实现零干扰与无缝穿透互补。
            pass
        except Exception as e_wcdb:
            logger.debug(f"[监控] 避让检测 WCDB 在线状态异常: {e_wcdb}")

        try:
            import win32gui
            # 🌟 【性能与卡死加固】仅在微信窗口处于前台激活状态时，检测“当前活跃聊天”才有实际意义。
            # 如果微信在后台或最小化，我们直接返回空，跳过 UIA 查找，彻底杜绝后台 UIA 遍历导致的挂起超时 Bug！
            if not getattr(self, "driver", None) or not getattr(self.driver, "hwnd", None):
                return "", [], "", False

            if win32gui.GetForegroundWindow() != self.driver.hwnd:
                return "", [], "", False

            # 🌟 【响应度与卡死防范】检测微信窗口当前是否正处于卡死/无响应 (Not Responding) 状态。
            # 通过发送 WM_NULL (0x0000) 并设置 250ms 的 SMTO_ABORTIFHUNG 超时。
            # 如果微信主线程当前卡死，立即跳过 UIA 查找，避免 UIA 线程在底层 COM 挂起卡死导致强释锁！
            try:
                import ctypes
                result_val = ctypes.c_ulong(0)
                ret = ctypes.windll.user32.SendMessageTimeoutW(
                    self.driver.hwnd,
                    0x0000,  # WM_NULL
                    0,
                    0,
                    0x0002,  # SMTO_ABORTIFHUNG
                    250,     # 250ms 超时
                    ctypes.byref(result_val),
                )
                if not ret:
                    logger.warning("[监控] 预检发现微信窗口无响应/卡死 (SendMessageTimeout 250ms)，跳过活跃检测")
                    return "", [], "", False
            except Exception as e_resp:
                logger.debug(f"[监控] 预检微信主窗口响应度异常: {e_resp}")

            from src.utils.user_activity import is_user_active
            # 缩短活跃检测时间到 1.5 秒，极大提升无红点消息的穿透检测响应速度
            user_active_now = is_user_active(cooldown_ms=1500)
            
            def _find_active_input_name():
                try:
                    from src.utils.safe_uia import find_active_input_control_safely
                    hwnd = getattr(self.driver, "hwnd", 0) if getattr(self, "driver", None) else 0
                    name = find_active_input_control_safely(None, hwnd=hwnd)
                    if name:
                        import re
                        name = re.sub(r'\s+按住.*$', '', name)
                        name = re.sub(r'\(\d+\)$', '', name)
                        return name.strip()
                except Exception as ex:
                    logger.debug(f"[预检] 寻找活跃输入框名称异常: {ex}")
                return ""
            active_name = await run_uia_with_timeout(_find_active_input_name, 10.0)
            
            if active_name and active_name not in self.SYSTEM_SESSIONS and not active_name.startswith('折叠的聊天'):
                active_last_msgs = await run_uia_with_timeout(
                    self.driver.get_all_messages, 15.0, False, 5, active_name, False
                )
                if active_last_msgs:
                    chat_fp_str = "||".join(f"{s}:{c}" for s, c in active_last_msgs)
                    active_chat_fp = hashlib.md5(f"{active_name}:{chat_fp_str}".encode()).hexdigest()
        except (asyncio.TimeoutError, TimeoutError):
            logger.debug("[监控] 预检活跃窗口超时，跳过本次活跃窗口检测")
        except Exception as active_ex:
            logger.warning("[监控] 预检活跃窗口消息气泡失败", exc_info=True)
            
        return active_name, active_last_msgs, active_chat_fp, user_active_now
