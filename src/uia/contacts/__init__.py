"""联系人同步子包导出入口。

对外保持稳定导入：
- from src.uia.contacts import ContactSync
- from src.uia.contacts import request_avatar_detail_sync_pause
"""

from .constants import (
    clear_contact_sync_pause,
    is_contact_sync_pause_requested,
    request_contact_sync_pause,
)
try:
    import win32gui
    from .contact_sync import ContactSync
except ImportError:
    ContactSync = None

__all__ = [
    "ContactSync",
    "clear_contact_sync_pause",
    "request_contact_sync_pause",
    "is_contact_sync_pause_requested",
]
