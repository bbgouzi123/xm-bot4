"""webview_patch.py — 针对 pywebview 和 webview2 的补丁模块"""
import os
import sys
import time
import threading

def apply_webview_patches():
    """应用 pywebview 各类底层缺陷猴子补丁"""
    try:
        import webview
        # 🌟 猴子补丁防御：修复 pywebview 在 WinForms 下 x 或 y 为 None 或 float 导致 SetWindowPos 崩溃的 ctypes 缺陷
        _orig_move = webview.Window.move
        def _patched_move(self, x, y):
            if x is None or y is None:
                return
            try:
                _orig_move(self, int(x), int(y))
            except Exception as move_err:
                print(f"[Patch] webview.Window.move 拦截异常: {move_err}")
        webview.Window.move = _patched_move
    except ImportError:
        pass

def setup_native_icon(js_api, icon_ico):
    """异步在窗口句柄就绪后设置高清 native 图标"""
    def set_native_icon():
        import ctypes
        for _ in range(20):
            time.sleep(0.5)
            hwnd = js_api._get_hwnd()
            if hwnd and os.path.exists(icon_ico):
                try:
                    hicon_small = ctypes.windll.user32.LoadImageW(0, icon_ico, 1, 16, 16, 0x0010)
                    hicon_big = ctypes.windll.user32.LoadImageW(0, icon_ico, 1, 256, 256, 0x0010)
                    if hicon_small:
                        ctypes.windll.user32.PostMessageW(hwnd, 0x0080, 0, hicon_small)
                    if hicon_big:
                        ctypes.windll.user32.PostMessageW(hwnd, 0x0080, 1, hicon_big)
                    break
                except Exception as e:
                    print(f"[外壳] 设置图标异常: {e}")
                    
    threading.Thread(target=set_native_icon, daemon=True).start()
