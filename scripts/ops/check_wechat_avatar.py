import sys
import ctypes
import win32gui
import uiautomation as uia

def check():
    hwnd = win32gui.FindWindow("Qt51514QWindowIcon", "微信")
    if not hwnd:
        print("Wechat not found!")
        return
    
    rect = win32gui.GetWindowRect(hwnd)
    print("Wechat Rect:", rect)
    
    root = uia.ControlFromHandle(hwnd)
    nav_toolbar = root.ToolBarControl(Name="导航")
    if not nav_toolbar.Exists(2, 0.5):
        print("Nav bar not found.")
        return
        
    nav_rect = nav_toolbar.BoundingRectangle
    print("Nav Rect:", nav_rect)
    
    width = nav_rect.right - nav_rect.left
    
    offset_x = min(80, max(24, int(width * 0.45)))
    screen_h = ctypes.windll.user32.GetSystemMetrics(1)
    scale_y = max(1.0, screen_h / 1080.0)
    offset_y = int(min(25 + scale_y * 15, max(20, int(36 * scale_y))))
    
    target_x = nav_rect.left + offset_x
    target_y = nav_rect.top + offset_y
    
    print(f"Calculated Avatar Coords: ({target_x}, {target_y})")
    print(f"Screen Height: {screen_h}, offset: x={offset_x}, y={offset_y}, width={width}")

    # See if there's any UIA control at these coordinates
    ctrl = uia.ControlFromPoint(target_x, target_y)
    if ctrl:
        print("Ctrl at point:", ctrl.Name, ctrl.ControlTypeName, ctrl.ClassName)
    else:
        print("No ctrl at point")

if __name__ == '__main__':
    check()
