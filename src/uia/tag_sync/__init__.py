from .chat_scene import ChatSceneMixin
from .moment_scene import MomentSceneMixin

class WeChatTagSync(ChatSceneMixin, MomentSceneMixin):
    """微信原生标签同步引擎
    
    聚合了 ChatSceneMixin 与 MomentSceneMixin 中的场景同步逻辑，
    保持原 WeChatTagSync 类的完整功能和 API 兼容性。
    """
    def __init__(self, driver):
        """初始化微信标签同步引擎
        
        Args:
            driver: WeChatDriver 实例
        """
        self.driver = driver
