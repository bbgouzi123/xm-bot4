import sys
import os
import time
import uiautomation as uia
import win32gui

# 添加项目根目录到路径
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

import ctypes

def force_accessibility_refresh(hwnd):
    print(f"Sending WM_GETOBJECT to {hwnd}...")
    OBJID_CLIENT = 0xFFFFFFFC # -4
    WM_GETOBJECT = 0x003D
    ctypes.windll.user32.SendMessageW(hwnd, WM_GETOBJECT, 0, OBJID_CLIENT)
    
    print("Setting SPI_SETSCREENREADER to True...")
    SPI_SETSCREENREADER = 0x0047
    ctypes.windll.user32.SystemParametersInfoW(SPI_SETSCREENREADER, 1, None, 3)

import ctypes
import uiautomation as uia

def force_accessibility_refresh(hwnd):
    print(f"\n[Action] Sending WM_GETOBJECT to HWND {hwnd}...")
    OBJID_CLIENT = 0xFFFFFFFC # -4
    WM_GETOBJECT = 0x003D
    ctypes.windll.user32.SendMessageW(hwnd, WM_GETOBJECT, 0, OBJID_CLIENT)
    
    print("[Action] Setting SPI_SETSCREENREADER to True...")
    SPI_SETSCREENREADER = 0x0047
    ctypes.windll.user32.SystemParametersInfoW(SPI_SETSCREENREADER, 1, None, 3)

import win32process

def inspect_window(hwnd, label):
    title = win32gui.GetWindowText(hwnd)
    class_name = win32gui.GetClassName(hwnd)
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    print(f"\n{'='*20} Inspecting {label} {'='*20}")
    print(f"HWND: {hwnd} | PID: {pid} | Class: {class_name} | Title: {title}")
    
    # Check environment variable of THIS PID if possible
    # We can't easily read another process's environment from Python without helper
    
    force_accessibility_refresh(hwnd)
    time.sleep(0.5)
    
    window = uia.ControlFromHandle(hwnd)
    children = window.GetChildren()
    print(f"Direct Children Count: {len(children)}")
    
    if len(children) == 0:
        print("!!! WARNING: Tree is EMPTY for this window.")
    else:
        print("-" * 60)
        print(f"{'Depth':<6} | {'ControlType':<15} | {'Name':<35} | {'Class':<25}")
        print("-" * 60)
        # 增加深度到 15，深入探测
        for control, depth in uia.WalkControl(window, maxDepth=15):
            print(f"{depth:<6} | {control.ControlTypeName:<15} | {control.Name or '':<35} | {control.ClassName}")
            if "导航" in (control.Name or "") or "聊天" in (control.Name or ""):
                print(f"[{depth}] >>> FOUND TARGET: {control.Name}")
    print('='*50)

def inspect_wechat():
    print("Starting Global AutomationId Search...")
    root = uia.GetRootControl()
    target = root.ToolBarControl(AutomationId="main_tabbar")
    if target.Exists(0):
        print(f"!!! SUCCESS! Found main_tabbar globally!")
        print(f"Name: {target.Name} | Parent HWND: {target.NativeWindowHandle}")
    else:
        print("Failed to find main_tabbar globally.")
        
    print("\nStarting Global Discovery...")
    targets = []
    
    def callback(hwnd, extra):
        if not win32gui.IsWindowVisible(hwnd): 
            return True
        title = win32gui.GetWindowText(hwnd)
        class_name = win32gui.GetClassName(hwnd)
        
        if "微信" in title or "Weixin" in title or "WeChat" in title:
            targets.append((hwnd, f"Match: {title} ({class_name})"))
        elif class_name in ["Qt51514QWindowIcon", "WeChatMainWndForPC"]:
            targets.append((hwnd, f"Match: {class_name}"))
        return True

    win32gui.EnumWindows(callback, None)
    
    if not targets:
        print("No WeChat windows found.")
        return
        
    for hwnd, label in sorted(list(set(targets))):
        inspect_window(hwnd, label)

if __name__ == "__main__":
    inspect_wechat()

if __name__ == "__main__":
    inspect_wechat()
