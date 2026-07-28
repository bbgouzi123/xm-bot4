"""
心跳校验模块
"""
import time
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# 心跳校验配置
HEARTBEAT_INTERVAL = 300
GRACE_PERIOD = 900
MAX_OFFLINE_FAILURES = 3

class HeartbeatMixin:
    _verify_thread: Optional[threading.Thread] = None
    _stop_event = threading.Event()
    _consecutive_failures: int = 0
    _last_success_time: Optional[float] = None
    _degraded: bool = False
    _notified_expired: bool = False

    @classmethod
    def start_periodic_verify(cls):
        """启动后台心跳校验线程"""
        if cls._verify_thread and cls._verify_thread.is_alive():
            return

        cls._stop_event.clear()
        cls._consecutive_failures = 0
        cls._last_success_time = time.time()
        cls._degraded = False
        cls._notified_expired = False
        cls._last_check_update_time = 0.0

        def _check_software_update():
            now = time.time()
            if now - getattr(cls, "_last_check_update_time", 0.0) < 43200:
                return
            setattr(cls, "_last_check_update_time", now)

            try:
                from app.paths import xm_bot4_splash_app_version
                from xm_py_updater import XMUpdater
                from pathlib import Path
                import sys

                curr_ver_raw = xm_bot4_splash_app_version() or "1.0.0"
                curr_ver = curr_ver_raw[1:] if curr_ver_raw.startswith("v") else curr_ver_raw

                if getattr(sys, 'frozen', False):
                    app_dir = Path(sys.executable).parent
                else:
                    app_dir = Path(__file__).resolve().parent.parent.parent.parent

                updater = XMUpdater(
                    app_key="xm-bot4",
                    current_version=curr_ver,
                    download_dir=app_dir / "update",
                    target_dir=app_dir,
                )

                data = updater.fetch_latest_sync()
                if data and data.get("version"):
                    latest_ver = str(data.get("version") or "").strip()
                    if XMUpdater.compare_versions(latest_ver, curr_ver) > 0:
                        changelog = data.get("changelog", "") or ""
                        body = f"发现新版本 v{latest_ver}！"
                        if changelog:
                            body += f" 更新日志: {changelog}"
                        else:
                            body += " 建议立即更新以获得更好的系统稳定性和安全体验。"

                        from src.utils.alert_notifier import alert_notifier
                        import asyncio

                        try:
                            loop = asyncio.get_event_loop()
                            if loop.is_running():
                                loop.create_task(alert_notifier.send_user_notification(
                                    title=f"📢 软件有新版本 v{latest_ver} 可用",
                                    body=body,
                                    category="system"
                                ))
                            else:
                                loop.run_until_complete(alert_notifier.send_user_notification(
                                    title=f"📢 软件有新版本 v{latest_ver} 可用",
                                    body=body,
                                    category="system"
                                ))
                        except Exception:
                            import threading
                            threading.Thread(target=lambda: asyncio.run(
                                alert_notifier.send_user_notification(
                                    title=f"📢 软件有新版本 v{latest_ver} 可用",
                                    body=body,
                                    category="system"
                                )
                            ), daemon=True).start()
            except Exception as e:
                logger.error(f"[心跳] 软件更新校验异常: {e}")

        def _send_expired_notification(status):
            try:
                from src.utils.alert_notifier import alert_notifier
                import asyncio
                plan_name = status.get("plan_name", "试用版")
                
                # 区分设备超限和订阅到期
                if status.get("status") == "device_limit_exceeded":
                    title = "⚠️ 设备授权未通过"
                    body = status.get("message") or "当前设备未授权。检测到您的账号绑定设备数已达上限，或当前机器未在该账号的绑定列表中。请前往主控制台“个人中心-产品订阅”进行设备解绑，或升级套餐增加名额。"
                else:
                    title = "⚠️ 订阅到期提醒"
                    body = f"您的产品订阅已到期（当前套餐: {plan_name}），为避免影响微信机器人自动化任务的运行，请及时续费。"

                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        loop.create_task(alert_notifier.send_user_notification(
                            title=title,
                            body=body,
                            category="system"
                        ))
                    else:
                        loop.run_until_complete(alert_notifier.send_user_notification(
                            title=title,
                            body=body,
                            category="system"
                        ))
                except Exception:
                    import threading
                    threading.Thread(target=lambda: asyncio.run(
                        alert_notifier.send_user_notification(
                            title=title,
                            body=body,
                            category="system"
                        )
                    ), daemon=True).start()
            except Exception as ex:
                logger.error(f"[心跳] 推送通知失败: {ex}")

        def heartbeat_loop():
            # 1. 启动时立即进行首次检测，避免延迟
            try:
                status = getattr(cls, 'check_subscription', lambda: {})()
                if status.get("valid") or status.get("status") == "trial":
                    cls._notified_expired = False
                else:
                    cls._notified_expired = True
                    _send_expired_notification(status)
            except Exception as e:
                logger.error(f"[心跳] 首次订阅校验异常: {e}")

            # 启动时检测一次软件更新
            _check_software_update()

            # 2. 进入周期性检查循环
            while not cls._stop_event.is_set():
                cls._stop_event.wait(HEARTBEAT_INTERVAL)
                if cls._stop_event.is_set():
                    break
                try:
                    # 检查软件更新（有 12 小时间隔限制）
                    _check_software_update()

                    # 这里的 check_subscription 将由 SubscriptionMixin 提供
                    status = getattr(cls, 'check_subscription', lambda: {})()
                    
                    if status.get("valid") or status.get("status") == "trial":
                        cls._consecutive_failures = 0
                        cls._last_success_time = time.time()
                        cls._notified_expired = False
                        if cls._degraded:
                            cls._degraded = False
                            logger.info("[心跳] 订阅已恢复，退出降级模式")
                    else:
                        cls._consecutive_failures += 1
                        
                        # 检测到过期，若未提示过则发送通知
                        if not cls._notified_expired:
                            cls._notified_expired = True
                            _send_expired_notification(status)

                        elapsed = time.time() - (cls._last_success_time or time.time())
                        if elapsed >= GRACE_PERIOD and cls._consecutive_failures >= MAX_OFFLINE_FAILURES:
                            if not cls._degraded:
                                cls._degraded = True
                                logger.warning("[心跳] 🔒 连续校验失败，超过宽限期，已降级")
                except Exception as e:
                    cls._consecutive_failures += 1
                    logger.error(f"[心跳] 校验异常: {e}")

        cls._verify_thread = threading.Thread(target=heartbeat_loop, daemon=True, name="subscription-heartbeat")
        cls._verify_thread.start()
        logger.info("[心跳] V2 订阅心跳已启动")

    @classmethod
    def stop_periodic_verify(cls):
        """停止心跳校验"""
        cls._stop_event.set()
        if cls._verify_thread:
            cls._verify_thread.join(timeout=5)
        logger.info("[心跳] 订阅心跳已停止")

    @classmethod
    def is_degraded(cls) -> bool:
        """是否处于降级模式"""
        import os
        if os.environ.get("XM_BYPASS_DEGRADE") == "true":
            return False
        return cls._degraded
