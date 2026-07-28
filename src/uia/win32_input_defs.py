import ctypes
import ctypes.wintypes

# ==================== Win32 常量与类型定义 ====================
WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
VK_ESCAPE = 0x1B
HC_ACTION = 0

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# 显式定义类型以防止 64 位溢出
# LRESULT 在 64 位上是 int64，在 32 位上是 long
LRESULT = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long

# 低级钩子回调类型定义
HOOKPROC = ctypes.WINFUNCTYPE(
    LRESULT,                 # 返回值
    ctypes.c_int,            # nCode
    ctypes.wintypes.WPARAM,  # wParam
    ctypes.wintypes.LPARAM,  # lParam
)

# 显式声明 API 参数类型与返回值
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.wintypes.DWORD]
user32.SetWindowsHookExW.restype = ctypes.wintypes.HHOOK

user32.CallNextHookEx.argtypes = [ctypes.wintypes.HHOOK, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT

user32.UnhookWindowsHookEx.argtypes = [ctypes.wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = ctypes.wintypes.BOOL

user32.PostThreadMessageW.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.UINT, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
user32.PostThreadMessageW.restype = ctypes.wintypes.BOOL
