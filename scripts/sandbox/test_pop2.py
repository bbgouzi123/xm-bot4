import uiautomation as auto
import time
import ctypes
import win32api
import win32con

def test():
    w = auto.WindowControl(ClassName='Qt51514QWindowIcon', Name='微信')
    if not w.Exists(1):
        print("Wechat not found")
        return
    nav = w.ToolBarControl(Name='导航')
    if not nav.Exists(1):
        print("Nav not found")
        return
    
    rect = nav.BoundingRectangle
    width = rect.right - rect.left
    
    offset_x = min(80, max(24, int(width * 0.45)))
    screen_h = ctypes.windll.user32.GetSystemMetrics(1)
    scale_y = max(1.0, screen_h / 1080.0)
    offset_y = int(min(25 + scale_y * 15, max(20, int(36 * scale_y))))
    
    target_x = rect.left + offset_x
    target_y = rect.top + offset_y
    print(f"Clicking at {target_x}, {target_y}")
    
    win32api.SetCursorPos((target_x, target_y))
    time.sleep(0.1)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.05)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    time.sleep(1)
    
    pop = auto.WindowControl(ClassName='mmui::ProfileUniquePop')
    if pop.Exists(1, 0.5):
        try:
            OBJID_CLIENT = -4
            WM_GETOBJECT = 0x003D
            hwnd_popup = pop.NativeWindowHandle
            if hwnd_popup:
                ctypes.windll.user32.SendMessageW(hwnd_popup, WM_GETOBJECT, 0, OBJID_CLIENT)
                time.sleep(0.3)
                pop.Refind()
        except:
            pass

        print("------------------ DUMPING PROFILE INFO ------------------")
        for c, d in auto.WalkControl(pop):
            print(f"Depth {d}: type={c.ControlTypeName}, name='{c.Name}', class={c.ClassName}")
        print("----------------------------------------------------------")
        
        try:
            pop.SendKeys('{Esc}')
        except:
            pass
    else:
        print("ProfileUniquePop not found after click!")

if __name__ == "__main__":
    test()
