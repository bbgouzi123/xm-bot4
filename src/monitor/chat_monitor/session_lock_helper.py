import logging
from typing import Optional

logger = logging.getLogger(__name__)

class SessionLockMixin:
    """会话锁的双重 key 助手 (Display Name 与 WxID 互相隔离锁，杜绝同名混淆锁死)"""

    def _is_session_processing(self, name: str, wxid: Optional[str] = None) -> bool:
        if wxid:
            return wxid in self._processing
        if name:
            return name in self._processing
        return False

    def _mark_session_processing(self, name: str, wxid: Optional[str] = None):
        if wxid:
            self._processing.add(wxid)
        elif name:
            self._processing.add(name)
        logger.debug(f"[会话锁] 标记锁定: name={name}, wxid={wxid}, 当前锁定集合: {self._processing}")

    def _clear_session_processing(self, name: str, wxid: Optional[str] = None):
        if wxid:
            self._processing.discard(wxid)
        if name:
            self._processing.discard(name)
        logger.debug(f"[会话锁] 释放锁定: name={name}, wxid={wxid}, 当前锁定集合: {self._processing}")


def resolve_wxid_from_cache(contacts_cache, account_id: str, name: str) -> Optional[str]:
    try:
        import re
        _n = name.strip()
        for _c in ((contacts_cache.get_friends(account_id) or []) + (contacts_cache.get_groups(account_id) or [])):
            _cw = (_c.get('wxid') or '').strip()
            _cn = re.sub(r'[\(（]\d+[\)）]$', '', (_c.get('name') or '')).strip()
            if _cn == _n or (_c.get('remark') or '').strip() == _n or _cw == _n:
                return _cw or None
    except Exception:
        pass
    return None
