import logging
import time
import asyncio
from src.utils.contacts_cache import contacts_cache
from src.utils.uia_task_runner import run_uia_with_timeout
from .utils import check_friend_in_list, check_group_in_list, is_acknowledgement_message

logger = logging.getLogger(__name__)

class EvaluatorPeekMixin:
    """文字跳动（无红点）切屏 Peek 安全穿透校验"""

    async def _should_peek_for_unread_zero(
        self, name: str, is_group: bool, last_msg: str, reply_cfg: dict,
        friend_excludes: list, group_excludes: list, is_friend_accept_notify: bool,
        friend_name_to_wxid: dict, group_name_to_wxid: dict, account_id: str
    ) -> bool:
        # 1. 全局白名单
        if self._whitelist_enabled and self._whitelist:
            if not any(w in name for w in self._whitelist):
                return False
                
        # 2. 人工接管状态
        if any(f.get("is_takeover", False) for f in contacts_cache.get_friends(account_id) if f.get("name") == name or f.get("wxid") == name):
            return False
            
        # 3. SDR 销售助手跟单挂起
        try:
            from src.task.auto_follow_daemon import is_session_locked
            if is_session_locked(name):
                return False
        except Exception:
            pass

        # 4. 人工干预挂起
        try:
            from src.utils.rest_time import get_rest_config
            rest_cfg = get_rest_config(account_id)
            suspend_secs = int(rest_cfg.get("manual_suspend_minutes", 30)) * 60
        except Exception:
            suspend_secs = 30 * 60
        last_intervention = self._manual_interventions.get(name, 0)
        if time.time() - last_intervention < suspend_secs:
            return False

        # 5. 防爆词免回复词汇
        if is_acknowledgement_message(last_msg):
            return False

        # 6. 黑白名单校验
        if is_group:
            bot_group_auto_start = reply_cfg.get("bot_group_auto_start", False)
            if not bot_group_auto_start:
                return False
            else:
                import re
                g_wxid = group_name_to_wxid.get(re.sub(r'[\(（]\d+[\)）]$', '', name).strip(), "") or group_name_to_wxid.get(name, "")
                group_mode = reply_cfg.get("auto_chat_group_mode", "black")
                in_group_list = check_group_in_list(name, g_wxid, group_excludes, account_id=account_id) or check_group_in_list(re.sub(r'[\(（]\d+[\)）]$', '', name).strip(), g_wxid, group_excludes, account_id=account_id)
                if group_mode == "black" and in_group_list:
                    return False
                elif group_mode == "white" and not in_group_list:
                    return False
        else:
            f_wxid = friend_name_to_wxid.get(name, "")
            friend_mode = reply_cfg.get("auto_chat_friend_mode", "black")
            in_friend_list = check_friend_in_list(name, f_wxid, friend_excludes, account_id=account_id)
            if friend_mode == "black" and in_friend_list:
                return False
            elif friend_mode == "white" and not in_friend_list:
                if not is_friend_accept_notify:
                    return False

        return True

    async def _peek_chat_and_validate(self, name: str, fp: str) -> bool:
        try:
            # 🌟 引入全局排他锁，彻底杜绝扫描器 Peek 动作与正在运行的自动回复工作流强抢前台物理界面的冲突
            from src.monitor.chat_monitor.reply_engine import _workflow_lock
            async with _workflow_lock:
                switched = await run_uia_with_timeout(self.driver.ChatWith, 15.0, name)
                if not switched:
                    attempts = self._peek_attempts.get(name, 0) + 1
                    self._peek_attempts[name] = attempts
                    if attempts >= 3:
                        self._fingerprints.setdefault(name, set()).add(fp)
                        self._peek_attempts.pop(name, None)
                    return False
                
                asyncio.create_task(self._collect_chat_data(name, is_force_fetch=True))
                last_msgs = await run_uia_with_timeout(self.driver.get_all_messages, 15.0, False, 3, name, True)
                if last_msgs:
                    self._peek_attempts.pop(name, None)
                    sender, content = last_msgs[-1][0], last_msgs[-1][1] or ""
                    driver_nickname = getattr(self.driver, '_nickname', '') or '我'
                    if sender in (driver_nickname, "战友", "我", "自己") or sender in ("SYS", "Time", "Recall"):
                        if sender not in ("SYS", "Time", "Recall"):
                            # 🌟 【加固】根据用户最高指示，取消检测到真人发送后的 30 分钟拉黑挂起机制。
                            # 遇到自己发送的消息本轮仍安全跳过（无需重发），但不会拉黑会话，未来对方一发新消息立刻能再次自动回复！
                            is_bot_reply = False
                            try:
                                account_id = getattr(self.driver, 'bot_wxid', None) or getattr(self.driver, '_wxid', None) or 'default'
                                partition = self.get_account_partition(account_id)
                                cache = partition.message_cache.get(name)
                                if cache and cache.get('reply_messages'):
                                    import time as _t
                                    cache_age = _t.time() - cache.get('timestamp', 0)
                                    if cache_age < 25:
                                        for r in cache['reply_messages']:
                                            if not isinstance(r, str) or not r.strip():
                                                continue
                                            r_norm = r.replace('\n', '').replace('\ufeff', '').strip()
                                            content_norm = content.replace('\n', '').replace('\ufeff', '').strip()
                                            if r_norm == content_norm:
                                                is_bot_reply = True
                                                break
                            except Exception:
                                pass

                            if not is_bot_reply:
                                logger.info(f"[自回复识别] 最新消息为真人手动发送: '{content[:40]}...'，本轮跳过，但根据设定不挂起该会话。")
                            else:
                                logger.info(f"[自回复识别] 最新消息为机器人自动回复: '{content[:40]}...'，本轮正常忽略。")

                        self._fingerprints.setdefault(name, set()).add(fp)
                        return False
                else:
                    attempts = self._peek_attempts.get(name, 0) + 1
                    self._peek_attempts[name] = attempts
                    if attempts >= 3:
                        self._fingerprints.setdefault(name, set()).add(fp)
                        self._peek_attempts.pop(name, None)
                    return False
        except Exception:
            attempts = self._peek_attempts.get(name, 0) + 1
            self._peek_attempts[name] = attempts
            if attempts >= 3:
                self._fingerprints.setdefault(name, set()).add(fp)
                self._peek_attempts.pop(name, None)
            return False
        return True
