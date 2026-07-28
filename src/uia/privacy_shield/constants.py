import ctypes
import ctypes.wintypes

# Windows 常量
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080  # 不在任务栏显示
WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
GWL_EXSTYLE = -20
LWA_COLORKEY = 0x01
LWA_ALPHA = 0x02
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2  # 取消置顶
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_SHOWWINDOW = 0x0040
SWP_NOACTIVATE = 0x0010
WM_PAINT = 0x000F
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_TIMER = 0x0113

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

# 64位 Windows 上 LRESULT 是 c_longlong（8字节），c_long 只有4字节会溢出
LRESULT = ctypes.c_longlong

# WNDPROC 回调函数类型（返回 LRESULT）
WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT,
    ctypes.wintypes.HWND,
    ctypes.c_uint,
    ctypes.wintypes.WPARAM,
    ctypes.wintypes.LPARAM,
)

# ===== kernel32 API 函数声明 =====
kernel32.GetModuleHandleW.argtypes = [ctypes.wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = ctypes.wintypes.HINSTANCE
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = ctypes.wintypes.DWORD

# ===== user32 API 函数声明 =====
user32.DefWindowProcW.argtypes = [
    ctypes.wintypes.HWND, ctypes.c_uint,
    ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM,
]
user32.DefWindowProcW.restype = LRESULT

user32.LoadCursorW.argtypes = [ctypes.wintypes.HINSTANCE, ctypes.wintypes.LPCWSTR]
user32.LoadCursorW.restype = ctypes.wintypes.HANDLE

user32.RegisterClassExW.argtypes = [ctypes.c_void_p]
user32.RegisterClassExW.restype = ctypes.wintypes.ATOM

user32.UnregisterClassW.argtypes = [ctypes.wintypes.LPCWSTR, ctypes.wintypes.HINSTANCE]
user32.UnregisterClassW.restype = ctypes.wintypes.BOOL

user32.CreateWindowExW.argtypes = [
    ctypes.wintypes.DWORD,
    ctypes.wintypes.LPCWSTR,
    ctypes.wintypes.LPCWSTR,
    ctypes.wintypes.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.wintypes.HWND,
    ctypes.wintypes.HMENU,
    ctypes.wintypes.HINSTANCE,
    ctypes.wintypes.LPVOID,
]
user32.CreateWindowExW.restype = ctypes.wintypes.HWND

user32.DestroyWindow.argtypes = [ctypes.wintypes.HWND]
user32.DestroyWindow.restype = ctypes.wintypes.BOOL

user32.ShowWindow.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = ctypes.wintypes.BOOL

user32.SetWindowPos.argtypes = [
    ctypes.wintypes.HWND,
    ctypes.wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_uint,
]
user32.SetWindowPos.restype = ctypes.wintypes.BOOL

user32.IsIconic.argtypes = [ctypes.wintypes.HWND]
user32.IsIconic.restype = ctypes.wintypes.BOOL

user32.SetLayeredWindowAttributes.argtypes = [
    ctypes.wintypes.HWND,
    ctypes.wintypes.COLORREF,
    ctypes.c_byte,
    ctypes.wintypes.DWORD,
]
user32.SetLayeredWindowAttributes.restype = ctypes.wintypes.BOOL

user32.InvalidateRect.argtypes = [ctypes.wintypes.HWND, ctypes.c_void_p, ctypes.wintypes.BOOL]
user32.InvalidateRect.restype = ctypes.wintypes.BOOL

user32.UpdateWindow.argtypes = [ctypes.wintypes.HWND]
user32.UpdateWindow.restype = ctypes.wintypes.BOOL

user32.SetTimer.argtypes = [ctypes.wintypes.HWND, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p]
user32.SetTimer.restype = ctypes.c_void_p

user32.KillTimer.argtypes = [ctypes.wintypes.HWND, ctypes.c_void_p]
user32.KillTimer.restype = ctypes.wintypes.BOOL

user32.PeekMessageW.argtypes = [ctypes.c_void_p, ctypes.wintypes.HWND, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
user32.PeekMessageW.restype = ctypes.wintypes.BOOL

user32.TranslateMessage.argtypes = [ctypes.c_void_p]
user32.TranslateMessage.restype = ctypes.wintypes.BOOL

user32.DispatchMessageW.argtypes = [ctypes.c_void_p]
user32.DispatchMessageW.restype = LRESULT

user32.BeginPaint.argtypes = [ctypes.wintypes.HWND, ctypes.c_void_p]
user32.BeginPaint.restype = ctypes.wintypes.HDC

user32.EndPaint.argtypes = [ctypes.wintypes.HWND, ctypes.c_void_p]
user32.EndPaint.restype = ctypes.wintypes.BOOL

user32.GetClientRect.argtypes = [ctypes.wintypes.HWND, ctypes.c_void_p]
user32.GetClientRect.restype = ctypes.wintypes.BOOL

user32.FillRect.argtypes = [ctypes.wintypes.HDC, ctypes.c_void_p, ctypes.wintypes.HBRUSH]
user32.FillRect.restype = ctypes.c_int

user32.DrawTextW.argtypes = [
    ctypes.wintypes.HDC,
    ctypes.wintypes.LPCWSTR,
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.wintypes.UINT,
]
user32.DrawTextW.restype = ctypes.c_int

user32.PostMessageW.argtypes = [
    ctypes.wintypes.HWND,
    ctypes.c_uint,
    ctypes.wintypes.WPARAM,
    ctypes.wintypes.LPARAM,
]
user32.PostMessageW.restype = ctypes.wintypes.BOOL

try:
    user32.SetWindowLongPtrW.argtypes = [ctypes.wintypes.HWND, ctypes.c_int, ctypes.c_longlong]
    user32.SetWindowLongPtrW.restype = ctypes.c_longlong
except AttributeError:
    pass

user32.SetWindowLongW.argtypes = [ctypes.wintypes.HWND, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long

user32.GetWindowLongW.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long

# ===== gdi32 API 函数声明 =====
gdi32.CreateSolidBrush.argtypes = [ctypes.wintypes.COLORREF]
gdi32.CreateSolidBrush.restype = ctypes.wintypes.HBRUSH

gdi32.SetBkMode.argtypes = [ctypes.wintypes.HDC, ctypes.c_int]
gdi32.SetBkMode.restype = ctypes.c_int

gdi32.SetTextColor.argtypes = [ctypes.wintypes.HDC, ctypes.wintypes.COLORREF]
gdi32.SetTextColor.restype = ctypes.wintypes.COLORREF

gdi32.CreateFontW.argtypes = [
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.wintypes.DWORD, ctypes.wintypes.DWORD,
    ctypes.wintypes.DWORD, ctypes.wintypes.DWORD, ctypes.wintypes.DWORD,
    ctypes.wintypes.DWORD, ctypes.wintypes.DWORD, ctypes.wintypes.DWORD,
    ctypes.wintypes.LPCWSTR,
]
gdi32.CreateFontW.restype = ctypes.wintypes.HFONT

gdi32.SelectObject.argtypes = [ctypes.wintypes.HDC, ctypes.wintypes.HGDIOBJ]
gdi32.SelectObject.restype = ctypes.wintypes.HGDIOBJ

gdi32.DeleteObject.argtypes = [ctypes.wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = ctypes.wintypes.BOOL

# ctypes.wintypes 不内置 WNDCLASSEXW，需要手动定义
class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("style", ctypes.c_uint),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.wintypes.HINSTANCE),
        ("hIcon", ctypes.wintypes.HICON),
        ("hCursor", ctypes.wintypes.HICON),
        ("hbrBackground", ctypes.wintypes.HBRUSH),
        ("lpszMenuName", ctypes.wintypes.LPCWSTR),
        ("lpszClassName", ctypes.wintypes.LPCWSTR),
        ("hIconSm", ctypes.wintypes.HICON),
    ]
