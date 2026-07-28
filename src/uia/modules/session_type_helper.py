import re
import logging
from src.uia.elements import WxClass
from src.uia.session import session_type_cache
from src.uia.retry import exists_with_timeout

logger = logging.getLogger("WeChatDriver.SessionTypeHelper")

def get_chat_window_type_impl(self, who: str = "") -> str:
    """获取当前聊天窗口类型的实际实现"""
    try:
        if who == "文件传输助手": 
            return "file_transfer"
        
        # 从聊天主详情视图 (mmui::ChatDetailView) 中查找元素
        chat_container = self.root.GroupControl(ClassName='mmui::ChatDetailView')
        if chat_container and chat_container.Exists(1):
            # 1) 如果有 "公众号主页" 按钮，100% 为公众号 / 服务号，深度搜索限制为 3 以防深入消息列表
            official_btn = chat_container.ButtonControl(Name='公众号主页', searchDepth=3)
            if official_btn and official_btn.Exists(0.3):
                return "official_account"
            
            # 2) 如果有 "聊天信息" 按钮，说明是普通聊（群聊或好友聊），深度搜索限制为 3
            chat_info_btn = chat_container.ButtonControl(Name='聊天信息', searchDepth=3)
            if chat_info_btn and chat_info_btn.Exists(0.3):
                # 判断是群聊还是单聊：检查标题中是否带有群人数括号（如 "(5)"）
                for ctrl in chat_container.GetChildren():
                    if ctrl.ControlTypeName == "TextControl" and ctrl.Name:
                        if re.search(r'[（(]\d+[)）]', ctrl.Name):
                            return "group"
                # 如果标题控件中没有找到，我们用 "who" 兜底判断
                if who and re.search(r'[（(]\d+[)）]', who):
                    return "group"
                return "chat"
        
        # 保守旧逻辑兜底
        if who and any(kw in who for kw in ["公众号", "服务号", "订阅号", "微信团队", "微信游戏", "微信支付", "微信运动"]):
            return "official_account"
        if who and re.search(r'[（(]\d+[)）]', who): 
            return "group"
        input_box = self.root.EditControl(ClassName=WxClass.CHAT_INPUT)
        if input_box and exists_with_timeout(input_box, 1.0):
            return "chat"
        return "unknown"
    except Exception as e:
        logger.error(f"获取聊天窗口类型失败: {e}")
        return "unknown"

def detect_and_cache_session_type_impl(self, session_name: str):
    """自动检测并缓存当前会话的真实类型"""
    if not session_name:
        return
    
    # 排除文件传输助手等特殊名字
    if session_name in ["文件传输助手", "微信团队", "服务通知", "订阅号消息"]:
        return
        
    # 🌟 快速缓存拦截：如果已学到过真实会话类型，则直接跳过 UIA 物理检测，保障高负载下的顺畅切换，防止引发 Exists 超时
    if session_type_cache.get_type(session_name) in ["official_account", "group", "chat", "friend"]:
        return
        
    real_type = get_chat_window_type_impl(self, session_name)
    if real_type in ["official_account", "group", "chat"]:
        logger.info(f"[会话学习] 自动学习到会话 '{session_name}' 的真实类型为: {real_type}")
        try:
            session_type_cache.set_type(session_name, real_type)
        except Exception as cache_ex:
            logger.error(f"[会话学习] 写入类型缓存异常: {cache_ex}")
