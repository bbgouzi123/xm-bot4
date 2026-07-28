"""
消息解析
2026-03-08 根据真实 UIA 数据重写

实际消息 ListItem 结构（来自 RecyclerListView）：
  - mmui::ChatBubbleReferItemView (Name='图片')
  - mmui::ChatTextItemView (Name='消息文本')
  - mmui::ChatBubbleItemView (Name='表情/链接等')
  - mmui::ChatVoiceItemView (Name='语音')
  - mmui::ChatPersonalCardItemView (Name='名片')
  
判断消息方向（是否自己发的）：
  - 通过控件位置判断（自己的消息偏右，对方偏左）
  - 或通过子控件的布局分析
"""
import time
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# 时间标记正则（匹配 "12:14"、"2024年12月12日 15:04"、"昨天 13:08" 等）
_TIME_PATTERNS = [
    re.compile(r'^\d{1,2}:\d{2}$'),                                        # 12:14
    re.compile(r'^(昨天|前天|星期[一二三四五六日天])\s*\d{1,2}:\d{2}$'),                # 昨天 13:08
    re.compile(r'^(上午|下午|早上|中午|晚上|半夜)?\s*\d{1,2}:\d{2}$'),                  # 下午 02:30
    re.compile(r'^\d{1,2}月\d{1,2}日\s*\d{1,2}:\d{2}$'),                    # 6月3日 16:15
    re.compile(r'^\d{1,2}月\d{1,2}日$'),                                  # 6月3日
    re.compile(r'^\d{4}年\d{1,2}月\d{1,2}日\s*\d{1,2}:\d{2}$'),              # 2024年12月12日 15:04
    re.compile(r'^\d{4}年\d{1,2}月\d{1,2}日$'),                            # 2024年12月12日
    re.compile(r'^\d{4}/\d{1,2}/\d{1,2}$'),                                # 2024/12/12
    re.compile(r'^\d{4}/\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2}$'),                # 2024/12/12 12:30
    re.compile(r'^\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2}$'),                      # 02/09 12:30
    re.compile(r'^\d{1,2}/\d{1,2}$'),                                      # 02/09
]


def _is_time_marker(cls: str, name: str) -> bool:
    """判断控件是否是时间标记（不是真正的消息）"""
    # 明确的时间标记
    if "TimeItem" in cls or "mmui::ChatTimeItemView" in cls:
        return True
    # ChatItemView 且不属于已知消息类型的容器，如果匹配时间正则，则是时间标记
    if "ChatItemView" in cls and cls not in (
        "mmui::ChatTextItemView", "mmui::ChatBubbleItemView",
        "mmui::ChatBubbleReferItemView", "mmui::ChatVoiceItemView",
        "mmui::ChatFileItemView", "mmui::ChatImageItemView",
        "mmui::ChatVideoItemView", "mmui::ChatPersonalCardItemView",
        "mmui::ChatSysItemView"
    ):
        s_clean = name.strip()
        for pat in _TIME_PATTERNS:
            if pat.match(s_clean):
                return True
    return False


# 消息类型 ClassName 映射
MSG_TYPE_MAP = {
    "mmui::ChatTextItemView": "text",
    "mmui::ChatBubbleItemView": "bubble",
    "mmui::ChatBubbleReferItemView": "reference",
    "mmui::ChatVoiceItemView": "voice",
    "mmui::ChatPersonalCardItemView": "card",
    "mmui::ChatImageItemView": "image",
    "mmui::ChatVideoItemView": "video",
    "mmui::ChatFileItemView": "file",
    "mmui::ChatSysItemView": "system",
    "mmui::ChatTimeItemView": "time",
}


def parse_message(ctrl, nickname: Optional[str] = None, session_name: Optional[str] = None, use_click_check: bool = False) -> Optional[dict]:
    """
    解析单条消息控件为字典，包含高级系统事件识别
    """
    try:
        cls = ctrl.ClassName or ""
        name = ctrl.Name or ""
        
        # 跳过时间标记
        if _is_time_marker(cls, name):
            return None
        
        # 跳过空 Name (如果为空，尝试从子控件中深度挖掘多媒体节点或子文本，防范微信4.x图片行无Name导致被过滤丢弃)
        if not name:
            for child, _ in uia.WalkControl(ctrl, maxDepth=3):
                c_cls = getattr(child, "ClassName", "")
                c_name = getattr(child, "Name", "")
                if c_name:
                    name = c_name
                    break
                elif "Image" in c_cls or "ChatImage" in c_cls:
                    name = "[图片]"
                    break
                elif "Video" in c_cls:
                    name = "[视频]"
                    break
                elif "Voice" in c_cls:
                    name = "[语音]"
                    break
                elif "File" in c_cls:
                    name = "[文件]"
                    break
            
        if not name:
            return None
 
        s_clean = name.strip()
        
        # 1. 优先提取高价值特殊系统事件（如撤回、新好友打招呼、系统回执）
        if "撤回" in s_clean:
            msg_type = "recall"
            content = "[撤回]"
        elif (s_clean.startswith("你已添加了") and s_clean.endswith("以上是打招呼的消息。")) or \
             (s_clean.startswith("你已添加了") and s_clean.endswith("现在可以开始聊天了。")) or \
             s_clean.startswith("以上是打招呼的消息"):
             msg_type = "greet"
             content = "[打招呼]"
        elif "SysItem" in cls or "ChatSysItemView" in cls:
            msg_type = "system"
            content = f"[{s_clean}]"
        else:
            # 2. 常规消息识别
            msg_type = "text"
            for cls_prefix, mtype in MSG_TYPE_MAP.items():
                if cls_prefix in cls:
                    msg_type = mtype
                    break
 
            # 兜底识别名片：如果类名或名称中含有名片特征，强制判定为 card
            if msg_type == "text" and ("ChatPersonalCard" in cls or "个人名片" in name or name.endswith("个人名片")):
                msg_type = "card"
 
            content = name
            
            # 特殊类型标记
            if msg_type == "image" or name in ("图片", "[图片]") or (msg_type == "reference" and name == "图片"):
                msg_type = "image"
                content = "[图片]"
            elif msg_type == "voice":
                # 尝试剥离形如 "语音3\"秒"、"语音 5 秒" 等微信自带的语音长度前缀，提取后面转文字的原文
                cleaned = re.sub(r'^语音\s*\d+[\s\\\"\'秒分]*(?:秒|分)?', '', name).strip()
                # 过滤可能残留的多余字符（如单独的引号、斜杠）或未转写情况下的残留
                cleaned = re.sub(r'^[\\\"\'`\s\-\:\：]+', '', cleaned).strip()
                if cleaned and "翻译" not in cleaned and "转写" not in cleaned:
                    content = f"[语音识别结果]: {cleaned}"
                    logger.info(f"[消息解析] 发现微信自带语音转文字结果，内容: {cleaned}")
                else:
                    content = "[语音]"
            elif msg_type == "card":
                content = "[名片]"
                logger.info(f"[消息解析] 成功识别并标记名片消息: 元素名='{name}', 类名='{cls}'")
            elif msg_type == "video" or name in ("视频", "[视频]"):
                msg_type = "video"
                content = "[视频]"
            elif msg_type == "file" or name in ("文件", "[文件]"):
                msg_type = "file"
                content = "[文件]"
 
 
        # 判断是否自己发的消息（系统、打招呼、撤回等消息类型绝对不属于自己发送）
        if msg_type in ("system", "greet", "recall"):
            is_self = False
        else:
            is_self = _detect_is_self(ctrl, nickname, session_name, use_click_check)
 
        return {
            "content": content,
            "type": msg_type,
            "isSelf": is_self,
            "timestamp": int(time.time()),
            "className": cls,
        }
 
    except Exception:
        return None
 
 
from src.uia.message_direction import detect_is_self as _detect_is_self
