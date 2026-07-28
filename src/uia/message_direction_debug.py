import time
import threading
import logging
from typing import Optional

logger = logging.getLogger(__name__)

def draw_debug_cross(hdc, x: int, y: int, scale: float):
    """在屏幕上绘制闪烁的红色十字星标，提供指示采样点（采用分层窗体对齐点击波纹以适应多分屏与高DPI）"""
    import win32gui
    import win32con

    def _draw_thread():
        try:
            # 1. 注册专属窗口类
            wc = win32gui.WNDCLASS()
            wc.lpfnWndProc = win32gui.DefWindowProc
            wc.lpszClassName = "WeChatDebugCrossOverlay"
            wc.hInstance = win32gui.GetModuleHandle(None)
            try:
                win32gui.RegisterClass(wc)
            except Exception:
                pass

            # 2. 创建窗口。大小设为 30x30 保证十字星不被裁切
            width, height = 30, 30
            left = x - width // 2
            top = y - height // 2
            
            style = win32con.WS_POPUP
            ex_style = (win32con.WS_EX_LAYERED | 
                        win32con.WS_EX_TRANSPARENT | 
                        win32con.WS_EX_TOPMOST | 
                        win32con.WS_EX_NOACTIVATE)
            
            hwnd = win32gui.CreateWindowEx(
                ex_style,
                wc.lpszClassName,
                "DebugCrossOverlay",
                style,
                left, top, width, height,
                0, 0, wc.hInstance, None
            )
            
            # 设置黑色 (0x000000) 为完全透明色
            win32gui.SetLayeredWindowAttributes(hwnd, 0x000000, 0, 1)
            
            # 显示窗口且不激活它
            win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
            win32gui.UpdateWindow(hwnd)

            # 3. 绘制红色十字架
            cx, cy = width // 2, height // 2
            w_hdc = win32gui.GetDC(hwnd)
            
            red = 0x0000FF  # BGR 格式的纯红
            pen = win32gui.CreatePen(win32con.PS_SOLID, 2, red)
            old_pen = win32gui.SelectObject(w_hdc, pen)
            
            size = int(6 * scale)
            win32gui.MoveToEx(w_hdc, cx - size, cy)
            win32gui.LineTo(w_hdc, cx + size + 1, cy)
            win32gui.MoveToEx(w_hdc, cx, cy - size)
            win32gui.LineTo(w_hdc, cx, cy + size + 1)
            
            win32gui.UpdateWindow(hwnd)
            win32gui.PumpWaitingMessages()
            
            # 停留 0.4 秒，让用户有足够的时间看清
            time.sleep(0.4)
            
            # 清理 GDI 资源并注销窗口
            win32gui.SelectObject(w_hdc, old_pen)
            win32gui.DeleteObject(pen)
            win32gui.ReleaseDC(hwnd, w_hdc)
            win32gui.DestroyWindow(hwnd)
        except Exception:
            pass

    threading.Thread(target=_draw_thread, daemon=True).start()


def print_detect_result(name: str, is_green_found: bool, sampled_points: list):
    """终端显眼横幅打印最后一条消息的最终判定结果"""
    print(f"\n==================================================")
    print(f"[最后消息检测] (消息内容): '{name}'")
    print(f"[最后消息检测] (判定结果): {'[自己发送 (is_self=True)]' if is_green_found else '[对方发送 (is_self=False)]'}")
    print(f"[最后消息检测] (绿色匹配): {'成功 (找到绿色气泡)' if is_green_found else '失败 (未发现绿色气泡)'}")
    if sampled_points:
        print(f"[最后消息检测] (采样点数量): {len(sampled_points)}，首点坐标及色值: (x={int(sampled_points[0][0])}, y={int(sampled_points[0][1])}) RGB=({sampled_points[0][2]},{sampled_points[0][3]},{sampled_points[0][4]})")
    print(f"==================================================\n")


def print_fallback_result(name: str):
    """打印降级兜底信息"""
    print(f"\n==================================================")
    print(f"[最后消息检测] (消息内容): '{name}'")
    print(f"[最后消息检测] [警告] 处于降级状态 (窗口不满足采色条件)，默认判定为: [对方发送 (is_self=False)]")
    print(f"==================================================\n")
