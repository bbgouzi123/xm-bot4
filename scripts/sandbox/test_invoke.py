import uiautomation as auto
import time
import win32gui

def enum_callback(hwnd, windows):
    if win32gui.IsWindowVisible(hwnd):
        class_name = win32gui.GetClassName(hwnd)
        title = win32gui.GetWindowText(hwnd)
        if title == "微信":
            windows.append(hwnd)
    return True

windows = []
win32gui.EnumWindows(enum_callback, windows)
print("wechat hwnds:", windows)

for hwnd in windows:
    wechat = auto.ControlFromHandle(hwnd)
    if wechat:
        print("found wechat root")
        toolbar = wechat.ToolBarControl(Name="导航")
        if toolbar.Exists(1):
            avatar = toolbar.GetChildren()[0]
            print(f"Avatar Name: {avatar.Name}")
            print(f"Control Type: {avatar.ControlTypeName}")
            invoke = avatar.GetInvokePattern()
            if invoke:
                print("Has InvokePattern!")
                invoke.Invoke()
            else:
                print("No InvokePattern. Trying .Click(simulateMove=False)")
                avatar.Click(simulateMove=False)
