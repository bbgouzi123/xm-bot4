import logging
from src.utils.uia_task_runner import run_uia_with_timeout

logger = logging.getLogger(__name__)

class PostActionsMixin:
    """超时监控、自动备份与滚动位置还原"""

    async def _post_check_actions(self, sessions: list, active_name: str, active_last_msgs: list):
        account_id = self.account_id
        if not account_id or account_id == 'default':
            return

        for s in sessions:
            n = s.get('name', '')
            if n:
                self._last_unread_snapshot[n] = int(s.get('unread', 0) or 0)

        # 🌟 人工接管响应超时与催单监控 (Top 3)
        try:
            from src.monitor.chat_monitor.takeover_timeout import process_takeover_timeouts
            process_takeover_timeouts(
                account_id=account_id,
                sessions=sessions,
                active_name=active_name,
                active_last_msgs=active_last_msgs,
                driver_nickname=getattr(self.driver, "_nickname", "") or "我"
            )
        except Exception as timeout_monitor_err:
            logger.error(f"[超时预警] 执行超时监控发生异常: {timeout_monitor_err}")

        # 🌟 微信封号保障箱 - 每日自动备份 (Top 2 Extension)
        try:
            from src.monitor.chat_monitor.auto_backup import trigger_daily_auto_backup
            trigger_daily_auto_backup(account_id=account_id)
        except Exception as auto_backup_err:
            logger.error(f"[自动备份] 执行自动备份发生异常: {auto_backup_err}")

        # 还原列表位置：如果在这一轮扫描中曾向下滚动过会话列表，在扫描结束时利用双击消息图标技巧快速回弹至顶部 (失败则用滚动兜底)
        if getattr(self, "_did_scroll_down_this_turn", False):
            try:
                # 只有在没有活跃自动回复任务时才允许双击消息图标回弹，以防干扰当前正在进行的聊天回复
                has_active_reply_tasks = bool(self._processing) or bool(self._message_buffer)
                success = False
                if not has_active_reply_tasks:
                    logger.info("[监控] 扫描与处理周期结束，优先尝试双击消息图标回弹至列表顶部...")
                    from src.uia.input_guard import uia_lock as _uia_lock
                    with _uia_lock("扫描结束，正在还原会话列表位置...", hwnd=getattr(self.driver, 'hwnd', None)):
                        success = await run_uia_with_timeout(self.driver.jump_to_next_unread, 10.0)
                
                if not success:
                    logger.info("[监控] 双击回弹条件未满足或未成功，执行滚轮向上滚动兜底...")
                    await run_uia_with_timeout(lambda: self.driver.scroll_sessions("up", times=3), 10.0)
            except Exception as e:
                logger.error(f"[监控] 双击回弹顶部发生异常，尝试执行滚轮向上滚动兜底. 异常: {e}")
                try:
                    await run_uia_with_timeout(lambda: self.driver.scroll_sessions("up", times=3), 10.0)
                except Exception as scroll_err:
                    logger.error(f"[监控] 滚轮向上滚动兜底亦失败: {scroll_err}")
            finally:
                self._did_scroll_down_this_turn = False
        
        self._first_scan_cycle_done = True
