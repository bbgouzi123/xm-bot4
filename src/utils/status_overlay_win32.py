import logging

logger = logging.getLogger(__name__)

def post_close_message(hwnd):
    # HUD 透明子窗口的生命周期由前端管理，此处无需额外操作
    pass

def manage_overlay_timer(hwnd, start_timer: bool):
    pass

def trigger_repaint(hwnd):
    pass

def run_status_overlay_loop(overlay):
    # 🌟 使用 Tauri/Webview 悬浮看板代替原生 GDI 看板。
    # 这里我们只假装启动成功（模拟hwnd，不创建实际的Win32窗口以防抢焦点或重复绘制）。
    overlay.hwnd = 99999
    
    # 模拟保持 stop_event 挂起，防止线程退出
    while not overlay._stop_event.is_set():
        overlay._stop_event.wait(0.5)
        
    overlay.hwnd = None
    try:
        from src.utils.status_overlay import broadcast_overlay_status
        broadcast_overlay_status(False)
    except Exception:
        pass
