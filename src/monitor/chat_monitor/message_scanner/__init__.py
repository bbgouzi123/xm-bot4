from .barrier import BarrierMixin
from .startup import StartupMixin
from .loop import LoopMixin
from .cleanup import CleanupMixin
from .active_chat import ActiveChatMixin
from .buffer_ops import BufferOpsMixin
from .session_loader import SessionLoaderMixin
from .filter_preparer import FilterPreparerMixin
from .evaluator_self import EvaluatorSelfMixin
from .evaluator_contact import EvaluatorContactMixin
from .evaluator_whitelist import EvaluatorWhitelistMixin
from .evaluator_peek import EvaluatorPeekMixin
from .evaluator_intervention import EvaluatorInterventionMixin
from .evaluator_group import EvaluatorGroupMixin
from .evaluator_reply import EvaluatorReplyMixin
from .evaluator_first_seen import EvaluatorFirstSeenMixin
from .session_evaluator import SessionEvaluatorMixin
from .post_actions import PostActionsMixin
from .check import CheckMixin
from .utils import check_friend_in_list, check_group_in_list


class MessageScannerLogic(
    BarrierMixin,
    StartupMixin,
    LoopMixin,
    CleanupMixin,
    ActiveChatMixin,
    BufferOpsMixin,
    SessionLoaderMixin,
    FilterPreparerMixin,
    EvaluatorSelfMixin,
    EvaluatorContactMixin,
    EvaluatorWhitelistMixin,
    EvaluatorPeekMixin,
    EvaluatorInterventionMixin,
    EvaluatorGroupMixin,
    EvaluatorReplyMixin,
    EvaluatorFirstSeenMixin,
    SessionEvaluatorMixin,
    PostActionsMixin,
    CheckMixin
):
    """
    UIA 扫描循环
    
    通过 Mixin 模式组装各个功能模块：
    - BarrierMixin: UIA 状态与账号元数据屏障
    - StartupMixin: 启动冷热检查
    - LoopMixin: 主扫描循环
    - CleanupMixin: 缓存清理与声音辅助
    - ActiveChatMixin: 活跃窗口检测与无红点穿透
    - BufferOpsMixin: 合并缓冲队列
    - SessionLoaderMixin: 会话列表与滚动跳转
    - FilterPreparerMixin: 自动回复配置隔离准备
    - EvaluatorSelfMixin: 判定是否是自己发的消息
    - EvaluatorContactMixin: 判断联系人有效性与迎新词
    - EvaluatorWhitelistMixin: 白黑名单回复限制校验
    - EvaluatorPeekMixin: 无未读文字跳动会话切屏穿透 Peek 校验
    - EvaluatorInterventionMixin: 人工干预与异常熔断/群发屏蔽校验
    - EvaluatorGroupMixin: 群聊分析与@指令校验
    - EvaluatorReplyMixin: 微信自动回复队列与状态广播
    - EvaluatorFirstSeenMixin: 首次扫码历史堆积未读屏蔽静默校验
    - SessionEvaluatorMixin: 逐一会话评估主调度
    - PostActionsMixin: 超时监控与备份回弹
    - CheckMixin: 主扫描入口编排
    """
    # 系统免回复前缀与微信内置特定前缀
    SYSTEM_SESSIONS = {
        "文件传输助手", "微信团队", "订阅号消息", "服务通知", "微信运动",
        "腾讯新闻", "微信支付", "群助手", "漂流瓶", "语音记事本",
        "mphelper", "fmessage", "qmessage"
    }
    SKIP_PREFIXES = ("我:", "我：", "[图片]", "[视频]", "[文件]", "[语音]", "图片", "视频", "文件", "语音")

