import win32gui

def enum_callback(hwnd, _):
    title = win32gui.GetWindowText(hwnd)
    cls = win32gui.GetClassName(hwnd)
    if win32gui.IsWindowVisible(hwnd) and ("无标题" in title or "WeChat" in title or "微信" in title or cls == "Qt51514QWindowIcon"):
        print(f"HWND: {hwnd}, Title: '{title}', Class: '{cls}'")

win32gui.EnumWindows(enum_callback, None)
