"""完整流程诊断V4：打开添加朋友 → 找搜索框 → 粘贴号码 → 搜索"""
import win32gui
import uiautomation as uia
import win32api
import ctypes
import time
import pyperclip

# === Step 1: 确保微信前置 ===
from src.uia.retry import force_foreground
force_foreground(528144)
time.sleep(0.3)

w = uia.ControlFromHandle(528144)
print(f"微信窗口: {w.Name}")

# === Step 2: 坐标点击快捷操作 ===
quick_btn = w.ButtonControl(Name="快捷操作")
print(f"\n快捷操作按钮: Exists={quick_btn.Exists(1, 0.3)}")
rect = quick_btn.BoundingRectangle
cx = (rect.left + rect.right) // 2
cy = (rect.top + rect.bottom) // 2
win32api.SetCursorPos((cx, cy))
time.sleep(0.2)
ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
time.sleep(0.05)
ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)
time.sleep(1.0)

# === Step 3: 坐标点击添加朋友 ===
add_ctrl = w.Control(Name="添加朋友")
print(f"添加朋友菜单: Exists={add_ctrl.Exists(2, 0.3)}")
rect2 = add_ctrl.BoundingRectangle
cx2 = (rect2.left + rect2.right) // 2
cy2 = (rect2.top + rect2.bottom) // 2
win32api.SetCursorPos((cx2, cy2))
time.sleep(0.2)
ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
time.sleep(0.05)
ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)
time.sleep(1.5)

# === Step 4: 找到添加朋友窗口 ===
add_hwnds = []
def cb(hwnd, _):
    if win32gui.IsWindowVisible(hwnd):
        title = win32gui.GetWindowText(hwnd)
        if title == "添加朋友":
            add_hwnds.append(hwnd)
win32gui.EnumWindows(cb, None)

if not add_hwnds:
    print("❌ 添加朋友窗口未弹出")
    exit()

add_win = uia.ControlFromHandle(add_hwnds[0])
print(f"\n✅ 添加朋友窗口打开: hwnd={add_hwnds[0]}")

# === Step 5: 遍历子控件找搜索框 ===
print("\n子控件：")
for ctrl, depth in uia.WalkControl(add_win, maxDepth=5):
    indent = "  " * depth
    name = getattr(ctrl, 'Name', '')
    ctype = getattr(ctrl, 'ControlTypeName', '')
    print(f"{indent}{ctype}: Name='{name}'")

# === Step 6: 尝试找搜索框并点击 ===
edit = add_win.EditControl()
print(f"\n第一个 EditControl: Exists={edit.Exists(1,0.3)}, Name='{edit.Name if edit.Exists(0.5,0.3) else 'N/A'}'")

if edit.Exists(0.5, 0.3):
    # 坐标点击搜索框
    rect3 = edit.BoundingRectangle
    cx3 = (rect3.left + rect3.right) // 2
    cy3 = (rect3.top + rect3.bottom) // 2
    print(f"搜索框坐标: ({cx3}, {cy3})")
    win32api.SetCursorPos((cx3, cy3))
    time.sleep(0.2)
    ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.05)
    ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.5)
    
    # 粘贴手机号
    pyperclip.copy("13800000000")
    time.sleep(0.3)
    edit.SendKeys("{Ctrl}v")
    time.sleep(0.5)
    print("✅ 号码已粘贴")
    
    # 回车搜索
    uia.SendKeys("{Enter}")
    time.sleep(3.5)
    print("✅ 已发送搜索回车")
    
    # 遍历搜索结果
    print("\n搜索后子控件：")
    for ctrl2, depth2 in uia.WalkControl(add_win, maxDepth=5):
        indent2 = "  " * depth2
        name2 = getattr(ctrl2, 'Name', '')
        ctype2 = getattr(ctrl2, 'ControlTypeName', '')
        if name2:
            print(f"{indent2}{ctype2}: Name='{name2}'")

print("\n=== 完成 ===")
