from .v2 import ContactSyncV2Mixin
from .legacy import ContactSyncLegacyMixin
from .manager import ContactManagerOpsMixin

class ContactSyncAllMixin(ContactSyncV2Mixin, ContactSyncLegacyMixin, ContactManagerOpsMixin):
    """
    联系人同步 V2 — 基于「通讯录管理」窗口的扁平列表方案。

    核心改进：
    - 不再依赖侧边栏的分组展开/折叠
    - 改用微信内置的「通讯录管理」窗口
    - 极大提升同步速度和可靠性
    """
    pass
