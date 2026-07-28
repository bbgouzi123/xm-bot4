import uiautomation as auto
import time
import win32gui, win32con

wechat = auto.WindowControl(ClassName='WeChatMainWndForPC')
if not wechat.Exists(1, 0):
    wechat = auto.WindowControl(Name='微信')

pop = wechat.WindowControl(ClassName='mmui::ProfileUniquePop')
if pop.Exists(1,0):
    print("Found popup")
    # try simulating close logic
    old_hwnd = pop.NativeWindowHandle
    if old_hwnd:
        # win32gui.PostMessage(old_hwnd, win32con.WM_CLOSE, 0, 0)
        pop.SendKeys('{Esc}')
        print("Sent ESC")
        time.sleep(1)
        if pop.Exists(1,0):
            print("Popup still exists")
        else:
            print("Popup closed cleanly")
else:
    print("Popup not found. Is it on your screen? Please check if a white square remains.")
    # check for orphaned mmui::ProfileUniquePop without WeChat parent if any
    pop_root = auto.WindowControl(ClassName='mmui::ProfileUniquePop')
    if pop_root.Exists(1, 0):
        print("Found orphaned popup natively.")
        hwnd = pop_root.NativeWindowHandle
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        print("Killed orphaned popup")
