import logging

logger = logging.getLogger(__name__)

class EvaluatorFirstSeenMixin:
    """会话历史未读消息与首次检查静默策略"""

    def _handle_first_seen_session(
        self, name: str, unread_count: int, last_time: str, is_group: bool,
        unread_private_sessions_count: int, is_friend_accept_notify: bool, fp: str,
        session: dict, is_at: bool
    ) -> bool:
        self._initialized.add(name)
        self._last_seen_msg[name] = fp
        
        is_msg_today = True
        if last_time:
            last_time_strip = str(last_time).strip().lower()
            old_keywords = ["昨天", "前天", "星期", "周", "月", "日", "年", "-", "/", "yesterday"]
            en_days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
            if any(kw in last_time_strip for kw in old_keywords) or any(d in last_time_strip for d in en_days):
                is_msg_today = False

        # 判定是否属于真正堆积的历史未读（避免把今天有价值的未读也当成历史未读）
        is_stale_unread = False
        if unread_count > 0:
            if not is_msg_today:
                is_stale_unread = True
            elif not is_group and unread_private_sessions_count > 5:
                # 好友消息虽然是今天，但未读堆积太多，属于冷启动堆积
                is_stale_unread = True
            elif is_group:
                # 如果是今天发生的群聊未读：
                # 刚启动时，如果该群仅在@我时回复但并没有@我，我们把它视为stale静默
                # 反之，如果被@了，或者本群就不限@自动回复，我们绝对不能把它算为stale（放行）
                group_at_only = getattr(self, "_group_at_only", True)
                if group_at_only and not is_at:
                    is_stale_unread = True

        if is_stale_unread:
            if is_friend_accept_notify:
                logger.info(f"[{name}] 检测到新好友添加通过通知，放行自动回复迎新词")
                return False
            elif unread_count > 0 and not is_group and is_msg_today and unread_private_sessions_count <= 5:
                logger.info(f"[监控] 会话 '{name}' 首现（有未读={session.get('unread', 0)}），由于是今天发来且未读好友总数较少，放行")
                return False
            elif unread_count > 0 and is_group and is_msg_today:
                # 如果被@了或者不需要@，且是今天，即使判定为stale也在第一轮给予豁免放行
                group_at_only = getattr(self, "_group_at_only", True)
                if is_at or not group_at_only:
                    logger.info(f"[监控] 会话 '{name}' 首现（有未读={session.get('unread', 0)}），群聊且是今天且满足@规则，豁免放行回复")
                    return False
            
            # 其他真正的历史/堆积未读，直接静默并加入指纹库
            self._fingerprints.setdefault(name, set()).add(fp)
            logger.info(f"[监控] 会话 '{name}' 判定为历史/堆积未读，已加入指纹库静默跳过")
            return True
        else:
            u_count = session.get('unread', 0) or 0
            # 🌟 核心防误触：在冷启动首次看到会话时，如果其未读数 unread 已经是 0，
            # 那么即使其最新一条消息包含 @ (is_at=True)，它也必然是早就被处理过的已读历史消息，
            # 绝对不应该重复放行回复！只有当 unread > 0 或为新好友通过通知时，才允许放行。
            if u_count > 0 or is_friend_accept_notify:
                logger.info(f"[监控] 会话 '{name}' 在扫描器运行期间首次出现且有未读，放行自动回复。当前未读数={u_count}")
                return False
            else:
                self._fingerprints.setdefault(name, set()).add(fp)
                logger.info(f"[监控] 会话 '{name}' 在扫描器运行期间首次出现但无新消息，自动加入指纹库静默跳过")
                return True
