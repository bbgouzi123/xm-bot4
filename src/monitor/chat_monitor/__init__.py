"""
聊天监控器 V3（模块化拆分版）
"""
from .base import ChatMonitorBase
from .session_manager import SessionManagerLogic
from .check_utils import CheckUtilsLogic
from .message_scanner import MessageScannerLogic
from .reply_engine import ReplyEngineLogic
from .tag_syncer import TagSyncerLogic

class ChatMonitor(
    SessionManagerLogic,
    CheckUtilsLogic,
    MessageScannerLogic,
    ReplyEngineLogic,
    TagSyncerLogic,
    ChatMonitorBase
):
    """
    聊天监控器 V3
    
    通过 Mixin 模式组装各个功能模块：
    - SessionManagerLogic: 会话与分区管理
    - CheckUtilsLogic: 过滤与校验助手
    - MessageScannerLogic: UIA 扫描循环
    - ReplyEngineLogic: AI 回复与工作流
    - TagSyncerLogic: CRM 标签同步
    - ChatMonitorBase: 基础状态与生命周期
    """
    def __init__(self, driver, ai_service):
        # 显式调用 base 的初始化
        ChatMonitorBase.__init__(self, driver, ai_service)
