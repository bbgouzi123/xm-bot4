import threading
import time
import logging
import random
from typing import List, Dict, Any

from src.uia.driver import WeChatDriver
from src.uia.retry import random_delay, try_click
from src.uia.tag_sync import WeChatTagSync
from src.utils.rest_time import is_rest_time
from src.utils.moment_config import get_moment_settings

logger = logging.getLogger(__name__)

class MomentInteractionManager:
    """
    智能巡回点赞评论管理器 
    依赖于 UIAutomation 的循环滚动与内容识别，模拟真实人类操作。
    通过将提取和滑动逻辑剥离为 moment_extractor 与 moment_interactor 提高可维护性。
    """
    def __init__(self, driver: WeChatDriver, ai_service=None):
        self.driver = driver
        self.ai_service = ai_service
        self._running = False
        self._paused = False
        self._thread = None
        self._interactions_log = []
        self._tag_sync = WeChatTagSync(driver)
        self._pending_tags = []
        self._account_id = getattr(driver, 'bot_wxid', 'main') or 'main'
        self._extract_queue = __import__("queue").Queue()
        self._extract_thread = None
        self._current_patrol_index = 0
        self.account_errors = {}
        
    def start(self):
        if self._running:
            return
            
        # 🌟 [修复] 动态同步 MultiAccountManager 中已连接的微信驱动，避免全局 dummy 导致连接失效
        import app.state as app_state
        account_manager = getattr(app_state, 'account_manager', None)
        if account_manager and account_manager.primary_driver:
            self.driver = account_manager.primary_driver
            self._account_id = getattr(self.driver, 'bot_wxid', 'main') or 'main'
            
        if not self.driver or not self.driver.is_connected():
            logger.error("WeChat not connected. Cannot start moment interactions.")
            return
            
        self.account_errors.clear()  # 重新启动时清空全部错误，恢复熔断账号
        self._running = True
        self._paused = False
        
        self._extract_thread = threading.Thread(target=self._extract_worker_loop, daemon=True, name="moment-extractor")
        self._extract_thread.start()
        
        self._thread = threading.Thread(target=self._patrol_loop, daemon=True)
        self._thread.start()
        logger.info("朋友圈自动点赞评论巡回已启动。")
        
    def stop(self):
        self._running = False
        self._paused = False
        self._extract_queue.put(None)
        
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._extract_thread:
            self._extract_thread.join(timeout=2.0)
        logger.info("朋友圈自动点赞评论巡回已停止。")

    def _extract_worker_loop(self):
        from .moment_extractor import extract_worker_loop
        extract_worker_loop(self)

    def pause(self):
        if self._running:
            self._paused = True
            logger.info("朋友圈巡游已暂停")

    def resume(self):
        if self._running:
            self._paused = False
            self.account_errors.clear()  # 用户恢复时重置所有连续异常计数，解除熔断
            logger.info("朋友圈巡游已恢复")
        
    def get_status(self) -> Dict[str, Any]:
        from .moment_helper import get_moment_status
        return get_moment_status(self)

    def _persist_log(self, action_type: str, author_name: str,
                     content: str = "", reply_text: str = "",
                     fingerprint: str = "", author_wxid: str = ""):
        """将互动日志记入内存缓存 + 传统推同步后端"""
        from .moment_helper import persist_moment_log
        persist_moment_log(self, action_type, author_name, content, reply_text, fingerprint, author_wxid)

    def get_logs(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self._interactions_log[-limit:]
        
    def _open_moments(self) -> Any:
        from src.monitor.moment_patrol_utils import open_moments
        return open_moments(self.driver)

    def _patrol_loop(self):
        """核心巡游监控线程 — 矩阵轮游调度（对齐竞品，高防封）"""
        from src.utils.stop_signal import stop_signal
        from src.orchestrator.ui_bus import (
            ui_bus, UICommand, UICommandKind, UICommandPriority, UICommandStatus
        )
        import app.state as app_state
        
        consecutive_errors = 0
        while self._running:
            if stop_signal.is_stopped:
                logger.info("[巡游] 检测到 ESC 停止信号，重置并跳过当前轮次")
                stop_signal.reset()
                time.sleep(2)
                continue

            if self._paused:
                time.sleep(5)
                continue

            # 1. 动态收集当前矩阵中所有在线的微信号
            active_accounts = []
            account_manager = getattr(app_state, 'account_manager', None)
            if account_manager:
                for inst in account_manager._instances.values():
                    if inst.driver and inst.driver.is_connected():
                        wxid = inst.wxid or getattr(inst.driver, 'bot_wxid', '')
                        if wxid:
                            active_accounts.append(wxid)
            
            if not active_accounts:
                # 兜底：回退到单账号模式
                main_wxid = getattr(self.driver, 'bot_wxid', 'main') or 'main'
                active_accounts.append(main_wxid)

            # 2. 过滤出开启了朋友圈互动且未超限的微信号
            valid_accounts = []
            from src.utils.daily_counter import DailyCounter
            dc = DailyCounter()
            for acc_id in active_accounts:
                acc_settings = get_moment_settings(acc_id)
                if acc_settings.get("enabled", True):
                    # 只要今日点赞或评论额度未达上限且未熔断，即为有效轮询账号
                    if self.account_errors.get(acc_id, 0) < 5:
                        if dc.can_do("moment_like", acc_id) or dc.can_do("moment_comment", acc_id):
                            valid_accounts.append((acc_id, acc_settings))

            if not valid_accounts:
                logger.info("[矩阵轮游] 没有任何已开启朋友圈互动或额度未满的账号，30秒后重新巡检...")
                time.sleep(30)
                continue

            # 3. 顺次轮询获取下一个调度账号
            if self._current_patrol_index >= len(valid_accounts):
                self._current_patrol_index = 0
            
            target_wxid, target_settings = valid_accounts[self._current_patrol_index]
            self._current_patrol_index = (self._current_patrol_index + 1) % len(valid_accounts)

            # 记录当前执行巡游的账号，供前端 status 接口查询
            self.current_patrol_wxid = target_wxid
            self.current_patrol_nickname = ""
            try:
                if account_manager:
                    for inst in account_manager._instances.values():
                        if inst.wxid == target_wxid:
                            self.current_patrol_nickname = inst.nickname or target_wxid
                            break
            except Exception as ex:
                logger.debug(f"[巡游] 获取账号昵称失败: {ex}")

            # 4. 防封打扰：判断当前是否是休息时间
            if is_rest_time("moment_interact", target_wxid):
                logger.info(f"[矩阵轮游] 账号 {target_wxid} 命中深度睡眠时段，跳过本轮巡游")
                time.sleep(30)
                continue

            logger.info(f"🔮 [矩阵轮游调度] 顺次命中账号: {target_wxid} (开启朋友圈自动巡查)")

            round_stats = 0
            try:
                # 5. 通过 UIBus 投递串行任务，确保物理隔离
                cmd = UICommand(
                    wxid=target_wxid if target_wxid != 'main' else '',
                    kind=UICommandKind.MOMENT_INTERACT,
                    payload={
                        "settings": target_settings,
                        "account_id": target_wxid
                    },
                    priority=UICommandPriority.LOW,
                    timeout=600.0,
                )
                ui_bus.submit(cmd)
                finished = ui_bus.await_result(cmd.id, timeout=1800.0)
                if finished.status == UICommandStatus.SUCCESS:
                    round_stats = finished.result
                    self.account_errors[target_wxid] = 0
                else:
                    raise RuntimeError(f"UIBus 任务返回非 SUCCESS: {finished.status.value}, 错误: {finished.error}")

                logger.info(f"[矩阵轮游] 账号 {target_wxid} 本轮巡查完成，互动条数: {round_stats}")
            except Exception as e:
                is_interrupt = "ESC" in str(e) or "Interrupt" in type(e).__name__ or "UIAInterruptError" in type(e).__name__
                if is_interrupt:
                    logger.info(f"[矩阵轮游] 用户中断操作，已通过 ESC 拦截并重置当前账号状态: {e}")
                    self.account_errors[target_wxid] = 0 # ESC 主动干预不记为异常
                else:
                    self.account_errors[target_wxid] = self.account_errors.get(target_wxid, 0) + 1
                    errs_count = self.account_errors[target_wxid]
                    logger.error(f"[矩阵轮游] 账号 {target_wxid} 刷圈异常 (累计 {errs_count} 次): {e}")
                    if errs_count >= 5:
                        logger.error(f"[朋友圈风控] 检测到账号 {target_wxid} 连续 5 次巡游严重异常，触发告警，该账号被安全熔断挂起")
                        self._trigger_risk_alert_safe(target_wxid, e)

            # 6. 计算本轮结束后的拟人化个性休眠时长
            interval_min = target_settings.get("patrol_interval_min", 300)
            interval_max = target_settings.get("patrol_interval_max", 900)
            wait_seconds = random.randint(interval_min, interval_max)
            logger.info(f"[矩阵轮游] 本轮调度结束。全局休眠 {wait_seconds} 秒后顺次轮询下一个账号...")
            
            for _ in range(wait_seconds):
                if not self._running or self._paused:
                    break
                if stop_signal.is_stopped:
                    logger.info("[巡游等待] 检测到 ESC 停止信号，已重置")
                    stop_signal.reset()
                time.sleep(1)

    def _trigger_risk_alert_safe(self, account_id: str, err: Exception):
        """发送防封风险报警"""
        from src.monitor.moment_patrol_utils import trigger_risk_alert_safe
        trigger_risk_alert_safe(account_id, err)

    def _patrol_round_body(self, settings: dict, account_id: str) -> int:
        from .moment_interactor import patrol_round_body
        return patrol_round_body(self, settings, account_id)
