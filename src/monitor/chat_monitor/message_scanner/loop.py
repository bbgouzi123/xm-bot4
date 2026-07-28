import asyncio
import time
import logging
from src.utils.rest_time import is_rest_time
from src.utils.uia_task_runner import is_uia_maintenance_active

logger = logging.getLogger(__name__)

class LoopMixin:
    """心跳守护与主扫描循环编排"""

    async def _loop(self):
        disconnected_at = None
        last_alert_time = 0.0
        _last_cache_cleanup = time.time()
        _CACHE_CLEANUP_INTERVAL = 300  # 每 5 分钟清理一次过期缓存
        while self._running:
            try:
                if self._paused:
                    await asyncio.sleep(1)
                    continue
                if is_uia_maintenance_active():
                    await asyncio.sleep(1)
                    continue
                
                curr_conn = self.driver.is_connected()
                if not curr_conn:
                    self._uia_preheated = False
                    if disconnected_at is None:
                        disconnected_at = time.time()
                        logger.warning("[心跳守护] 检测到微信实例意外掉线/窗口关闭，启动 60 秒确认聚合窗口...")
                    else:
                        elapsed_disconnect = time.time() - disconnected_at
                        if elapsed_disconnect >= 60.0 and (time.time() - last_alert_time) >= 300.0:
                            logger.error("[心跳守护] 微信实例断线确认已超过 60 秒，触发风控/下线报警！")
                            last_alert_time = time.time()
                            try:
                                from src.utils.alert_notifier import alert_notifier
                                from src.utils.license_validator.machine import MachineMixin
                                machine_code = MachineMixin.get_machine_code()
                                account_id = getattr(self.driver, 'bot_wxid', '') or self.driver._nickname or "分身账号"
                                
                                await alert_notifier.trigger_risk_alert(
                                    machine_code=machine_code,
                                    account_id=account_id,
                                    reason="微信客户端窗口已持续断开或异常关闭超过 60 秒，登录过期或被腾讯风控限制！",
                                    is_fatal=True,
                                    hwnd=self.driver.hwnd or 0
                                )
                            except Exception as ae:
                                logger.error(f"[心跳守护] 发送掉线告警失败: {ae}")
                else:
                    if disconnected_at is not None:
                        logger.info("[心跳守护] 微信实例已重新连接，清除断线计时")
                        disconnected_at = None

                if curr_conn:
                    # ── 原子初始化与 UIA 热身屏障 ──
                    init_ready = await self._wait_for_initialization()
                    if not init_ready:
                        await asyncio.sleep(1)
                        continue

                    if not getattr(self, "_startup_checked", False):
                        self._startup_checked = True
                        try:
                            await self._run_startup_chat_check()
                        except Exception as startup_err:
                            logger.error(f"[监控] 运行启动聊天检查时发生异常: {startup_err}", exc_info=True)

                    # ── 多开专用：每次 UIA 操作前确保目标微信窗口可见 ──
                    # 🌟 关键优化：如果当前已经激活了 WCDB 数据库引擎，允许窗口隐藏并静默后台同步。
                    # 只有在完全降级为纯物理 UIA 扫描时，才需要强制拉到前台。
                    if not getattr(self, "_wcdb_active", False):
                        hwnd = getattr(self.driver, 'hwnd', 0)
                        if hwnd:
                            import win32gui
                            import ctypes
                            _user32 = ctypes.windll.user32
                            _is_iconic = _user32.IsIconic(hwnd)
                            _is_visible = win32gui.IsWindowVisible(hwnd)
                            if _is_iconic or not _is_visible:
                                logger.info(f"[多开-置前] hwnd={hwnd} 检测到窗口最小化/不可见，自动唤回后执行扫描")
                                try:
                                    loop = asyncio.get_event_loop()
                                    from src.uia.retry.window_ops import ensure_wechat_visible_for_automation
                                    visible = await loop.run_in_executor(
                                        None,
                                        lambda: ensure_wechat_visible_for_automation(hwnd, timeout=3.0)
                                    )
                                    if not visible:
                                        logger.warning(f"[多开-置前] hwnd={hwnd} 唤回失败，跳过本轮扫描")
                                        await asyncio.sleep(self._check_interval)
                                        continue
                                except Exception as _vis_err:
                                    logger.warning(f"[多开-置前] hwnd={hwnd} 唤回异常: {_vis_err}")

                    await self._check()
            except asyncio.CancelledError:
                break
            except Exception as e:
                err_str = str(e)
                # COM / UIA 异常是 Windows 进程间通信报错，需要冷却更长时间
                is_com_error = any(k in err_str for k in (
                    'COMError', 'HRESULT', 'InvalidOperationException',
                    'UIA_E', 'ElementNotAvailableException', '-2147', '0x80',
                ))
                cool_secs = 5 if is_com_error else 1
                logger.error(
                    f"[监控] {'COM/UIA 异常' if is_com_error else '扫描异常'} "
                    f"(冷却 {cool_secs}s): {e}",
                    exc_info=(not is_com_error)
                )
                self._stats["errors"] += 1
                await asyncio.sleep(cool_secs)
            await asyncio.sleep(self._check_interval)
            # 定时内存缓存清理（防止长时间运行导致 OOM）
            now = time.time()
            if now - _last_cache_cleanup > _CACHE_CLEANUP_INTERVAL:
                _last_cache_cleanup = now
                self._cleanup_stale_caches(now)
