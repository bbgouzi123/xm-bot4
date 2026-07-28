import time
import logging
import re
import uiautomation as uia
from src.uia.session import clean_session_name, parse_session_name

logger = logging.getLogger(__name__)

def _click_context_menu_item(self, item, target_text: str) -> bool:
    """右键点击控件，并通过键盘快捷键选中第一个选项（置顶）进行点击"""
    try:
        # 确保在前台，因为右键菜单和键盘输入需要前台激活
        import ctypes
        if ctypes.windll.user32.GetForegroundWindow() != self.hwnd:
            from src.uia.retry.window_ops import ensure_wechat_foreground
            ensure_wechat_foreground(self.hwnd)
            
        # 使用 OS 级鼠标事件进行物理右键点击，避开 UIA COM 的事件订阅错误
        from src.uia.retry.clicks import physical_right_click
        
        rect = item.BoundingRectangle
        cx = (rect.left + rect.right) // 2
        cy = (rect.top + rect.bottom) // 2
        
        # 使用 physical_right_click，内部自动处理遮罩穿透与光标恢复
        physical_right_click(cx, cy, settle=0.06, restore_cursor=True)
        time.sleep(0.35)

        # 模拟键盘操作：向下键聚焦第一个菜单项（置顶），然后回车确认
        logger.info(f"[UIA] 发送键盘指令: {{Down}} + {{Enter}} 执行置顶操作...")
        uia.SendKeys("{Down}{Enter}")
        time.sleep(0.3)
        return True
    except Exception as e:
        logger.error(f"[UIA] 右键菜单快捷键操作异常: {e}")
        return False

def pin_session_impl(self, session_name: str) -> bool:
    """【xm-bot4核心】置顶指定的会话的实际实现"""
    try:
        # 1. 确保在聊天主页并寻找该会话
        self._ensure_chat_page()
        
        # 寻找会话
        session_list = self._find_session_list()
        target_item = None
        if session_list:
            children = session_list.GetChildren()
            for item in children[:20] if children else []:
                raw_name = (item.Name or "").strip()
                if raw_name:
                    parsed = parse_session_name(raw_name, real_name=session_name)
                    if parsed and clean_session_name(parsed.get("name", "")) == clean_session_name(session_name):
                        target_item = item
                        # 检查是否已经置顶
                        if parsed.get("isPinned") or "已置顶" in raw_name:
                            logger.info(f"[UIA] 会话 '{session_name}' 已经处于置顶状态，无需重复操作")
                            return True
                        break
        
        # 2. 如果当前列表中没看到，或者没找到，先 ChatWith 激活它，使其出现在列表顶部
        if not target_item:
            logger.info(f"[UIA] 会话 '{session_name}' 未在当前列表中显现，开始定位激活...")
            if not self.ChatWith(session_name, lock_input=False, foreground=True):
                logger.warning(f"[UIA] 无法定位并激活会话 '{session_name}'，置顶中止")
                return False
            time.sleep(0.3)
            # 重新寻找
            session_list = self._find_session_list()
            if session_list:
                children = session_list.GetChildren()
                for item in children[:20] if children else []:
                    raw_name = (item.Name or "").strip()
                    if raw_name:
                        parsed = parse_session_name(raw_name, real_name=session_name)
                        if parsed and clean_session_name(parsed.get("name", "")) == clean_session_name(session_name):
                            target_item = item
                            if parsed.get("isPinned") or "已置顶" in raw_name:
                                logger.info(f"[UIA] 会话 '{session_name}' 激活后已处于置顶状态")
                                return True
                            break

        if not target_item:
            logger.warning(f"[UIA] 未能在会话列表中找到 '{session_name}' 控件，置顶失败")
            return False

        # 3. 执行右键菜单置顶
        logger.info(f"[UIA] 开始为会话 '{session_name}' 执行右键置顶操作...")
        return _click_context_menu_item(self, target_item, "置顶")
    except Exception as e:
        logger.error(f"[UIA] 执行会话置顶失败: {e}")
        return False
