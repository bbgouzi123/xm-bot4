import uiautomation as auto
import time
wechat = auto.WindowControl(ClassName='WeChatMainWndForPC')
if not wechat.Exists(1, 0):
    wechat = auto.WindowControl(Name='微信')

pop = wechat.WindowControl(ClassName='mmui::ProfileUniquePop')
print('Waiting for pop... (open it manualy)')
while not pop.Exists(1,0):
    time.sleep(1)

print('Found pop, clicking head...')
import ctypes
ctypes.windll.user32.SendMessageW(pop.NativeWindowHandle, 0x003D, 0, -4)
time.sleep(0.3)
pop.Refind()
head = pop.ButtonControl(ClassName='mmui::ContactHeadView', searchDepth=10)
if head.Exists(1):
    head.Click()
    time.sleep(1)
    print("Enumerating top-level mmui windows...")
    import win32gui
    def dump_mmui(hwnd, _):
        cls = win32gui.GetClassName(hwnd)
        if "mmui" in cls or "Image" in cls or "Preview" in cls:
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                print(f"TopWindow: HWND={hwnd}, title={title}, class={cls}")
                
    win32gui.EnumWindows(dump_mmui, None)
else:
    print("Head not found!")
