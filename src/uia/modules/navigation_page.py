import time
import logging

logger = logging.getLogger("WeChatDriver.NavigationPage")

def ensure_chat_page_impl(self, force: bool = False) -> bool:
    """确保当前激活在微信聊天主页 (Chats Tab)"""
    try:
        from src.uia.retry.clicks import try_click
        
        # 1. 如果不是强制刷新，先检查当前是否已经在聊天页面中（判断聊天输入框或会话详情视图是否存在）
        if not force:
            chat_container = self.root.GroupControl(ClassName='mmui::ChatDetailView')
            if chat_container.Exists(0.15):
                return True
            edit_area = self.root.EditControl(ClassName='mmui::ChatInputField')
            if edit_area.Exists(0.15):
                return True

        # 2. 如果不在，或被强制，则定位左侧导航 TabBar 上的“微信”按钮并点击
        tab_bar = self.root.Control(ClassName="mmui::MainTabBar")
        chat_tab = None
        if tab_bar.Exists(0.2):
            chat_tab = tab_bar.ButtonControl(Name="微信")
            if not chat_tab.Exists(0.1):
                chat_tab = tab_bar.Control(Name="微信")
                
        if not chat_tab or not chat_tab.Exists(0.15):
            # 兜底：直接在主窗口全局搜索微信 Tab 按钮
            chat_tab = self.root.ButtonControl(Name="微信")
            if not chat_tab.Exists(0.15):
                chat_tab = self.root.Control(Name="微信", ClassName="mmui::XTabBarItem")
                
        if chat_tab and chat_tab.Exists(0.2):
            logger.info("[UIA] 定位到 '微信' 导航按钮，准备点击切换回聊天主页")
            try_click(chat_tab, max_retries=2, delay=0.15)
            time.sleep(0.3)
            return True
            
        logger.warning("[UIA] 未能定位到 '微信' 导航按钮，尝试发送 Alt+W 快捷键兜底...")
        # 微信原生支持 Alt+W 打开/切换微信主界面或聊天 Tab 状态，可作为优秀辅助
        import uiautomation as uia
        uia.SendKeys("%w") # 发送 Alt+w 快捷键
        time.sleep(0.3)
        return True
    except Exception as e:
        logger.error(f"[UIA] ensure_chat_page 异常: {e}")
        return False
