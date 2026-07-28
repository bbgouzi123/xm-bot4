"""
message_direction_util.py
辅助判定是否为自己发的消息的纯 UI 节点查找与类型过滤函数
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

def check_nickname_or_me_in_ctrl(ctrl, nickname: Optional[str], ctrl_rect, scale: float) -> bool:
    """
    通过在 UIA 树中深度/广度遍历昵称和“我”文字节点来判断是否为自己发送的。
    """
    if not nickname:
        return False
        
    stack = [ctrl]
    while stack:
        curr = stack.pop()
        c_name = curr.Name or ""
        c_cls = curr.ClassName or ""
        if c_name in (nickname, "我") and c_cls in ("Button", "Image", "Pane", "Custom", "Text"):
            try:
                curr_rect = curr.BoundingRectangle
                if curr_rect and curr_rect.width() > 0:
                    left_diff = curr_rect.left - ctrl_rect.left
                    # 如果匹配到昵称但位置偏向左侧，一般是群聊里别人发的消息中显示的群昵称，需要过滤
                    if c_name == nickname and left_diff < 70 * scale:
                        continue
            except Exception:
                pass
            return True
        try:
            children = curr.GetChildren()
            if children:
                stack.extend(children)
        except Exception:
            pass
    return False

def check_non_text_class_name(ctrl) -> bool:
    """
    检测当前控件是否为非文本控件（如图片、视频、文件、名片等），是则返回 True。
    """
    cls_name = ctrl.ClassName or ""
    return any(k in cls_name for k in ("Image", "Video", "File", "Card", "Voice", "App"))
