from pathlib import Path

from src.utils.contact_sync_checkpoint import ContactSyncCheckpointStore
from .profile_parser import ContactProfileMixin
from .query_tags import ContactQueryTagMixin
from .session import ContactBaseMixin
from .storage import ContactStorageMixin
from .sync_contacts import ContactSyncAllMixin
from .sync_details import ContactSyncDetailsMixin
from .lazy_sync import ContactLazySyncMixin


class ContactSync(
    ContactSyncDetailsMixin,
    ContactSyncAllMixin,
    ContactProfileMixin,
    ContactQueryTagMixin,
    ContactStorageMixin,
    ContactBaseMixin,
    ContactLazySyncMixin,
):
    """联系人同步操作（按职责拆分为多个 mixin 组装）。"""

    CONTACTS_FILE = Path.home() / ".xm-ai-bot" / "contacts.json"
    CONTACTS_TAGS_FILE = Path.home() / ".xm-ai-bot" / "contact_tags.json"

    def __init__(self, driver):
        self.driver = driver
        self._checkpoint_store = ContactSyncCheckpointStore()

