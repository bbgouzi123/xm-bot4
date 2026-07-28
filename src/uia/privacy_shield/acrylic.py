import ctypes
import ctypes.wintypes
import logging
from .constants import user32

logger = logging.getLogger(__name__)

# SetWindowCompositionAttribute 的结构体（未公开 API，Windows 10 1803+ 支持）
class ACCENT_POLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_uint),  # AABBGGRR 格式
        ("AnimationId", ctypes.c_int),
    ]

class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
    _fields_ = [
        ("Attrib", ctypes.c_int),
        ("pvData", ctypes.c_void_p),
        ("cbData", ctypes.c_size_t),
    ]

# Acrylic 常量
ACCENT_DISABLED = 0
ACCENT_ENABLE_BLURBEHIND = 3        # 普通模糊（Win10 早期）
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4  # 亚克力毛玻璃（Win10 1803+）
WCA_ACCENT_POLICY = 19


def _apply_acrylic_blur(hwnd, color_argb=0xCC1A1A1A):
    """
    对指定窗口应用 Windows 10+ Acrylic 毛玻璃效果

    参数:
        hwnd: 窗口句柄
        color_argb: AABBGGRR 格式的叠加色
            - AA = Alpha 透明度 (0x00=全透明, 0xFF=不透明)
            - BB/GG/RR = 叠加色的 BGR 分量
            - 默认 0xCC1A1A1A = 80% 不透明的深灰色毛玻璃
    """
    try:
        # 动态获取函数（未公开 API）
        SetWindowCompositionAttribute = user32.SetWindowCompositionAttribute
        SetWindowCompositionAttribute.argtypes = [
            ctypes.wintypes.HWND,
            ctypes.POINTER(WINDOWCOMPOSITIONATTRIBDATA),
        ]
        SetWindowCompositionAttribute.restype = ctypes.wintypes.BOOL

        accent = ACCENT_POLICY()
        accent.AccentState = ACCENT_ENABLE_ACRYLICBLURBEHIND
        accent.AccentFlags = 2  # 绘制所有边框
        accent.GradientColor = color_argb

        data = WINDOWCOMPOSITIONATTRIBDATA()
        data.Attrib = WCA_ACCENT_POLICY
        data.pvData = ctypes.cast(ctypes.byref(accent), ctypes.c_void_p)
        data.cbData = ctypes.sizeof(accent)

        result = SetWindowCompositionAttribute(hwnd, ctypes.byref(data))
        if result:
            logger.info("[隐私遮罩] 毛玻璃效果已启用 (Acrylic)")
            return True
        else:
            # 降级尝试普通模糊
            accent.AccentState = ACCENT_ENABLE_BLURBEHIND
            accent.GradientColor = color_argb
            data.pvData = ctypes.cast(ctypes.byref(accent), ctypes.c_void_p)
            result = SetWindowCompositionAttribute(hwnd, ctypes.byref(data))
            if result:
                logger.info("[隐私遮罩] 毛玻璃效果已启用 (BlurBehind 降级)")
                return True

    except Exception as e:
        logger.debug(f"[隐私遮罩] 毛玻璃 API 不可用: {e}")

    return False
