"""
全局快捷键管理器
(重构版：使用 keyboard 底层键盘钩子，解决纯 Win32 RegisterHotKey 因 UIPI 或热键冲突导致全局失效的问题)
"""
import threading
import httpx
from xm_py_server.runtime_urls import LOOPBACK_HOST, http_origin

from src.utils.uia_logger import get_logger

logger = get_logger("HotkeyManager")

_BOT4_LOCAL_API = http_origin(LOOPBACK_HOST, 42041)

class GlobalHotkeyManager:
    _instance = None
    
    def __init__(self):
        self.running = False
        self.hotkeys = {}
        self._thread = None
        
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register_hotkey(self, key_str, callback):
        # 包装回调，另起线程执行防止阻塞底层输入流
        def _safe_callback():
            try:
                threading.Thread(target=callback, daemon=True).start()
            except Exception as e:
                logger.error(f"[全局热键] 回调触发异常: {e}")
                
        self.hotkeys[key_str] = _safe_callback
        return key_str

    def start(self):
        if self.running:
            return
            
        logger.info("[全局热键] 底层监听引擎启动")
        self.running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="Win32HotkeyThread")
        self._thread.start()

    def _run_loop(self):
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()

        vk_map = {
            'f1': 0x70, 'f2': 0x71, 'f3': 0x72, 'f4': 0x73, 'f5': 0x74, 'f6': 0x75,
            'f7': 0x76, 'f8': 0x77, 'f9': 0x78, 'f10': 0x79, 'f11': 0x7A, 'f12': 0x7B,
            'a': 0x41, 'b': 0x42, 'c': 0x43, 'd': 0x44, 'e': 0x45, 'f': 0x46,
            'g': 0x47, 'h': 0x48, 'i': 0x49, 'j': 0x4A, 'k': 0x4B, 'l': 0x4C,
            'm': 0x4D, 'n': 0x4E, 'o': 0x4F, 'p': 0x50, 'q': 0x51, 'r': 0x52,
            's': 0x53, 't': 0x54, 'u': 0x55, 'v': 0x56, 'w': 0x57, 'x': 0x58,
            'y': 0x59, 'z': 0x5A,
        }

        # 允许 WM_HOTKEY 消息通过 UIPI 隔离
        try:
            user32.ChangeWindowMessageFilter(0x0312, 1) # MSGFLT_ADD = 1
        except Exception:
            pass

        registered = {}
        hotkey_id = 1000

        for k_str, cb in self.hotkeys.items():
            vk = vk_map.get(k_str.lower())
            if not vk and len(k_str) == 1:
                vk = user32.VkKeyScanW(ord(k_str)) & 0xFF
            
            if vk:
                if user32.RegisterHotKey(None, hotkey_id, 0, vk):
                    registered[hotkey_id] = cb
                    logger.debug(f"[全局热键] 成功注册全局热键: {k_str} (ID={hotkey_id})")
                    hotkey_id += 1
                else:
                    logger.error(f"[全局热键] 注册热键 {k_str} 失败")
            else:
                logger.error(f"[全局热键] 无法解析热键键值: {k_str}")

        if not registered:
            self.running = False
            return

        msg = wintypes.MSG()
        while self.running:
            res = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if res <= 0:
                break
            
            if msg.message == 0x0312: # WM_HOTKEY
                cb = registered.get(msg.wParam)
                if cb:
                    cb()
            
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        # 注销所有热键
        for h_id in registered.keys():
            user32.UnregisterHotKey(None, h_id)

    def stop(self):
        if self.running:
            self.running = False
            # 发送一个空的 WM_NULL 消息给线程以唤醒 GetMessageW 并退出
            try:
                import ctypes
                user32 = ctypes.windll.user32
                if self._thread and self._thread.is_alive() and getattr(self, "_thread_id", None):
                    # 也可以调用 PostThreadMessageW 发送 WM_QUIT 消息
                    # 为简便起见，由于 GetMessageW 收到了非正值会结束，我们在注销中让它退出
                    # 这里直接通过 Python 状态位来终结
                    user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)
            except Exception:
                pass


def f8_toggle_bot():
    """F8 触发后的回调处理：调用本地 API 启停自动聊天"""
    try:
        # 先查询当前状态
        with httpx.Client() as client:
            resp = client.get(f"{_BOT4_LOCAL_API}/api/system/bot/status", timeout=2.0)
            if resp.status_code == 200:
                data = resp.json()
                is_running = False
                if "running" in data:
                    is_running = data["running"]
                elif "data" in data and isinstance(data["data"], dict) and "running" in data["data"]:
                    is_running = data["data"]["running"]
                
                # 切换状态
                if is_running:
                    logger.info("[全局热键] F8 触发 -> 停止自动聊天")
                    client.post(f"{_BOT4_LOCAL_API}/api/system/bot/stop", timeout=2.0)
                else:
                    logger.info("[全局热键] F8 触发 -> 启动自动聊天")
                    client.post(f"{_BOT4_LOCAL_API}/api/system/bot/start", timeout=2.0)
    except Exception as e:
        logger.error(f"[全局热键] F8 触发接口请求失败: {e}")


def init_global_hotkeys():
    """初始化全局快捷键"""
    mgr = GlobalHotkeyManager.get_instance()
    # 注册 F8
    mgr.register_hotkey('f8', f8_toggle_bot)
    
    # 提示：ESC 紧急停止功能现在由 StopSignal 的底层轮询监听器负责，
    # 不再使用 keyboard 钩子，以避免全局按键冲突和刷新页面时的误触发。

    mgr.start()
