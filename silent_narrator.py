import ctypes
import ctypes.wintypes as ctypes_wintypes
import threading
import logging
import time
import atexit

logger = logging.getLogger(__name__)

SPI_GETSCREENREADER = 70
SPI_SETSCREENREADER = 71
SPIF_SENDCHANGE = 2

class SilentNarrator:
    """
    模拟 Narrator.exe 的系统存在感，让 Qt/微信开放 UIA 控件树，
    而无需真正启动 Windows 讲述人进程。

    工作原理：
      1. SystemParametersInfo(SPI_SETSCREENREADER, TRUE) + SPIF_SENDCHANGE
         → Windows 系统标志被设置，并广播 WM_SETTINGCHANGE 给所有窗口
         → 微信的 Qt 事件循环收到消息后重新检查标志并加载完整 UIA Provider
      2. 持有 CUIAutomation COM 对象（通过 uiautomation 库的内部对象）
         → Windows UIA 子系统为本进程建立 Named Pipe
         → 目标应用（微信）检测到 UIA Client 连接，确认无障碍客户端存在
      3. 保活线程每 30 秒检查 SPI 标志是否被其他程序清除，必要时重新设置
    """
    _lock = threading.Lock()
    _active = False
    _uia_root = None
    _keep_alive_thread = None
    _stop_event = threading.Event()

    @classmethod
    def activate(cls):
        with cls._lock:
            if cls._active:
                logger.info('[SilentNarrator] 已处于激活状态，跳过')
                return
            try:
                cls._set_screen_reader_flag(True)
                cls._init_uia_client()
                cls._stop_event.clear()
                cls._keep_alive_thread = threading.Thread(
                    target=cls._keep_alive_loop, 
                    name='SilentNarrator-KeepAlive', 
                    daemon=True
                )
                cls._keep_alive_thread.start()
                cls._active = True
                logger.info('[SilentNarrator] ✅ 激活成功，已模拟屏幕阅读器存在')
            except Exception as e:
                logger.error(f'[SilentNarrator] ❌ 激活失败: {e}', exc_info=True)

    @classmethod
    def deactivate(cls):
        with cls._lock:
            if not cls._active:
                return
            cls._stop_event.set()
            cls._active = False
            cls._uia_root = None
            cls._set_screen_reader_flag(False)
            logger.info('[SilentNarrator] 已停用')

    @classmethod
    def is_active(cls):
        return cls._active

    @classmethod
    def check_wechat_accessibility(cls):
        try:
            import uiautomation as auto
            wechat_win = auto.WindowControl(searchDepth=1, ClassName='WeChatMainWndForPC', timeout=3)
            if not wechat_win.Exists(maxSearchSeconds=2):
                return False
            children = wechat_win.GetChildren()
            return len(children) > 1
        except Exception:
            return False

    @classmethod
    def _set_screen_reader_flag(cls, enabled):
        try:
            user32 = ctypes.WinDLL('user32', use_last_error=True)
            ok = user32.SystemParametersInfoW(SPI_SETSCREENREADER, 1 if enabled else 0, None, SPIF_SENDCHANGE)
            if not ok:
                err = ctypes.get_last_error()
                logger.warning(f'[SilentNarrator] SystemParametersInfoW 返回失败，错误码: {err}')
        except Exception as e:
            logger.warning(f'[SilentNarrator] 设置 SPI 标志失败: {e}')

    @classmethod
    def _init_uia_client(cls):
        try:
            import uiautomation as auto
            cls._uia_root = auto.GetRootControl()
            logger.debug('[SilentNarrator] UIA 客户端已通过 uiautomation 库初始化')
        except Exception as e:
            logger.debug(f'[SilentNarrator] uiautomation 初始化失败，尝试 comtypes: {e}')
            try:
                import comtypes.client as comtypes
                cls._uia_root = comtypes.client.CreateObject('{FF48DBA4-60EF-4201-AA87-54103EEF594E}')
                logger.debug('[SilentNarrator] UIA 客户端已通过 comtypes 初始化')
            except Exception as e2:
                logger.warning(f'[SilentNarrator] comtypes UIA 初始化也失败: {e2}\n仅依赖 SPI 标志运行，效果可能受限')

    @classmethod
    def _keep_alive_loop(cls):
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        while not cls._stop_event.wait(timeout=30):
            if not cls._active:
                break
            try:
                flag = ctypes.c_bool(False)
                user32.SystemParametersInfoW(SPI_GETSCREENREADER, 0, ctypes.byref(flag), 0)
                if not flag.value:
                    logger.info('[SilentNarrator] SPI 标志被外部清除，正在重新设置...')
                    cls._set_screen_reader_flag(True)
            except Exception as e:
                logger.debug(f'[SilentNarrator] 保活检查异常: {e}')

atexit.register(SilentNarrator.deactivate)
