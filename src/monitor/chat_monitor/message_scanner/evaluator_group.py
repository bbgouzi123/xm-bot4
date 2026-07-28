import re
import asyncio
import logging
from src.utils.contacts_cache import contacts_cache

logger = logging.getLogger(__name__)

class EvaluatorGroupMixin:
    """群聊判定与群聊@消息状态处理"""

    def _resolve_is_group(self, session: dict, name: str, last_msg: str, friend_name_to_wxid: dict, group_name_to_wxid: dict) -> bool:
        is_group = session.get('isGroup', False)
        clean_name = re.sub(r'[\(（]\d+[\)）]$', '', name).strip()
        nickname = getattr(self.driver, '_nickname', '')
        
        if clean_name in group_name_to_wxid or name in group_name_to_wxid:
            is_group = True
        elif clean_name in friend_name_to_wxid or name in friend_name_to_wxid:
            is_group = False

        if not is_group:
            has_group_suffix = bool(re.search(r'[\(（]\d+[\)）]$', name)) or bool(re.search(r'[\(（]\d+[\)）]$', clean_name))
            if has_group_suffix or '、' in name or '、' in clean_name:
                is_group = True
            else:
                from src.uia.session import session_type_cache
                cached_type = session_type_cache.get_type(name) or session_type_cache.get_type(clean_name)
                if cached_type == "group":
                    is_group = True
                elif cached_type == "friend":
                    is_group = False
                else:
                    if ":" in last_msg or "：" in last_msg:
                        has_group_format = False
                        if ":" in last_msg:
                            has_group_format = bool(re.match(r'^[^\s:：、]{1,30}:\s', last_msg))
                        elif "：" in last_msg:
                            has_group_format = bool(re.match(r'^[^\s:：、]{1,30}：', last_msg))
                        
                        if has_group_format:
                            parts = last_msg.split(":", 1) if ":" in last_msg else last_msg.split("：", 1)
                            prefix = parts[0].strip()
                            is_valid_sender = (
                                0 < len(prefix) <= 35 and 
                                not prefix.lower() in ("http", "https", "ftp", "file", "ws", "wss") and 
                                not any(prefix.endswith(ext) for ext in (".com", ".cn", ".net", ".org")) and
                                prefix not in (nickname, "我", "自己")
                            )
                            if is_valid_sender:
                                is_group = True
                    if not is_group and ("[有人@我]" in last_msg or last_msg.startswith("[有人@我]")):
                        is_group = True

        from src.uia.session import session_type_cache
        if is_group:
            session['isGroup'] = True
            has_group_suffix = bool(re.search(r'[\(（]\d+[\)）]$', name)) or bool(re.search(r'[\(（]\d+[\)）]$', clean_name))
            is_in_known_groups = (clean_name in group_name_to_wxid or name in group_name_to_wxid)
            has_at_prefix = "[有人@我]" in last_msg or last_msg.startswith("[有人@我]")
            if is_in_known_groups or has_group_suffix or has_at_prefix:
                session_type_cache.set_type(name, "group")
                if clean_name != name:
                    session_type_cache.set_type(clean_name, "group")
        else:
            session['isGroup'] = False
            session_type_cache.set_type(name, "friend")
            if clean_name != name:
                session_type_cache.set_type(clean_name, "friend")

        return is_group

    def _check_group_receipt(self, last_msg: str, is_group: bool, reply_cfg: dict) -> bool:
        auto_receipt_enabled = reply_cfg.get("auto_receipt_enabled", False)
        custom_receipt_keywords = reply_cfg.get("custom_receipt_keywords", [])
        
        is_valid_receipt_announcement = False
        if is_group and auto_receipt_enabled:
            is_announcement = False
            last_msg_clean = last_msg.replace('\u2005', ' ').replace('\u200b', '').strip()
            for all_tag in ("所有人", "all", "All"):
                pattern = re.compile(rf'@[\s\u2005]*{re.escape(all_tag)}', re.IGNORECASE)
                if pattern.search(last_msg_clean) or pattern.search(last_msg):
                    is_announcement = True
                    break
            if is_announcement:
                matched_kw = False
                for kw in ("回", "扣", "答", "签", "吱", "阅", "1", "2", "打卡"):
                    if kw in last_msg:
                        matched_kw = True
                        break
                if not matched_kw and custom_receipt_keywords:
                    for kw in custom_receipt_keywords:
                        if kw.strip() and kw.strip() in last_msg:
                            matched_kw = True
                            break
                if matched_kw:
                    is_valid_receipt_announcement = True
        return is_valid_receipt_announcement

    def _check_group_at(self, session: dict, last_msg: str, is_group: bool, is_valid_receipt_announcement: bool, reply_cfg: dict) -> bool:
        # 🌟 修复前置：@所有人 群公告消息，无论 UIA 是否标记红点、WCDB 引擎是否激活，必须最优先放行
        # 微信对 @所有人 不会产生"[有人@我]"红点，导致 session.isAt 始终为 False，此处前置拦截保证其必然触发回复
        if is_group:
            _clean = last_msg.replace('\u2005', ' ').replace('\u200b', '').strip()
            for _tag in ('所有人', 'all', 'All'):
                if re.search(rf'@[\s\u2005]*{re.escape(_tag)}', _clean, re.IGNORECASE) or \
                   re.search(rf'@[\s\u2005]*{re.escape(_tag)}', last_msg, re.IGNORECASE):
                    session['isAt'] = True
                    return True

        from src.api.config_api import _load_configs
        configs = _load_configs() or {}
        respond_to_all_mentions = reply_cfg.get("respond_to_all_mentions", False)
        
        is_at = session.get('isAt', False)
        if not is_at and is_group:
            from src.monitor.chat_monitor.check_utils import check_is_at_message
            # 🛡️ 严格限缩检测：只判定当前到达的最新消息（last_msg）本身是否为 @我 或 [有人@我]
            # 彻底杜绝历史残留未读@对后面普通消息判定的时序污染
            is_at = check_is_at_message(last_msg, self.driver, self.account_id, reply_cfg)


        if not is_at and is_valid_receipt_announcement:
            is_at = True
        
        # 移除 @所有人 消息过滤逻辑：即使未开启 respond_to_all_mentions，根据用户要求，@所有人 消息也必须回复
        
        if is_at:
            session['isAt'] = True
        return is_at

    def _ignore_un_at_group(self, name: str, last_msg: str, fp: str):
        last_seen_fp = self._last_seen_msg.get(name)
        if fp != last_seen_fp:
            logger.info(f"[监控] 会话 '{name}' 设置了群聊仅在被@时回复，当前未被@，跳过自动回复并广播忽略状态")
            try:
                from src.utils.websocket_manager import ws_manager
                loop = asyncio.get_running_loop()
                if loop and loop.is_running():
                    asyncio.ensure_future(ws_manager.broadcast_task_update(
                        task_id=f"auto_reply_{name}",
                        task_type="自动回复",
                        status="completed",
                        progress=100,
                        total=100,
                        message="群聊消息未@，已自动忽略",
                        friend_name=name,
                        incoming_msg=last_msg
                    ))
            except Exception as ws_ex:
                logger.debug(f"[WS] 广播跳过群聊回复异常: {ws_ex}")
        
        self._last_seen_msg[name] = fp
        self._fingerprints.setdefault(name, set()).add(fp)
        self._initialized.add(name)

    def _parse_group_sender(self, last_msg: str, default_user: str) -> str:
        last_msg_strip = last_msg.strip()
        
        # Clean prefix tags like [有人@我], [@所有人], [系统消息], etc.
        # Use regex to strip any bracketed prefixes at the start
        pattern = re.compile(r'^(\[[^\]]+\]\s*)*')
        match = pattern.match(last_msg_strip)
        if match:
            prefix_len = match.end()
            body = last_msg_strip[prefix_len:].strip()
        else:
            body = last_msg_strip

        # Find the first colon in the remaining body
        colon_idx = body.find(':')
        if colon_idx == -1:
            colon_idx = body.find('：')
            
        if colon_idx != -1 and colon_idx < 35:
            sender = body[:colon_idx].strip()
            # Ensure sender is not just a bracketed item or something like http
            if sender and not (sender.startswith('[') and sender.endswith(']')):
                if not sender.lower() in ("http", "https", "ftp", "file", "ws", "wss"):
                    return sender
                    
        return default_user

