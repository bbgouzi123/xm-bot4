import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

class ChatSceneMixin:
    """微信聊天窗口场景下标签同步及备注修改的代理 Mixin (已解耦)"""

    def apply_tags_from_chat(
        self,
        friend_name: str,
        tags: List[str],
    ) -> bool:
        """从聊天窗口给好友打标签"""
        from .sync_runner import WeChatTagRemarkSyncRunner
        drv = getattr(self, 'driver', self)
        runner = WeChatTagRemarkSyncRunner(drv)
        return runner.apply_tags_from_chat(friend_name, tags)

    def apply_remark_and_tags_from_chat(
        self,
        friend_name: str,
        remark: Optional[str] = None,
        tags: Optional[List[str]] = None,
        phone: Optional[str] = None,
    ) -> bool:
        """从聊天窗口给好友修改备注、打标签并同步填写电话"""
        from .sync_runner import WeChatTagRemarkSyncRunner
        drv = getattr(self, 'driver', self)
        runner = WeChatTagRemarkSyncRunner(drv)
        return runner.apply_remark_and_tags_from_chat(friend_name, remark, tags, phone)

    def get_friend_tags(self, profile_win) -> List[str]:
        """从好友资料弹窗中读取已有标签"""
        from .sync_runner import WeChatTagRemarkSyncRunner
        drv = getattr(self, 'driver', self)
        runner = WeChatTagRemarkSyncRunner(drv)
        return runner.get_friend_tags(profile_win)

