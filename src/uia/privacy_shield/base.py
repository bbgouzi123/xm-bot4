import threading

class PrivacyShieldBase:
    """隐私保护遮罩基础状态实现"""

    def __init__(self):
        if hasattr(self, "_initialized") and self._initialized:
            return
        self._initialized = True

        self._enabled = False
        self._shield_hwnd = None
        self._wechat_hwnd = None
        self._tracking_thread = None
        self._stop_event = threading.Event()
        self._wndclass_registered = False
        self._config_path = ""
        self._acrylic_ok = False       # 毛玻璃是否成功启用
        self._wechat_nickname = ""     # 当前保护的微信昵称
        self._wechat_avatar_path = ""  # 微信头像文件路径
        self._fallback_avatar_path = ""
        self._fallback_nickname = ""
        self._fallback_lock = threading.Lock()  # 防止兜底头像并发加载
        self._bypass_depth = 0             # bypass_shield 可重入深度计数器
        self._record_mode = False          # 录屏保护模式：True 时 overlay 隐藏真实昵称/头像

    @property
    def enabled(self) -> bool:
        return self._enabled
