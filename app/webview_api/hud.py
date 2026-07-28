# -*- coding: utf-8 -*-
import os
import sys
import time
import threading
import traceback
import ctypes
from ctypes.wintypes import RECT
from main import _write_crash_log
from xm_py_server.shell import PywebviewShell

def open_hud_window_impl(shell, url: str):
    """打开 HUD 自动化控制中心子窗口（透明、无边框、置顶）。"""
    _write_crash_log(f"[xm-bot4-shell] open_hud_window 被调用，url: {url}")
    
    try:
        import webview
    except ImportError:
        _write_crash_log("[xm-bot4-shell] pywebview 未安装，无法创建 HUD 窗口")
        print("[xm-bot4-shell] pywebview 未安装，无法创建 HUD 窗口")
        return
    
    try:
        # 检查是否已经存在 hud 窗口，防止重复创建
        for win in webview.windows:
            try:
                win_url = getattr(win, 'url', None)
                if win.title == "自动化控制中心" or (win_url and "window=hud" in win_url):
                    shell._safe_invoke(win.restore)
                    shell._safe_invoke(win.show)
                    _write_crash_log("[xm-bot4-shell] HUD 窗口已存在，直接显示")
                    print("[xm-bot4-shell] HUD 窗口已存在，直接显示")
                    return
            except Exception as win_err:
                _write_crash_log(f"[xm-bot4-shell] 遍历窗口异常: {win_err}")
        
        # 计算屏幕右侧居中的初始位置（物理像素）
        init_x, init_y = 100, 100
        try:
            screen_w = ctypes.windll.user32.GetSystemMetrics(0)
            screen_h = ctypes.windll.user32.GetSystemMetrics(1)
            win_w, win_h = 360, 520
            init_x = max(0, screen_w - win_w - 48)
            init_y = max(0, (screen_h - win_h) // 2)
        except Exception as ctypes_err:
            _write_crash_log(f"[xm-bot4-shell] 获取屏幕指标异常: {ctypes_err}")
        
        _write_crash_log(f"[xm-bot4-shell] 初始坐标计算完成 x: {init_x}, y: {init_y}")

        class HudWebviewApi(PywebviewShell):
            """HUD 子窗口专属的极简安全 JS API 实例。"""
            def minimize(self):
                self.minimize_window()

            def get_windows_workarea(self):
                """获取 Windows 系统的真实工作区大小（扣除任务栏）"""
                try:
                    rect = RECT()
                    # SPI_GETWORKAREA = 48
                    res = ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0)
                    if res:
                        return {
                            "left": rect.left,
                            "top": rect.top,
                            "right": rect.right,
                            "bottom": rect.bottom,
                            "width": rect.right - rect.left,
                            "height": rect.bottom - rect.top
                        }
                except Exception as e:
                    _write_crash_log(f"[HUD-API] 获取工作区异常: {e}")
                return None

            def request_theme_sync(self):
                """HUD 窗口请求主窗口同步主题"""
                import webview
                main_win = None
                for win in webview.windows:
                    if win != self._window:
                        main_win = win
                        break
                if main_win:
                    try:
                        main_win.evaluate_js("window.__xm_theme_request__?.()")
                    except Exception as e:
                        pass

        hud_api = HudWebviewApi()
        
        def _create():
            """在后台线程中创建 pywebview 浮窗（pywebview 多窗口规范要求）。"""
            try:
                _write_crash_log("[xm-bot4-shell] 在后台线程中开始执行 webview.create_window...")
                hud_win = webview.create_window(
                    title="自动化控制中心",
                    url=url,
                    width=360,
                    height=520,
                    min_size=(10, 10),           # ← 破除 WinForms 默认 MinimumSize 限制，允许缩放到极小的胶囊尺寸
                    x=init_x,
                    y=init_y,
                    frameless=True,
                    transparent=False,           # ← 改为 False，彻底解决 WinForms 平台的 TransparencyKey 穿透 bug
                    on_top=True,
                    background_color='#1E293B',  # ← 设为与前端暗色主题相近的底色，防止闪烁
                    resizable=False,
                    easy_drag=False,
                    shadow=False,                # ← 禁用阴影，保持真透明无描边
                    js_api=hud_api,
                    hidden=True                  # ← 🌟 默认隐藏窗口，阻断 WebView2 初始化启动时的 3 秒大黑屏
                )
                _write_crash_log("[xm-bot4-shell] webview.create_window 成功返回，仅通过 set_window 绑定引用...")
                hud_api.set_window(hud_win)

                # 🌟 核心防穿透与圆角方案：在窗口句柄就绪后，通过 Win32 API 剪裁为完美圆角矩形，保证点击绝不穿透
                def apply_win32_rounded_corners():
                    
                    def _get_hwnd_local(window):
                        if not window:
                            return None
                        # 方案 1：gui.hwnd（旧版 pywebview）
                        gui = getattr(window, 'gui', None)
                        if gui:
                            if hasattr(gui, 'hwnd'):
                                return gui.hwnd
                            elif hasattr(gui, 'window'):
                                try:
                                    if getattr(gui.window, 'IsHandleCreated', True):
                                        return gui.window.Handle.ToInt32()
                                except Exception:
                                    pass
                        # 方案 2：window.native.Handle（新版 pywebview 5.x+）
                        native = getattr(window, 'native', None)
                        if native:
                            try:
                                if getattr(native, 'IsHandleCreated', True):
                                    return native.Handle.ToInt32()
                            except Exception:
                                try:
                                    if getattr(native, 'IsHandleCreated', True):
                                        return int(native.Handle)
                                except Exception:
                                    pass
                        # 方案 3：FindWindow 回退（通过标题查找）
                        try:
                            title = getattr(window, 'title', '')
                            if title:
                                return ctypes.windll.user32.FindWindowW(None, title)
                        except Exception:
                            pass
                        return None

                    hwnd = None
                    for _ in range(50):
                        time.sleep(0.1)
                        hwnd = _get_hwnd_local(hud_win)
                        if hwnd:
                            break
                    if hwnd:
                        _write_crash_log(f"[xm-bot4-shell] 成功获取 HUD 窗口句柄 HWND: {hwnd}，开始应用 Win32 圆角剪裁")
                        try:
                            # 使用 RECT 结构体动态获取窗口当前的物理像素宽度和高度，适配 High DPI 缩放
                            rect = RECT()
                            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
                            w = rect.right - rect.left
                            h = rect.bottom - rect.top

                            # 同步自适应 DPI 缩放，消除圆角边缘残影
                            scale = 1.0
                            try:
                                dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
                                scale = dpi / 96.0
                            except Exception:
                                pass
                            radius = int(16 * scale)
                            ellipse = radius * 2
                            rgn = ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, w, h, ellipse, ellipse)
                            ctypes.windll.user32.SetWindowRgn(hwnd, rgn, True)
                            _write_crash_log(f"[xm-bot4-shell] Win32 SetWindowRgn 裁剪圆角已应用: w={w}, h={h}, scale={scale}")
                        except Exception as rgn_err:
                            _write_crash_log(f"[xm-bot4-shell] Win32 裁剪圆角异常: {rgn_err}")
                        
                        # 🌟 挂载防抖磁吸拖拽位置修正逻辑，防止折叠胶囊被拖入 Windows 任务栏或屏幕外
                        try:
                            revised_timer = [None]
                            
                            def perform_limit_check():
                                try:
                                    rect = RECT()
                                    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
                                    win_w = rect.right - rect.left
                                    win_h = rect.bottom - rect.top
                                    
                                    work_rect = RECT()
                                    ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(work_rect), 0)
                                    
                                    # 仅在窗口处于折叠胶囊状态（小窗口）时应用防抖弹回
                                    is_collapsed = win_w < 120 or win_h < 120
                                    if is_collapsed:
                                        new_left = rect.left
                                        new_top = rect.top
                                        revised = False
                                        
                                        # 1. 限制底部越界（防止掉入 Windows 任务栏）
                                        if rect.bottom > work_rect.bottom - 12:
                                            new_top = work_rect.bottom - win_h - 12
                                            revised = True
                                            
                                        # 2. 限制顶部越界
                                        if rect.top < work_rect.top + 12:
                                            new_top = work_rect.top + 12
                                            revised = True
                                            
                                        # 3. 限制左侧越界
                                        if rect.left < work_rect.left + 12:
                                            new_left = work_rect.left + 12
                                            revised = True
                                            
                                        # 4. 限制右侧越界
                                        if rect.right > work_rect.right - 12:
                                            new_left = work_rect.right - win_w - 12
                                            revised = True
                                            
                                        if revised:
                                            # SWP_NOSIZE=1, SWP_NOZORDER=4, SWP_NOACTIVATE=16
                                            ctypes.windll.user32.SetWindowPos(hwnd, 0, new_left, new_top, 0, 0, 1 | 4 | 16)
                                            _write_crash_log(f"[HUD-Limit] 触发拖拽安全吸附弹回: x={new_left}, y={new_top}")
                                except Exception as e_limit:
                                    _write_crash_log(f"[HUD-Limit] 拖拽吸附限制执行异常: {e_limit}")

                            def on_moved():
                                if revised_timer[0] is not None:
                                    try: revised_timer[0].cancel()
                                    except: pass
                                t = threading.Timer(0.1, perform_limit_check)
                                revised_timer[0] = t
                                t.start()

                            hud_win.events.moved += on_moved
                            _write_crash_log("[xm-bot4-shell] HUD 窗口移动事件监听挂载成功")
                        except Exception as e_moved:
                            _write_crash_log(f"[xm-bot4-shell] 挂载拖拽吸附监听异常: {e_moved}")
                    else:
                        _write_crash_log("[xm-bot4-shell] 未能获取到 HUD 窗口句柄 HWND，跳过裁剪")

                threading.Thread(target=apply_win32_rounded_corners, daemon=True).start()

                # 🌟 核心优雅加载流：当网页完全载入并且渲染就绪后再亮出窗口
                has_shown = [False]
                def show_window_smoothly():
                    if not has_shown[0]:
                        has_shown[0] = True
                        _write_crash_log("[xm-bot4-shell] HUD 网页完全加载完毕，正式激活并显现窗口")
                        hud_win.show()

                hud_win.events.loaded += show_window_smoothly

                # 🌟 3.5秒兜底显示保护，确保 HUD 绝对不会因为极端网页渲染事件丢失而彻底“失踪”
                def force_show_fallback():
                    time.sleep(3.5)
                    if not has_shown[0]:
                        _write_crash_log("[xm-bot4-shell] 触发 3.5s 兜底流程，强制显示 HUD 窗口")
                        try:
                            show_window_smoothly()
                        except Exception as e:
                            _write_crash_log(f"[xm-bot4-shell] 兜底显示抛出异常: {e}")

                threading.Thread(target=force_show_fallback, daemon=True).start()

                def on_close():
                    _write_crash_log("[xm-bot4-shell] HUD 窗口已关闭")
                    print("[xm-bot4-shell] HUD 窗口已关闭")

                hud_win.events.closed += on_close
                _write_crash_log(f"[xm-bot4-shell] 已成功创建 HUD 原生子窗口并就绪: {url}")
                print(f"[xm-bot4-shell] 已成功创建 HUD 原生子窗口: {url}")
            except Exception as e:
                _write_crash_log(f"[xm-bot4-shell] _create 后台线程创建 HUD 窗口失败: {e}\n{traceback.format_exc()}")
                print(f"[xm-bot4-shell] 创建 HUD 窗口失败: {e}")

        threading.Thread(target=_create, daemon=True, name="HudWindowThread").start()
        _write_crash_log("[xm-bot4-shell] HudWindowThread 后台线程已启动")
    except Exception as e:
        _write_crash_log(f"[xm-bot4-shell] open_hud_window 核心逻辑抛出异常: {e}\n{traceback.format_exc()}")
