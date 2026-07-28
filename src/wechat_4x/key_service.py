import ctypes
import os
import sys

def get_dll_path():
    # 优先使用环境变量指定路径
    env_path = os.environ.get("WX_KEY_DLL_PATH")
    if env_path and os.path.exists(env_path):
        return env_path

    dll_name = 'sqlite3_secure.dll'
    
    # 1. PyInstaller 打包环境：兼容根目录、assets 子目录及 _internal/assets 子目录下的 DLL 定位
    if getattr(sys, 'frozen', False):
        meipass = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
        candidates_frozen = [
            os.path.join(os.path.dirname(sys.executable), dll_name),
            os.path.join(meipass, dll_name),
            os.path.join(meipass, 'assets', dll_name),
            os.path.join(os.path.dirname(sys.executable), '_internal', 'assets', dll_name),
            os.path.join(os.path.dirname(sys.executable), 'assets', dll_name),
        ]
        for p in candidates_frozen:
            if os.path.exists(p):
                return p

    # 2. 本地开发环境：在项目相关目录中查找
    here = os.path.dirname(os.path.abspath(__file__))                     # src/wechat_4x
    src_dir = os.path.dirname(here)                                        # src
    backend_dir = os.path.dirname(src_dir)                                 # backend-python
    product_dir = os.path.dirname(backend_dir)                             # xm-bot4

    candidates = [
        os.path.join(backend_dir, "assets", dll_name),
        os.path.join(product_dir, "wx", "WeFlow", "resources", dll_name),
        os.path.join(backend_dir, "resources", dll_name),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p

    # 3. 兜底策略：如果都找不到，返回默认开发环境下的 assets 路径
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base_dir, 'assets', dll_name)

class KeyService:
    def __init__(self):
        self.dll = None
        self.initialized = False
        
        self.InitHook = None
        self.PollKeyData = None
        self.GetStatusMessage = None
        self.CleanupHook = None
        self.GetLastErrorMsg = None
        self.GetImageKey = None   # WeFlow 方案：读取 kvcomm 缓存，派生图片密钥

        self._load_dll()

    def _load_dll(self):
        dll_path = get_dll_path()
        if not os.path.exists(dll_path):
            raise FileNotFoundError(f"DLL未找到，请确保已拷贝至: {dll_path}")
        
        self.dll = ctypes.CDLL(dll_path)
        
        # bool InitializeHook(uint32 targetPid, const char* token)
        self.InitHook = self.dll._x_init_session
        self.InitHook.argtypes = [
            ctypes.c_uint32, 
            ctypes.c_char_p, 
            ctypes.c_char_p, 
            ctypes.c_char_p, 
            ctypes.c_int
        ]
        self.InitHook.restype = ctypes.c_bool
        
        # bool PollKeyData(_Out_ char *keyBuffer, int bufferSize)
        self.PollKeyData = self.dll._x_poll_session
        self.PollKeyData.argtypes = [ctypes.c_char_p, ctypes.c_int]
        self.PollKeyData.restype = ctypes.c_bool
        
        # bool GetStatusMessage(_Out_ char *msgBuffer, int bufferSize, _Out_ int *outLevel)
        self.GetStatusMessage = self.dll._x_status_session
        self.GetStatusMessage.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        self.GetStatusMessage.restype = ctypes.c_bool
        
        # bool CleanupHook()
        self.CleanupHook = self.dll._x_term_session
        self.CleanupHook.restype = ctypes.c_bool
        
        # const char* GetLastErrorMsg()
        self.GetLastErrorMsg = self.dll._x_err_session
        self.GetLastErrorMsg.restype = ctypes.c_char_p

        # bool GetImageKey(_Out_ char *resultBuffer, int bufferSize)
        # 读取微信 kvcomm 缓存目录中的 code，返回 JSON：{accounts:[{wxid,keys:[{code}]}]}
        try:
            self.GetImageKey = self.dll._x_image_session
            self.GetImageKey.argtypes = [ctypes.c_char_p, ctypes.c_int]
            self.GetImageKey.restype = ctypes.c_bool
        except AttributeError:
            self.GetImageKey = None  # 旧版 DLL 未导出此函数，降级静默
        
        self.initialized = True

    def initialize_hook(self, pid: int) -> bool:
        """根据PID向目标微信进程注入Hook"""
        if not self.initialized:
            return False
        import datetime
        import hashlib
        today_str = datetime.date.today().strftime("%Y%m%d")
        salt = "XmCoreSecretSalt"
        token_src = today_str + salt
        token = hashlib.md5(token_src.encode('utf-8')).hexdigest().encode('utf-8')
        return self.InitHook(pid, token, None, None, 0)

    def poll_key_data(self) -> str:
        """非阻塞轮询是否获取到数据库密钥，返回16进制字符串或None"""
        if not self.initialized:
            return None
        buffer = ctypes.create_string_buffer(256)
        if self.PollKeyData(buffer, 256):
            return buffer.value.decode('utf-8')
        return None

    def get_status_message(self) -> tuple:
        """获取DLL内部状态日志，返回 (消息文本, 日志级别) 或 (None, None)
        
        注意：DLL 有时会返回只含换行符的消息（如 "\n"、"\r\n"），
        必须 strip() 后再判断有效性，防止空行被打印到控制台。
        """
        if not self.initialized:
            return None, None
        buffer = ctypes.create_string_buffer(256)
        level = ctypes.c_int(0)
        if self.GetStatusMessage(buffer, 256, ctypes.byref(level)):
            msg = buffer.value.decode('utf-8', errors='ignore').strip()
            return (msg if msg else None), level.value
        return None, None

    def cleanup_hook(self) -> bool:
        """清理并卸载Hook，执行内存脱钩自清"""
        if not self.initialized:
            return False
        return self.CleanupHook()

    def get_last_error(self) -> str:
        """获取最后一次DLL底层错误文本"""
        if not self.initialized:
            return ""
        err = self.GetLastErrorMsg()
        return err.decode('utf-8', errors='ignore') if err else ""
