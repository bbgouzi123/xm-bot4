import logging
from typing import Any
from src.utils.uia_task_runner import run_uia_with_timeout


logger = logging.getLogger(__name__)

class CheckUtilsLogic:
    """过滤与校验助手方法 Mixin"""
    
    OFFICIAL_KEYWORDS = ('微信团队', '微信支付', '腾讯', '官方')
    SKIP_PREFIXES = ('[图片]', '[视频]', '[语音]', '[文件]', '[位置]',
                     '[表情]', '[转账]', '[红包]', '[拍一拍]', '[系统]')
    SYSTEM_SESSIONS = ('文件传输助手', '微信团队', '服务通知', '微信支付',
                       '腾讯新闻', '订阅号消息', '公众号', '服务号')

    def _is_official_account(self, session_name: str) -> bool:
        return any(kw in session_name for kw in self.OFFICIAL_KEYWORDS)

    def _is_mass_sending_message(self, session_name: str, message: str) -> bool:
        cache = self._mass_sending_cache.get(session_name)
        if not cache:
            return False
        try:
            normalized_msg = message.replace('\n', '').replace('\ufeff', '').strip()
            cached_messages = cache.get('messages', [])
            if len(normalized_msg) > 45 and normalized_msg.endswith('…'):
                base = normalized_msg[:-3]
                return any(m.startswith(base) for m in cached_messages)
            return normalized_msg in cached_messages
        except Exception:
            return False

    def _is_auto_reply_message(self, session_id: str, message: str, partition) -> bool:
        cache = partition.message_cache.get(session_id)
        if not cache or not cache.get('reply_messages'):
            return False
        if not isinstance(message, str):
            return False
        normalized_msg = message.replace('\n', '').replace('\ufeff', '').strip()
        reply_messages = cache['reply_messages']
        
        normalized_replies = []
        for r in reply_messages:
            if isinstance(r, str):
                normalized_replies.append(r.replace('\n', '').replace('\ufeff', '').strip())
            elif isinstance(r, bool):
                continue
            elif r is not None:
                normalized_replies.append(str(r).replace('\n', '').replace('\ufeff', '').strip())

        if len(normalized_msg) > 45 and normalized_msg.endswith('…'):
            base = normalized_msg[:-3]
            if normalized_replies:
                last_reply = normalized_replies[-1]
                if isinstance(last_reply, str):
                    return last_reply.startswith(base)
        return normalized_msg in normalized_replies

    async def _collect_chat_data(self, name: str, is_force_fetch: bool = False):
        """对指定会话进行非阻塞聊天记录数据监控采集"""
        from src.uia.session import clean_session_name
        try:
            # 🌟 【UIA 排他锁避让】若当前其它前台交互动作（如模拟按键录音）已开启排他锁，
            # 立即跳过 UIA 聊天数据采集，彻底杜绝在物理按键按下期间因并发调用 UIA 导致系统挂起超时的 Bug！
            try:
                from src.uia.input_guard import uia_lock
                if uia_lock.is_locked:
                    logger.debug(f"[监控] 检测到 UIA 排他锁已开启，跳过对会话 {name} 的聊天记录采集")
                    return
            except Exception as e_lock:
                logger.debug(f"[监控] 避让检测 UIA 锁状态异常: {e_lock}")

            driver_nickname = getattr(self.driver, '_nickname', '') or '我'
            search_who = clean_session_name(name)
            
            def _check_exists():
                if not self.driver.root:
                    return False
                try:
                    from src.utils.safe_uia import find_active_input_control_safely, get_chat_container_safely
                    
                    # 1. 快速判定：先校验窗口标题。如果标题不匹配，直接返回 False，绝不进行耗时的 UIA 树遍历
                    active_title = find_active_input_control_safely(self.driver.root, getattr(self.driver, "hwnd", None))
                    if not active_title:
                        return False
                        
                    import re
                    def normalize_spaces(text: str) -> str:
                        return re.sub(r'\s+', ' ', text).strip()
                        
                    if normalize_spaces(active_title) != normalize_spaces(search_who):
                        return False
                        
                    # 🌟 优化：如果 Win32 标题已经精确匹配，即可断定当前处于目标会话中，
                    # 100% 豁免后续 UIA 树检索以避免 COM 线程阻塞卡死
                    return True
                except Exception as check_ex:
                    logger.debug(f"[数据监控] 快速校验会话存在性异常: {check_ex}")
                    return False
                
            is_current = await run_uia_with_timeout(_check_exists, 5.0)
            if is_current or is_force_fetch:
                last_msgs = await run_uia_with_timeout(self.driver.get_all_messages, 15.0, False, 3, name, True)
                if last_msgs:
                    mapped = []
                    for item in last_msgs:
                        if isinstance(item, (list, tuple)) and len(item) >= 2:
                            sender, content = item[0], item[1]
                        else:
                            sender, content = "未知", str(item)
                        role = "assistant" if sender in (driver_nickname, "我", "自己") else "user"
                        mapped.append({
                            "sender": sender,
                            "role": role,
                            "content": content
                        })
                    from src.utils.chat_collection import ChatCollectionManager
                    account_id = getattr(self.driver, 'bot_wxid', None) or getattr(self.driver, '_wxid', None) or 'default'
                    ChatCollectionManager.get_instance().collect_messages(account_id, name, mapped)
        except Exception as ex:
            logger.debug(f"[数据监控] 采集聊天记录异常 ({name}): {ex}")

    async def _handle_privilege_command(self, name: str, last_msg: str, session, friend_name_to_wxid, group_name_to_wxid, account_id) -> bool:
        """检查并处理手机端特权指令操控，如果匹配特权指令并处理了则返回 True，否则返回 False"""
        try:
            from src.api.config_api.base_config import _load_configs
            configs = _load_configs()
            if not configs.get("enable_privilege_commands", True):
                return False
        except Exception:
            pass

        clean_cmd = last_msg.strip()
        if clean_cmd not in (
            "[接管]", "[人工接管]", "[取消接管]", "[AI回复]", "/takeover", "/human", "/release", "/ai",
            "/global_stop_reply", "[关闭回复]", "/global_start_reply", "[开启回复]",
            "#继续", "#恢复托管", "#恢复", "#继续恢复", "[继续]", "[恢复]"
        ):
            return False
            
        import hashlib
        fp = hashlib.md5(f"{name}:{last_msg}".encode()).hexdigest()
        if name not in self._fingerprints:
            self._fingerprints[name] = set()
            
        if fp in self._fingerprints[name]:
            return True
            
        try:
            switched = await run_uia_with_timeout(self.driver.ChatWith, 15.0, name)
            if switched:
                last_msgs = await run_uia_with_timeout(self.driver.get_all_messages, 15.0, False, 2, name, True)
                if last_msgs:
                    last_msg_item = last_msgs[-1]
                    if isinstance(last_msg_item, (list, tuple)) and len(last_msg_item) >= 2:
                        sender, content = last_msg_item[0], last_msg_item[1]
                    else:
                        sender, content = "未知", str(last_msg_item)
                    driver_nickname = getattr(self.driver, '_nickname', '') or '我'
                    if sender in (driver_nickname, "我", "自己") and content.strip() == clean_cmd:
                        # 1. 全局控制指令处理
                        if clean_cmd in ("/global_stop_reply", "[关闭回复]"):
                            from src.utils.uia_task_runner import suspend_engine
                            suspend_engine("手机特权指令关闭")
                            reply_text = "[系统通知] 全局自动回复已暂停。"
                            await run_uia_with_timeout(self.driver.SendMsg, 15.0, name, reply_text)
                            self._fingerprints[name].add(fp)
                            return True
                        elif clean_cmd in ("/global_start_reply", "[开启回复]"):
                            from src.utils.uia_task_runner import resume_engine
                            resume_engine()
                            reply_text = "[系统通知] 全局自动回复已开启。"
                            await run_uia_with_timeout(self.driver.SendMsg, 15.0, name, reply_text)
                            self._fingerprints[name].add(fp)
                            return True

                        # 2. 单会话控制指令处理
                        target_state = clean_cmd in ("[接管]", "[人工接管]", "/takeover", "/human")
                        is_group = session.get('isGroup', False)
                        target_wxid = group_name_to_wxid.get(name, "") if is_group else friend_name_to_wxid.get(name, "")
                        target_key = target_wxid or name
                        
                        from src.utils.contacts_cache import contacts_cache
                        if target_key:
                            contacts_cache.update_friend(account_id, target_key, is_takeover=target_state)
                            contacts_cache.merge_friend_detail_by_name(account_id, name, "群聊" if is_group else "联系人", is_takeover=target_state)
                        
                        if target_state:
                            self._human_takeover_sessions.add(name)
                            if target_wxid:
                                self._human_takeover_sessions.add(target_wxid)
                        else:
                            keys_to_remove = [name]
                            if target_wxid:
                                keys_to_remove.append(target_wxid)
                            for k in keys_to_remove:
                                self._human_takeover_sessions.discard(k)
                                self._manual_interventions.pop(k, None)
                                try:
                                    partition = self.get_account_partition(account_id)
                                    partition.suspended_sessions.pop(k, None)
                                except Exception:
                                    pass

                        action_desc = "人工接管" if target_state else "AI自动回复"
                        logger.info(f"[特权指令] 会话 {name} 已切换为 {action_desc} 模式")
                        
                        reply_text = f"[系统通知] 会话已成功切换为 {action_desc} 模式。"
                        await run_uia_with_timeout(self.driver.SendMsg, 15.0, name, reply_text)
                        
                        self._fingerprints[name].add(fp)
                        return True
        except Exception as ex:
            logger.error(f"[特权指令] 物理指令处理异常: {ex}")
        return False

    def _check_group_at_context(self, name: str, user_name: str, account_id: str) -> bool:
        """检查群聊中虽然未@，但根据上下文用户是否正与机器人持续对话"""
        try:
            from src.utils.chat_history import ChatHistoryManager
            history_mgr = ChatHistoryManager(account_id)
            ctx = history_mgr.get_context(name, window_size=10)
            for m in reversed(ctx):
                if m.get("role") == "assistant" and m.get("content", "").startswith(f"@{user_name}"):
                    return True
                if m.get("role") == "user" and m.get("sender") == user_name and "@" in m.get("content", ""):
                    return True
        except Exception:
            pass
        return False

def check_is_at_message(message: str, driver: Any, account_id: str, reply_cfg: dict) -> bool:
    """
    检查消息中是否包含 @我 或 @所有人 提及标识
    """
    if not message:
        return False
    import re
    from src.api.config_api import _load_configs
    configs = _load_configs() or {}
    
    last_msg_clean = message.replace('\u2005', ' ').replace('\u200b', '').strip()
    
    # 兼容 [有人@我] 物理UI前缀
    if "[有人@我]" in last_msg_clean or last_msg_clean.startswith("[有人@我]"):
        return True
        
    nicknames_to_check = []
    nickname = getattr(driver, '_nickname', '') if driver else ''
    bot_wxid = getattr(driver, 'bot_wxid', '') or getattr(driver, '_wxid', '') or account_id
    if nickname:
        nicknames_to_check.append(nickname)
    if bot_wxid:
        nicknames_to_check.append(bot_wxid)
    bot_name_cfg = configs.get("bot_name", "")
    if bot_name_cfg:
        nicknames_to_check.append(bot_name_cfg)
    
    # 去除发送人前缀
    colon_idx = last_msg_clean.find(':')
    if colon_idx == -1:
        colon_idx = last_msg_clean.find('：')
    if colon_idx != -1 and colon_idx < 35:
        last_msg_clean = last_msg_clean[colon_idx + 1:].strip()

    for n in nicknames_to_check:
        if not n:
            continue
        pattern = re.compile(rf'@[\s\u2005]*{re.escape(n)}', re.IGNORECASE)
        if pattern.search(last_msg_clean) or pattern.search(message):
            return True
            
    # 🌟 @所有人 始终作为被 @ 提起的有效状态，确保群聊机器人必须回复该消息
    for all_tag in ("所有人", "all", "All"):
        pattern = re.compile(rf'@[\s\u2005]*{re.escape(all_tag)}', re.IGNORECASE)
        if pattern.search(last_msg_clean) or pattern.search(message):
            return True
                
    return False

