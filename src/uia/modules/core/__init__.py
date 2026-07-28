"""WeChatDriver 核心能力包：子模块按职责拆分，本文件仅做多继承拼装（对外导出 ``WeChatCoreMixin``）。"""

from .connect import WeChatCoreConnectMixin
from .init import WeChatCoreInitMixin
from .profile import WeChatCoreProfileMixin
from .sessions import WeChatCoreSessionsMixin
from .state import WeChatCoreStateMixin
from .walk import WeChatCoreWalkMixin
from .walk_helper import WeChatCoreWalkHelperMixin


class WeChatCoreMixin(
    WeChatCoreInitMixin,
    WeChatCoreWalkMixin,
    WeChatCoreWalkHelperMixin,
    WeChatCoreProfileMixin,
    WeChatCoreStateMixin,
    WeChatCoreConnectMixin,
    WeChatCoreSessionsMixin,
):
    """窗口连接、账号资料提取、会话列表与控件深搜等核心能力。

    包内模块：
    - ``init`` — 状态字段
    - ``walk`` — WalkControl / BFS
    - ``profile`` — 昵称 / wxid / 头像
    - ``state`` — 连接态、分辨率、置前、当前用户
    - ``connect`` — 发现窗口与 UIA 绑定
    - ``sessions`` — 会话列表
    - ``preview_helpers`` — 头像预览 / 另存为纯函数（复用 ``try_click``）
    """

    # ==================== 朋友圈操作 ====================
    # （具体实现见 WeChatMomentsMixin）
