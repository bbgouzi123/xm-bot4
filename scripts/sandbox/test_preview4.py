import uiautomation as auto
import time
import win32api
import win32con

wechat = auto.WindowControl(ClassName='WeChatMainWndForPC')
if not wechat.Exists(1, 0):
    wechat = auto.WindowControl(Name='微信')

# summon popup
print("Summoning popup...")
wechat.SwitchToThisWindow()
time.sleep(0.5)

nav = wechat.ToolBarControl(Name='导航')
rect = nav.BoundingRectangle
w = rect.right - rect.left
import ctypes
screen_h = ctypes.windll.user32.GetSystemMetrics(1)
scale_y = max(1.0, screen_h / 1080.0)
tx = rect.left + min(80, max(24, int(w*0.45)))
ty = rect.top + int(min(25 + scale_y*15, max(20, int(36*scale_y))))
old_pos = win32api.GetCursorPos()
win32api.SetCursorPos((tx, ty))
win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
time.sleep(0.5)
win32api.SetCursorPos(old_pos)

pop = wechat.WindowControl(ClassName='mmui::ProfileUniquePop')
if not pop.Exists(1, 0):
    pop = auto.WindowControl(ClassName='mmui::ProfileUniquePop')

if pop.Exists(1, 0):
    ctypes.windll.user32.SendMessageW(pop.NativeWindowHandle, 0x003D, 0, -4)
    time.sleep(0.3)
    pop.Refind()
    head = pop.ButtonControl(ClassName='mmui::ContactHeadView', searchDepth=10)
    if head.Exists(1):
        print("Clicking head...")
        # move cursor to center of head and click to be sure
        head.Click()
        time.sleep(1)
        print("Looking for image preview window...")
        for win in auto.GetRootControl().GetChildren():
            if win.ControlTypeName == 'WindowControl' and ('mmui' in win.ClassName or 'Image' in win.ClassName or 'Preview' in win.ClassName or 'ContactProfile' in win.ClassName):
                print(f"TopWindow: class={win.ClassName}, name={win.Name}")
                # Look for download button
                # Print all buttons in the window using WalkControl
                print("Buttons:")
                for c, d in auto.WalkControl(win, maxDepth=10):
                    if c.ControlTypeName == 'ButtonControl' or c.ControlTypeName == 'ImageControl':
                        print(f"  [{c.ControlTypeName}] Name='{c.Name}', Class='{c.ClassName}'")
                        
                # Attempt to close it with Esc
                win.SendKeys('{Esc}')
                print("Sent ESC to close preview")
                time.sleep(0.5)
        
        # close popup
        pop.SendKeys('{Esc}')
    else:
        print("Head view not found, only pop found")
else:
    print("pop not summoned!")
