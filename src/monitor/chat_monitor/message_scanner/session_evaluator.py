import time
import hashlib
import re
import asyncio
import logging
from src.utils.contacts_cache import contacts_cache
from src.utils.uia_task_runner import run_uia_with_timeout

logger = logging.getLogger(__name__)

class SessionEvaluatorMixin:
    """会话队列逐一扫描与策略评估"""

    async def _evaluate_single_session(self, session: dict, active_name: str, active_last_msgs: list, active_chat_fp: str, user_active_now: bool, reply_cfg: dict, friend_excludes: list, group_excludes: list, friend_name_to_wxid: dict, group_name_to_wxid: dict, unread_private_sessions_count: int, account_id: str):
        name = session.get('name', '')
        if not name or session.get('isOfficial', False) or name in self.SYSTEM_SESSIONS or name.startswith('折叠的聊天'):
            return

        is_wcdb_active = False
        if hasattr(self, "_wcdb_session_monitor") and self._wcdb_session_monitor:
            try:
                # 只要微信数据库正常连接，即视为活跃
                is_wcdb_active = self._wcdb_session_monitor.is_active()
            except Exception:
                pass

        if is_wcdb_active and name != active_name:
            # 🌟 修复：@所有人 群公告消息即使在 WCDB 模式下非激活窗口，也必须穿透评估，不能被静默过滤
            _last_msg_pre = session.get('lastMessage', '') or ''
            _has_at_all = any(
                re.search(rf'@[\s\u2005]*{tag}', _last_msg_pre, re.IGNORECASE)
                for tag in ('所有人', 'all', 'All')
            )
            if not _has_at_all:
                return
            # 🐛 [双发Bug修复] WCDB激活时，@所有人 群公告消息会被两个独立通道同时处理：
            #   • DB 通道（db_unread_syncer → process_unread_session）：轮询 session.db，检测到未读后入队
            #   • UIA 穿透通道（本处）：扫描到含 @所有人 的会话后也强行入队
            # 两条通道共用同一个 _enqueue_to_reply_buffer，导致缓冲区先被 DB 通道填充、
            # 再被 UIA 通道因缓冲区已清空（弹出后 key 消失）而二次入队，最终触发两次回复。
            #
            # ✅ 根本修复：WCDB 激活期间，@所有人 消息的感知职责完全归属 DB 通道（与整体策略一致）。
            # UIA 穿透通道在此直接退出，彻底消除竞争条件与双发根源。
            # （DB 通道在 db_unread_syncer_helper.py 中已对 is_at 和 @所有人 做了完整处理）
            logger.debug(f"[双发防御] WCDB 激活时 @所有人 会话 '{name}' 完全交由 DB 通道处理，UIA 穿透通道跳过")
            return

        last_msg = session.get('lastMessage', '') or ''
        if not last_msg.strip():
            return

        last_time = session.get('lastTime', '') or ''
        unread_count = session.get('unread', 0)

        # 🌟 极速过滤历史陈旧消息：如果未读消息的时间显示不是今天，则视为陈旧堆积，不进行自动回复。
        # 今天的时间格式在微信中通常为 "15:30" 或 "下午 3:30"，不包含 "昨天"、"前天"、星期、日期或特殊年份。
        is_msg_today = True
        if last_time:
            last_time_strip = str(last_time).strip().lower()
            old_keywords = ["昨天", "前天", "星期", "周", "月", "日", "年", "-", "/", "yesterday"]
            en_days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
            if any(kw in last_time_strip for kw in old_keywords) or any(d in last_time_strip for d in en_days):
                is_msg_today = False
        
        if unread_count > 0 and not is_msg_today:
            fp_temp = hashlib.md5(f"{name}:{last_time}:{last_msg}:{unread_count}".encode()).hexdigest()
            self._fingerprints.setdefault(name, set()).add(fp_temp)
            logger.info(f"[监控] 会话 '{name}' unread={unread_count}，但时间 '{last_time}' 判定为非今天陈旧消息，直接跳过并拦截。")
            return

        # 1. 过滤名片消息
        fp = hashlib.md5(f"{name}:{last_time}:{last_msg}:{unread_count}".encode()).hexdigest()
        if "[名片]" in last_msg or "[个人名片]" in last_msg or "个人名片" in last_msg:
            if fp not in self._fingerprints.get(name, set()):
                self._fingerprints.setdefault(name, set()).add(fp)
                logger.info(f"[监控] 会话 '{name}' 最新消息为名片类型，已过滤并跳过自动回复。指纹: {fp}")
            return

        # 4. 判断是否是群聊与 WxID
        is_group = self._resolve_is_group(session, name, last_msg, friend_name_to_wxid, group_name_to_wxid)
        target_wxid = group_name_to_wxid.get(name, "") if is_group else friend_name_to_wxid.get(name, "")

        # UIA 轮询模式下，实时检测新消息是否为红包或转账，并执行抢红包/转账
        is_redpacket = False
        is_transfer = False
        last_msg_lower = last_msg.lower()
        if any(x in last_msg or x in last_msg_lower for x in ("[微信红包]", "[红包]", "微信红包")):
            if "你领取了" not in last_msg and "已拆" not in last_msg:
                is_redpacket = True
        elif ("[转账]" in last_msg or "微信转账" in last_msg or "待你收款" in last_msg or "你有一笔待接收的转账" in last_msg or "待接收的转账" in last_msg) and "已收款" not in last_msg and "已收" not in last_msg:
            is_transfer = True
            
        if (is_redpacket or is_transfer):
            redpacket_enabled = bool(reply_cfg.get("auto_redpacket_friend_enabled", False))
            if not is_group and redpacket_enabled:
                logger.info(f"[监控] 发现未处理红包或转账会话: '{name}'，触发自动抢红包/收款")
                # 注入抢红包/转账 RPA 任务
                asyncio.create_task(
                    self.driver.claim_redpacket(target_wxid or name, is_group)
                )
                # 记录指纹，避免重复触发
                self._fingerprints.setdefault(name, set()).add(fp)
                return
            else:
                # 群聊，或者未开启自动领取的私聊，直接忽略拦截，不触发自动回复
                logger.info(f"[监控] 会话 '{name}' 收到红包或转账，不满足自动领取条件，已主动忽略。指纹: {fp}")
                self._fingerprints.setdefault(name, set()).add(fp)
                self._last_seen_msg[name] = fp
                return

        if self._is_session_processing(name, target_wxid):
            self._initialized.add(name)
            return

        # 2. 活跃窗口无红点新消息穿透判定
        if name == active_name and active_chat_fp and not user_active_now:
            if active_chat_fp not in self._fingerprints.setdefault(name, set()):
                if active_last_msgs:
                    sender, content = self._extract_bubble_info(active_last_msgs[-1])
                    driver_nickname = getattr(self.driver, '_nickname', '') or '我'
                    is_friend_sender = sender not in (driver_nickname, "我", "自己", "SYS", "Time", "Recall", "GREET")
                    
                    # 💡 【大厂实践优化】此处不再对物理气泡已判定为对方的消息执行 _check_is_self_sent 文本内容二次校验，
                    # 避免好友发送与机器人历史回复相同内容时造成误拦截。

                    if is_friend_sender:
                        if content == "[名片]" or "[个人名片]" in content or "个人名片" in content:
                            self._fingerprints[name].add(active_chat_fp)
                            logger.info(f"[监控] 活跃会话 '{name}' 最新穿透消息为名片类型，已主动过滤跳过自动回复。指纹: {active_chat_fp}")
                            return
                        self._fingerprints[name].add(active_chat_fp)
                        session["unread"] = 1
                        session["lastMessage"] = content
                        last_msg = content
                        unread_count = 1
                        fp = hashlib.md5(f"{name}:{last_time}:{content}:1".encode()).hexdigest()
                        logger.info(f"[监控] 活跃窗口 '{name}' 探测到无红点新消息 (指纹={active_chat_fp})，强行放行自动回复: {content}")

        # 3. 手机端特权指令操控
        if await self._handle_privilege_command(name, last_msg, session, friend_name_to_wxid, group_name_to_wxid, account_id):
            logger.info(f"[监控] 会话 '{name}' 触发手机端特权指令，跳过自动回复")
            return


        # 5. 防止自回复循环（如果最后一条消息是自己发的）
        from .session_evaluator_helper import handle_self_sent_and_takeover
        if handle_self_sent_and_takeover(self, name, last_msg, is_group, active_name, active_last_msgs, account_id, unread_count, target_wxid, session, friend_name_to_wxid, group_name_to_wxid, fp):
            return

        # 6. 新好友迎新词与群打卡回执公告检测
        is_friend_accept_notify = self._check_friend_acceptance(last_msg, last_time)
        
        # 🌟 根据用户最高指示：新加好友的通过验证消息，必须是未读且消息是当天的才进行回复
        if is_friend_accept_notify:
            unread_count = int(session.get('unread', 0) or 0)
            is_msg_today = True
            if last_time:
                last_time_strip = str(last_time).strip().lower()
                old_keywords = ["昨天", "前天", "星期", "周", "月", "日", "年", "-", "/", "yesterday"]
                en_days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
                if any(kw in last_time_strip for kw in old_keywords) or any(d in last_time_strip for d in en_days):
                    is_msg_today = False
            if unread_count <= 0 or not is_msg_today:
                is_friend_accept_notify = False

        is_valid_receipt_announcement = self._check_group_receipt(last_msg, is_group, reply_cfg)

        # 7. @检测
        is_at = self._check_group_at(session, last_msg, is_group, is_valid_receipt_announcement, reply_cfg)

        # 8. 微信公众号/系统通知二次过滤
        if not is_group and not is_friend_accept_notify:
            if not self._check_known_contact(name, account_id, fp):
                return

        # 9. 免打扰与黑白名单拦截
        if is_group and not is_at and session.get('isMuted', False):
            logger.info(f"[监控] 会话 '{name}' 是免打扰群聊且未被@，跳过自动回复")
            return

        bot_group_auto_start = reply_cfg.get("bot_group_auto_start", False)
        auto_chat_group_at_only = reply_cfg.get("auto_chat_group_at_only", True)

        if is_group and auto_chat_group_at_only and not is_at:
            # 🌟 [艾特热度衰减融合] 即使物理上未被@，但在 5分钟热度期内也应该放行！
            from src.monitor.chat_monitor.group_mention_decay import group_mention_decay_mgr
            g_key = target_wxid or name
            user_name_val = session.get('userName', '')
            if not user_name_val:
                user_name_val = self._parse_group_sender(last_msg, name)
            if group_mention_decay_mgr.check_and_update_heat_dual(g_key, user_name_val, None, last_msg):
                is_at = True
                session['isAt'] = True
                logger.info(f"[热度衰减监控] UIA 扫描检测到群聊 '{name}' 中的用户 '{user_name_val}' 处于免 @ 活跃期，已放行。")
            else:
                if unread_count > 0:
                    self._ignore_un_at_group(name, last_msg, fp)
                return

        # 10. 历史消息静默与首次初始化校验
        if name not in self._initialized:
            should_skip = self._handle_first_seen_session(
                name, unread_count, last_time, is_group,
                unread_private_sessions_count, is_friend_accept_notify, fp, session, is_at
            )
            if should_skip:
                return
        else:
            last_seen_fp = self._last_seen_msg.get(name)
            self._last_seen_msg[name] = fp
            
            is_re_request = False
            re_request_keywords = ["再发", "重发", "没收到", "重新", "再来", "发一份", "发一遍", "发个"]
            if any(kw in last_msg for kw in re_request_keywords):
                is_re_request = True

            if fp != last_seen_fp:
                from src.utils.uia_task_runner import is_session_fused, report_uia_success
                if is_session_fused(name):
                    logger.info(f"[监控] 熔断会话 '{name}' 探测到新消息传入，执行熔断状态自愈重置")
                    report_uia_success(name)
                asyncio.create_task(self._collect_chat_data(name, is_force_fetch=False))
            
            if session.get('unread', 0) == 0:
                if not is_re_request and (fp == last_seen_fp or fp in self._fingerprints.setdefault(name, set())):
                    return
                # 媒体消息 Peek 避让
                if last_msg.strip() in ("[图片]", "[视频]", "[文件]", "[语音]", "图片", "视频", "文件", "语音"):
                    if name != active_name:
                        logger.debug(f"[监控] 会话 '{name}' 无红点且非当前打开，最新消息为媒体占位符 '{last_msg}'，忽略切屏 Peek 并标记指纹")
                        self._fingerprints[name].add(fp)
                        return
                partition = self.get_account_partition()
                if self._is_auto_reply_message(name, last_msg, partition):
                    logger.info(f"[监控] 会话 '{name}' unread=0, 判定为自动回复消息，跳过")
                    self._fingerprints[name].add(fp)
                    return
                from src.utils.user_activity import is_user_active
                if name != active_name and is_user_active(cooldown_ms=800):  # 优化：1500ms→800ms，减少误漏Peek
                    logger.info(f"[监控] 会话 '{name}' 无红点且用户活跃且非当前打开，跳过本次扫描，等待空闲后 Peek")
                    return
                
                # 前置安全避让校验 (防止抢焦点)
                if not await self._should_peek_for_unread_zero(name, is_group, last_msg, reply_cfg, friend_excludes, group_excludes, is_friend_accept_notify, friend_name_to_wxid, group_name_to_wxid, account_id):
                    logger.info(f"[监控] 会话 '{name}' unread=0 (文字跳动)，但其不满足自动回复白黑名单等配置条件，跳过切屏 Peek 并标记指纹")
                    self._fingerprints[name].add(fp)
                    return

                logger.info(f"[{name}] 无红点但文字跳动且用户空闲，启动切屏穿透校验 Peek...")
                if not await self._peek_chat_and_validate(name, fp):
                    return
            else:
                if not is_re_request and fp in self._fingerprints.setdefault(name, set()):
                    from .session_evaluator_helper import handle_unread_fingerprint_check
                    if handle_unread_fingerprint_check(self, name, fp, session, reply_cfg, is_group, friend_excludes, group_excludes, friend_name_to_wxid, group_name_to_wxid, account_id, is_friend_accept_notify, target_wxid):
                        return

        # 11. 公众号/撤回拦截与人工干预、白黑名单过滤校验
        if self._is_official_account(name) or last_msg.endswith('撤回了一条消息'):
            logger.info(f"[监控] 会话 '{name}' 是公众号或属于撤回消息，跳过")
            self._fingerprints[name].add(fp)
            return

        if not self._check_manual_intervention_and_acknowledgement(name, last_msg, fp, reply_cfg, account_id, unread_count=unread_count):
            return

        if self.is_session_suspended(name) or self._is_mass_sending_message(name, last_msg):
            self._ignore_suspended_or_mass_sent(name, last_msg, fp)
            return

        if not await self._validate_whitelist_rules(name, is_group, clean_name=re.sub(r'[\(（]\d+[\)）]$', '', name).strip(), friend_excludes=friend_excludes, group_excludes=group_excludes, friend_name_to_wxid=friend_name_to_wxid, group_name_to_wxid=group_name_to_wxid, reply_cfg=reply_cfg, is_friend_accept_notify=is_friend_accept_notify, fp=fp, last_msg=last_msg):
            return

        # 12. 同事屏蔽与冷却时间检查
        user_name = session.get('userName', name)
        if is_group:
            user_name = self._parse_group_sender(last_msg, user_name)
            if self._colleague_names and user_name in self._colleague_names:
                logger.info(f"[监控] 群聊 '{name}' 发言人 '{user_name}' 为同事，跳过")
                self._fingerprints[name].add(fp)
                return
            if self._group_at_only and not is_at:
                if unread_count > 0:
                    self._ignore_un_at_group(name, last_msg, fp)
                return

        partition = self.get_account_partition()
        if self._is_auto_reply_message(name, last_msg, partition):
            logger.info(f"[监控] 会话 '{name}' 判定为自动回复消息，跳过")
            self._fingerprints[name].add(fp)
            return

        if not hasattr(self, '_last_reply_msg'):
            self._last_reply_msg = {}

        is_new_content = True
        if name in self._last_reply_msg:
            if self._last_reply_msg[name].strip() == last_msg.strip():
                is_new_content = False

        if name in self._last_reply_time and time.time() - self._last_reply_time[name] < self._cooldown:
            if not is_new_content:
                logger.debug(f"[监控] 会话 '{name}' 处于全局频控冷却中且内容无变化，跳过")
                return

        # 13. 入列自动回复消息队列与广播通知
        target_wxid = None
        if is_group:
            target_wxid = group_name_to_wxid.get(re.sub(r'[\(（]\d+[\)）]$', '', name).strip(), "") or group_name_to_wxid.get(name, "")
        else:
            target_wxid = friend_name_to_wxid.get(name, "")

        self._enqueue_to_reply_buffer(name, last_msg, is_group, user_name, is_at, fp, wxid=target_wxid)

    async def _scan_and_enqueue_active_chat_async(self, active_name: str, active_last_msgs: list):
        """
        [扫尾防御] 异步处理活跃会话的扫尾检测，代理给 active_chat_helper 以防本文件过大。
        """
        from ..active_chat_helper import scan_and_enqueue_active_chat_async_impl
        await scan_and_enqueue_active_chat_async_impl(self, active_name, active_last_msgs)
