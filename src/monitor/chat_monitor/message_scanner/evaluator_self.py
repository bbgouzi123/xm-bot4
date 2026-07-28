import logging

logger = logging.getLogger(__name__)

class EvaluatorSelfMixin:
    """自回复避免判定逻辑"""

    def _check_is_self_sent(self, name: str, last_msg: str, is_group: bool, active_name: str, active_last_msgs: list, account_id: str, unread_count: int = 0, target_wxid: str = "") -> bool:
        # 🌟 微信消息明细数据库最高优先级校验
        wxid_to_check = target_wxid
        if not wxid_to_check:
            try:
                from src.utils.contacts_cache import contacts_cache
                friends = contacts_cache.get_friends(account_id) or []
                groups = contacts_cache.get_groups(account_id) or []
                for item in (friends + groups):
                    if item.get("name") == name or item.get("remark") == name or item.get("nickname") == name or item.get("wxid") == name:
                        wxid_to_check = item.get("wxid", "")
                        if wxid_to_check:
                            break
            except Exception as e:
                logger.debug(f"[自回复校验] 逆向匹配 wxid 异常: {e}")

        if wxid_to_check:
            try:
                if hasattr(self, '_wcdb_session_monitor') and self._wcdb_session_monitor:
                    recent_msgs = self._wcdb_session_monitor.get_latest_messages(wxid_to_check, limit=3)
                    if recent_msgs:
                        is_self_sent_db = bool(recent_msgs[-1].get("is_self"))
                        if is_self_sent_db:
                            logger.info(f"[自回复拦截] 从底层数据库最新消息确认发送方为自己，拦截自动回复。Wxid: {wxid_to_check}")
                            return True
            except Exception as db_ex:
                logger.debug(f"[自回复拦截] 查询底层数据库确认发送方异常: {db_ex}")

        # 🌟 黄金法则 1: 如果有未读红点（unread > 0），在微信机制中这必然是对方发送的新消息，绝对不可能是自己发送的
        if unread_count > 0:
            return False

        is_self_sent = False
        last_msg_strip = last_msg.strip()
        nickname = getattr(self.driver, '_nickname', '')
        if not nickname:
            try:
                from src.crm.account_data import get_active_nickname
                nickname = get_active_nickname()
            except Exception:
                pass
        bot_wxid = getattr(self.driver, 'bot_wxid', '') or getattr(self.driver, '_wxid', '')

        # 🌟 黄金法则 2: 如果是群聊，通过提取真实的发言人昵称做精确匹配，而不是用模糊的 startswith，防止重名或内容前缀冲突
        if is_group and last_msg_strip:
            colon_idx = last_msg_strip.find(':')
            if colon_idx == -1:
                colon_idx = last_msg_strip.find('：')
            if colon_idx != -1:
                sender_part = last_msg_strip[:colon_idx].strip()
                # 剥离系统可能附加的 [@所有人] / [有人@我] 等标签
                for prefix_to_strip in ("[@所有人]", "[有人@我]"):
                    sender_part = sender_part.replace(prefix_to_strip, "")
                sender_part = sender_part.strip()
                
                self_names = ["我"]
                if nickname:
                    self_names.append(nickname)
                if bot_wxid:
                    self_names.append(bot_wxid)
                if sender_part in self_names:
                    is_self_sent = True
                    logger.info(f"[自回复拦截] 群聊最新发言人识别为自己({sender_part})，拦截自动回复")

        # 🌟 黄金法则 3: 对于当前活跃的聊天详情窗口，优先且只信任 UIA 的物理相对位置（头像左右侧）判定结果
        if not is_self_sent and last_msg_strip:
            if name == active_name and active_last_msgs:
                try:
                    last_bubble_sender, last_bubble_content = active_last_msgs[-1]
                    driver_nickname = getattr(self.driver, '_nickname', '') or '我'
                    if last_bubble_sender in (driver_nickname, "我", "自己"):
                        is_self_sent = True
                        logger.info(f"[自回复拦截] 从活跃聊天气泡判定最新消息为自己发送: '{last_bubble_content}'")
                except Exception:
                    pass

            # 🌟 黄金法则 4: 非活跃/无红点 Peek 判定，只允许对比内存中极短时间窗口（如 15 秒）内由机器人发送出去的缓存记录
            if not is_self_sent:
                try:
                    partition = self.get_account_partition(account_id)
                    cache = partition.message_cache.get(name)
                    
                    reply_candidates = []
                    if cache and cache.get('reply_messages'):
                        import time as _t
                        cache_age = _t.time() - cache.get('timestamp', 0)
                        # 将时间窗口限制在 15 秒内，超出该时间即便内容一致也视为对方的有效回复
                        if cache_age < 15:
                            reply_candidates.extend(cache['reply_messages'])
                            
                    # ⚠️ 彻底废除去 SQLite/历史上下文数据库读取 assistant 历史聊天记录作为拦截候选的逻辑，
                    # 避免长周期内容碰撞误判。

                    if reply_candidates:
                        for r in reply_candidates:
                            if not isinstance(r, str) or not r.strip():
                                continue
                            r_norm = r.replace('\n', '').replace('\ufeff', '').strip()
                            last_norm = last_msg_strip.replace('\n', '').replace('\ufeff', '')
                            # 如果是群聊，需剥离发言人前缀再进行内容精准比对
                            if is_group:
                                colon_idx = last_norm.find(':')
                                if colon_idx == -1:
                                    colon_idx = last_norm.find('：')
                                if colon_idx != -1:
                                    last_norm = last_norm[colon_idx + 1:].strip()
                                    
                            if r_norm == last_norm:
                                is_self_sent = True
                                logger.info(f"[自回复拦截] 从极短内存缓存匹配成功，判定最新消息为机器人自己发送: '{r}'")
                                break
                            truncated = last_norm.rstrip('….').rstrip('.')
                            if truncated and len(truncated) >= 6 and r_norm.startswith(truncated):
                                is_self_sent = True
                                logger.info(f"[自回复拦截] 从极短内存缓存模糊前缀匹配成功，判定最新消息为机器人自己发送: '{r}'")
                                break
                except Exception as ex:
                    logger.debug(f"[自回复拦截] 执行自回复校验异常: {ex}")
        return is_self_sent
