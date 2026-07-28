import uiautomation as auto
import time
import win32api
import win32con
import ctypes
import os
import win32gui
import win32process

def enum_callback(hwnd, results):
    if win32gui.GetClassName(hwnd) == "Qt51514QWindowIcon" and win32gui.GetWindowText(hwnd) == "微信" and win32gui.IsWindowVisible(hwnd):
        results.append(hwnd)

results = []
win32gui.EnumWindows(enum_callback, results)

if not results:
    print("WeChat 4.x window not found")
    os._exit(1)

hwnd = results[0]
wechat = auto.ControlFromHandle(hwnd)

user32 = ctypes.windll.user32
user32.ShowWindow(hwnd, 5)
user32.SetForegroundWindow(hwnd)
time.sleep(1)

nav = wechat.ToolBarControl(Name='导航')
rect = nav.BoundingRectangle
width = rect.right - rect.left
offset_x = min(80, max(24, int(width * 0.45)))
screen_h = ctypes.windll.user32.GetSystemMetrics(1)
scale_y = max(1.0, screen_h / 1080.0)
offset_y = int(min(25 + scale_y * 15, max(20, int(36 * scale_y))))
target_x = rect.left + offset_x
target_y = rect.top + offset_y

win32api.SetCursorPos((target_x, target_y))
time.sleep(0.1)
win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
time.sleep(0.05)
win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

time.sleep(1.5)

popup = auto.WindowControl(ClassName='mmui::ProfileUniquePop')
with open('dump_wechat.txt', 'w', encoding='utf-8') as f:
    if popup.Exists(2, 0.5):
        # Wake up popup using -4
        hwnd_pop = popup.NativeWindowHandle
        if hwnd_pop:
            ctypes.windll.user32.SendMessageW(hwnd_pop, 0x003D, 0, -4)
            time.sleep(0.5)
            popup.Refind()
            
        f.write(f'--- MATCHED WINDOW: ClassName={popup.ClassName}, Name={popup.Name} ---\n')
        # maxDepth=14 just like real app
        for ctrl, depth in auto.WalkControl(popup, maxDepth=14):
            f.write('  ' * depth + f'{ctrl.ControlTypeName} - Name: {ctrl.Name}, ClassName: {ctrl.ClassName}\n')
    else:
        f.write('POPUP NOT FOUND\n')

win32api.keybd_event(win32con.VK_ESCAPE, 0, 0, 0)
win32api.keybd_event(win32con.VK_ESCAPE, 0, win32con.KEYEVENTF_KEYUP, 0)
