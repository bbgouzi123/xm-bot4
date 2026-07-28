import ctypes
import ctypes.wintypes
import os
import logging
import threading
from .constants import user32, gdi32
from .base import PrivacyShieldBase

logger = logging.getLogger(__name__)

# ==================== 进程级 GDI+ 单例管理 ====================
# 避免每次 WM_PAINT 都执行 GdiplusStartup/Shutdown（极其昂贵且异常路径下泄漏令牌）
_gdiplus_token = None
_gdiplus_lock = threading.Lock()
_gdiplus_module = None


def _ensure_gdiplus():
    """确保 GDI+ 已全局初始化（进程级单次），返回 (gdiplus_module, token)"""
    global _gdiplus_token, _gdiplus_module
    if _gdiplus_token is not None:
        return _gdiplus_module, _gdiplus_token
    with _gdiplus_lock:
        if _gdiplus_token is not None:
            return _gdiplus_module, _gdiplus_token
        from ctypes import windll, byref, c_size_t
        gdiplus = windll.gdiplus
        token = c_size_t(0)
        si = GdiplusStartupInput()
        si.GdiplusVersion = 1
        status = gdiplus.GdiplusStartup(byref(token), byref(si), None)
        if status == 0:
            _gdiplus_token = token
            _gdiplus_module = gdiplus
            
            # 声明 GDI+ API 函数参数与返回值，确保 64 位下指针不被截断与错误强转
            gdiplus.GdipCreateFromHDC.argtypes = [ctypes.wintypes.HDC, ctypes.c_void_p]
            gdiplus.GdipCreateFromHDC.restype = ctypes.c_int

            gdiplus.GdipCreateSolidFill.argtypes = [ctypes.c_uint, ctypes.c_void_p]
            gdiplus.GdipCreateSolidFill.restype = ctypes.c_int

            gdiplus.GdipFillRectangleI.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
            gdiplus.GdipFillRectangleI.restype = ctypes.c_int

            gdiplus.GdipDeleteBrush.argtypes = [ctypes.c_void_p]
            gdiplus.GdipDeleteBrush.restype = ctypes.c_int

            gdiplus.GdipDeleteGraphics.argtypes = [ctypes.c_void_p]
            gdiplus.GdipDeleteGraphics.restype = ctypes.c_int

            gdiplus.GdipCreateBitmapFromFile.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p]
            gdiplus.GdipCreateBitmapFromFile.restype = ctypes.c_int

            gdiplus.GdipSetInterpolationMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
            gdiplus.GdipSetInterpolationMode.restype = ctypes.c_int

            gdiplus.GdipSetSmoothingMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
            gdiplus.GdipSetSmoothingMode.restype = ctypes.c_int

            gdiplus.GdipCreatePath.argtypes = [ctypes.c_int, ctypes.c_void_p]
            gdiplus.GdipCreatePath.restype = ctypes.c_int

            gdiplus.GdipAddPathEllipseI.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
            gdiplus.GdipAddPathEllipseI.restype = ctypes.c_int

            gdiplus.GdipSetClipPath.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
            gdiplus.GdipSetClipPath.restype = ctypes.c_int

            gdiplus.GdipDrawImageRectI.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
            gdiplus.GdipDrawImageRectI.restype = ctypes.c_int

            gdiplus.GdipResetClip.argtypes = [ctypes.c_void_p]
            gdiplus.GdipResetClip.restype = ctypes.c_int

            gdiplus.GdipDeletePath.argtypes = [ctypes.c_void_p]
            gdiplus.GdipDeletePath.restype = ctypes.c_int

            gdiplus.GdipDisposeImage.argtypes = [ctypes.c_void_p]
            gdiplus.GdipDisposeImage.restype = ctypes.c_int

            logger.info("[隐私遮罩] GDI+ 进程级初始化成功")
        else:
            logger.error(f"[隐私遮罩] GDI+ 初始化失败: status={status}")
        return _gdiplus_module, _gdiplus_token

class GdiplusStartupInput(ctypes.Structure):
    _fields_ = [
        ("GdiplusVersion", ctypes.c_uint),
        ("DebugEventCallback", ctypes.c_void_p),
        ("SuppressBackgroundThread", ctypes.wintypes.BOOL),
        ("SuppressExternalCodecs", ctypes.wintypes.BOOL),
    ]

class RenderingMixin(PrivacyShieldBase):
    """绘制相关逻辑"""
    
    def _get_logo_path(self) -> str:
        """获取 LOGO 文件路径"""
        import sys
        # 1. PyInstaller 打包环境
        if getattr(sys, 'frozen', False):
            meipass = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
            candidates = [
                os.path.join(os.path.dirname(sys.executable), "assets", "logo.png"),
                os.path.join(os.path.dirname(sys.executable), "_internal", "assets", "logo.png"),
                os.path.join(meipass, "assets", "logo.png"),
                os.path.join(meipass, "logo.png"),
            ]
            for p in candidates:
                if os.path.exists(p):
                    return p

        # 2. 开发环境
        here = os.path.dirname(os.path.abspath(__file__))
        # src/uia/privacy_shield/rendering.py -> src/uia/privacy_shield -> src/uia -> src -> backend-python
        backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(here))))
        candidates = [
            os.path.join(backend_dir, "assets", "logo.png"),
            os.path.join(os.getcwd(), "assets", "logo.png"),
            os.path.join(os.getcwd(), "_internal", "assets", "logo.png"),
        ]
        for p in candidates:
            if os.path.exists(p):
                return p
        return ""

    def _on_paint(self, hwnd):
        """绘制遮罩内容：居中圆头像和昵称，下方低调隐私信息，底部品牌信息凸显"""

        class PAINTSTRUCT(ctypes.Structure):
            _fields_ = [
                ("hdc", ctypes.wintypes.HDC),
                ("fErase", ctypes.wintypes.BOOL),
                ("rcPaint", ctypes.wintypes.RECT),
                ("fRestore", ctypes.wintypes.BOOL),
                ("fIncUpdate", ctypes.wintypes.BOOL),
                ("rgbReserved", ctypes.c_byte * 32),
            ]

        ps = PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))

        if hdc:
            # 获取窗口尺寸
            rect = ctypes.wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rect))
            w = rect.right - rect.left
            h = rect.bottom - rect.top

            # ===== 使用 GDI+ 绘制深色不透明底色（绝对隐私安全网） =====
            # 使用进程级 GDI+ 单例，避免每次 WM_PAINT 都 Startup/Shutdown（防资源泄漏）
            try:
                from ctypes import byref, c_void_p
                gdiplus, _token = _ensure_gdiplus()
                if gdiplus and _token:
                    graphics = c_void_p()
                    brush_gp = c_void_p()
                    try:
                        gdiplus.GdipCreateFromHDC(hdc, byref(graphics))
                        # 使用 100% 不透明的深色 (ARGB: 0xFF1E293B, Slate 800)
                        gdiplus.GdipCreateSolidFill(ctypes.c_uint(0xFF1E293B), byref(brush_gp))
                        gdiplus.GdipFillRectangleI(graphics, brush_gp, 0, 0, w, h)
                    finally:
                        # 确保 GDI+ 对象一定被释放，防止句柄泄漏
                        if brush_gp:
                            gdiplus.GdipDeleteBrush(brush_gp)
                        if graphics:
                            gdiplus.GdipDeleteGraphics(graphics)
                else:
                    raise RuntimeError("GDI+ 未初始化")
            except Exception as e:
                # 极端降级：普通 GDI 填充
                brush = gdi32.CreateSolidBrush(0x002A170F)
                user32.FillRect(hdc, ctypes.byref(rect), brush)
                gdi32.DeleteObject(brush)

            gdi32.SetBkMode(hdc, 1)  # TRANSPARENT
            cy = h // 2

            # =========================================================
            # 1. 核心视觉区 (居中)：头像 -> 昵称 -> 状态 -> 次要提示
            # =========================================================
            
            avatar_to_draw = self._wechat_avatar_path or self._fallback_avatar_path
            nickname_to_draw = self._wechat_nickname or self._fallback_nickname
            
            # 增加默认兜底：若头像不存在或路径无效，使用系统内置 Logo 作为兜底头像
            if not avatar_to_draw or not os.path.exists(avatar_to_draw):
                avatar_to_draw = self._get_logo_path()
            
            # 录屏保护模式：隐藏真实身份信息
            if self._record_mode:
                avatar_to_draw = self._get_logo_path()          # 调用已有 Logo 作为隐私保护新头像
                nickname_to_draw = "用户***"
            
            # 计算居中起点
            center_h = 0
            if nickname_to_draw or avatar_to_draw:
                center_h += 112 + 20 + 36 + 25  # 头像 + 间距 + 昵称 + 间距
            center_h += 24 + 15 + 24  # 状态 + 间距 + 次要提示
            
            content_y = cy - (center_h // 2)

            # --- 1. 用户头像 ---
            if nickname_to_draw or avatar_to_draw:
                avatar_size = 112
                if avatar_to_draw and os.path.exists(avatar_to_draw):
                    try:
                        ok = self._draw_logo(hdc, avatar_to_draw, w, content_y, avatar_size, is_circle=True)
                        if not hasattr(self, '_avatar_dbg'):
                            self._avatar_dbg = True
                            logger.info(f"[隐私遮罩] 头像绘制结果: {ok}, path={avatar_to_draw}")
                    except Exception as e:
                        if not hasattr(self, '_avatar_dbg'):
                            self._avatar_dbg = True
                            logger.error(f"[隐私遮罩] 头像绘制异常: {e}")
                else:
                    if not hasattr(self, '_avatar_dbg'):
                        self._avatar_dbg = True
                        if not self._record_mode:
                            logger.warning(f"[隐私遮罩] 头像路径无效: path='{avatar_to_draw}', exists={os.path.exists(avatar_to_draw) if avatar_to_draw else 'N/A'}")
                
                content_y += avatar_size + 20
                
                # --- 2. 用户昵称 (保持) ---
                font_user = gdi32.CreateFontW(
                    32, 0, 0, 0, 600, 0, 0, 0, 1, 0, 0, 4, 0, "Microsoft YaHei UI"
                )
                old_font = gdi32.SelectObject(hdc, font_user)
                gdi32.SetTextColor(hdc, 0x00F0E8E2)  # BGR for #E2E8F0 白亮色
                
                user_text = f"{nickname_to_draw}"
                user_rect = ctypes.wintypes.RECT(0, content_y, w, content_y + 40)
                user32.DrawTextW(hdc, user_text, len(user_text), ctypes.byref(user_rect), 0x25)
                gdi32.SelectObject(hdc, old_font)
                gdi32.DeleteObject(font_user)

                content_y += 36 + 25

            # --- 3. 核心状态 (调大字号，颜色调淡) ---
            font_status = gdi32.CreateFontW(
                26, 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 4, 0, "Microsoft YaHei UI"
            )
            old_font = gdi32.SelectObject(hdc, font_status)
            gdi32.SetTextColor(hdc, 0x0071665C)  # 更淡一点的灰颜色，融入深色背景
            status = "隐 私 保 护 中"
            status_rect = ctypes.wintypes.RECT(0, content_y, w, content_y + 36)
            user32.DrawTextW(hdc, status, len(status), ctypes.byref(status_rect), 0x25)
            gdi32.SelectObject(hdc, old_font)
            gdi32.DeleteObject(font_status)

            content_y += 26 + 15

            # --- 4. 次要提示 (调大字号，颜色调淡) ---
            font_tip = gdi32.CreateFontW(
                22, 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 4, 0, "Microsoft YaHei UI"
            )
            old_font = gdi32.SelectObject(hdc, font_tip)
            gdi32.SetTextColor(hdc, 0x00695547)  # 非常低调的暗灰色
            tip = "正在执行自动化安全操作，屏幕已锁定防泄露"
            tip_rect = ctypes.wintypes.RECT(0, content_y, w, content_y + 32)
            user32.DrawTextW(hdc, tip, len(tip), ctypes.byref(tip_rect), 0x25)
            gdi32.SelectObject(hdc, old_font)
            gdi32.DeleteObject(font_tip)


            # =========================================================
            # 2. 底部区：xm-bot4信息 (调大更清晰)
            # =========================================================
            bottom_y = h - 200

            # --- 5. LOGO ---
            logo_size_bottom = 64
            logo_path = self._get_logo_path()
            if logo_path:
                try:
                    self._draw_logo(hdc, logo_path, w, bottom_y, logo_size_bottom, is_circle=False)
                except Exception:
                    pass

            bottom_y += logo_size_bottom + 15

            # --- 6. 品牌名 ---
            font_brand = gdi32.CreateFontW(
                30, 0, 0, 0, 700, 0, 0, 0, 1, 0, 0, 4, 0, "Microsoft YaHei UI"
            )
            old_font = gdi32.SelectObject(hdc, font_brand)
            gdi32.SetTextColor(hdc, 0x0081B910)  # BGR for #10B981 翡翠绿
            brand_name = "xm-bot4"
            brand_rect = ctypes.wintypes.RECT(0, bottom_y, w, bottom_y + 40)
            user32.DrawTextW(hdc, brand_name, len(brand_name), ctypes.byref(brand_rect), 0x25)
            gdi32.SelectObject(hdc, old_font)
            gdi32.DeleteObject(font_brand)
            
            bottom_y += 40 + 10

            # --- 7. Slogan (调大) ---
            font_slogan = gdi32.CreateFontW(
                22, 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 4, 0, "Microsoft YaHei UI"
            )
            old_font = gdi32.SelectObject(hdc, font_slogan)
            gdi32.SetTextColor(hdc, 0x00B8A394)  # BGR for #94A3B8 浅灰银
            slogan = "商业级私域资产管理 · 为客户隐私保驾护航"
            slogan_rect = ctypes.wintypes.RECT(0, bottom_y, w, bottom_y + 32)
            user32.DrawTextW(hdc, slogan, len(slogan), ctypes.byref(slogan_rect), 0x25)
            gdi32.SelectObject(hdc, old_font)
            gdi32.DeleteObject(font_slogan)

        user32.EndPaint(hwnd, ctypes.byref(ps))

    def _draw_logo(self, hdc, logo_path: str, canvas_w: int, logo_y: int, size: int, is_circle: bool = False, border_radius: int = 0) -> bool:
        """使用 GDI+ 在 HDC 上绘制 PNG LOGO（居中缩放，支持圆形或圆角裁剪）
        
        使用进程级 GDI+ 单例令牌，避免每次调用都 Startup/Shutdown 导致资源泄漏。
        所有 GDI+ 对象通过 try/finally 确保释放。
        """
        image = None
        graphics = None
        path = None
        try:
            from ctypes import c_void_p, byref, c_int, c_uint, c_size_t, c_float

            # 复用进程级 GDI+ 令牌
            gdiplus, _token = _ensure_gdiplus()
            if not gdiplus or not _token:
                return False

            # 加载图片
            image = c_void_p()
            status = gdiplus.GdipCreateBitmapFromFile(ctypes.c_wchar_p(logo_path), byref(image))
            if status != 0 or not image:
                print(f"[隐私遮罩] GDI+ 加载图片失败: status={status}, image={image.value}, path={logo_path}", flush=True)
                image = None  # 标记为无需清理
                return False

            # 创建 GDI+ Graphics 对象
            graphics = c_void_p()
            gdiplus.GdipCreateFromHDC(hdc, byref(graphics))

            # 设置高质量缩放
            gdiplus.GdipSetInterpolationMode(graphics, 7)  # HighQualityBicubic
            gdiplus.GdipSetSmoothingMode(graphics, 4)  # AntiAlias

            # 居中绘制
            logo_x = (canvas_w - size) // 2
            
            # 圆形裁剪支持
            if is_circle:
                path = c_void_p()
                gdiplus.GdipCreatePath(0, byref(path))
                gdiplus.GdipAddPathEllipseI(path, logo_x, logo_y, size, size)
                gdiplus.GdipSetClipPath(graphics, path, 0)

            gdiplus.GdipDrawImageRectI(graphics, image, logo_x, logo_y, size, size)
            
            if is_circle and path:
                gdiplus.GdipResetClip(graphics)

            return True

        except Exception as e:
            print(f"[隐私遮罩] GDI+ LOGO 绘制异常: {e}", flush=True)
            return False
        finally:
            # 确保所有 GDI+ 对象一定被释放，防止句柄泄漏导致进程被系统终止
            try:
                gdiplus, _ = _ensure_gdiplus()
                if gdiplus:
                    if path:
                        gdiplus.GdipDeletePath(path)
                    if graphics:
                        gdiplus.GdipDeleteGraphics(graphics)
                    if image:
                        gdiplus.GdipDisposeImage(image)
            except Exception:
                pass
