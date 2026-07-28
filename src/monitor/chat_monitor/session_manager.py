import logging
from datetime import datetime
from typing import Dict, List
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

@dataclass
class AccountPartition:
    """账号分区"""
    account_id: str = ''
    task_map: Dict[str, dict] = field(default_factory=dict)
    message_cache: Dict[str, dict] = field(default_factory=dict)
    last_reply_cache: Dict[str, str] = field(default_factory=dict)
    suspended_sessions: Dict[str, float] = field(default_factory=dict)
    _last_cleanup: float = 0.0

    def should_cleanup(self) -> bool:
        return datetime.now().timestamp() - self._last_cleanup > 300

    def cleanup_expired_data(self) -> int:
        current_time = datetime.now().timestamp()
        expired_sessions = []
        for session_id, cache_data in list(self.message_cache.items()):
            if current_time - cache_data.get('timestamp', 0) > 600:
                expired_sessions.append(session_id)
        for session_id in expired_sessions:
            self.message_cache.pop(session_id, None)
            self.last_reply_cache.pop(session_id, None)
        self._last_cleanup = current_time
        return len(expired_sessions)

class SessionManagerLogic:
    """会话与缓存管理 Mixin"""
    
    def get_account_partition(self, account_id: str = '') -> AccountPartition:
        key = account_id
        if not key:
            key = getattr(self.driver, 'bot_wxid', None) or getattr(self.driver, '_wxid', None)
            if not key:
                from src.crm.account_data import get_active_account
                key = get_active_account()
        if key not in self._partitions:
            self._partitions[key] = AccountPartition(account_id=key)
        partition = self._partitions[key]
        if partition.should_cleanup():
            cleaned = partition.cleanup_expired_data()
            if cleaned > 0:
                logger.debug(f'账号分区 {key} 清理了 {cleaned} 个过期项')
        return partition

    def suspend_session(self, session_name: str, account_id: str = '') -> bool:
        try:
            partition = self.get_account_partition(account_id)
            partition.suspended_sessions[session_name] = datetime.now().timestamp()
            logger.info(f'会话 {session_name} 已挂起')
            return True
        except Exception as e:
            logger.error(f'挂起会话失败: {e}')
            return False

    def unsuspend_session(self, session_name: str, account_id: str = '') -> bool:
        try:
            partition = self.get_account_partition(account_id)
            if session_name in partition.suspended_sessions:
                del partition.suspended_sessions[session_name]
                logger.info(f'会话 {session_name} 已解除挂起')
                return True
            return False
        except Exception as e:
            logger.error(f'解除挂起失败: {e}')
            return False

    def is_session_suspended(self, session_name: str, account_id: str = '') -> bool:
        try:
            partition = self.get_account_partition(account_id)
            return session_name in partition.suspended_sessions
        except Exception:
            return False

    def cache_mass_sending_message(self, session_name: str, message: str):
        if session_name not in self._mass_sending_cache:
            self._mass_sending_cache[session_name] = {'messages': [], 'timestamp': 0}
        cache = self._mass_sending_cache[session_name]
        normalized = message.replace('\n', '').replace('\ufeff', '').strip()
        cache['messages'].append(normalized)
        cache['timestamp'] = datetime.now().timestamp()
        if len(cache['messages']) > 100:
            cache['messages'] = cache['messages'][-10:]

    def clear_mass_sending_cache(self, session_name: str = None):
        if session_name:
            self._mass_sending_cache.pop(session_name, None)
        else:
            self._mass_sending_cache.clear()

    def _update_message_cache(self, session_id: str, user_message: str,
                               reply_message: str,
                               partition: AccountPartition):
        if session_id not in partition.message_cache:
            partition.message_cache[session_id] = {
                'user_message': '', 'reply_messages': [], 'timestamp': 0}
        cache = partition.message_cache[session_id]
        if user_message:
            cache['user_message'] = user_message
            cache['reply_messages'] = []
        if reply_message:
            if isinstance(reply_message, str):
                cache['reply_messages'].append(reply_message)
            else:
                cache['reply_messages'].append(str(reply_message))
        cache['timestamp'] = datetime.now().timestamp()
        self._clean_expired_cache(partition)

    def _clean_expired_cache(self, partition: AccountPartition,
                             max_age: float = 3600):
        current_time = datetime.now().timestamp()
        expired = [
            sid for sid, cache in partition.message_cache.items()
            if current_time - cache.get('timestamp', 0) > max_age
        ]
        for sid in expired:
            partition.message_cache.pop(sid, None)
