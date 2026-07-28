r"""测试多显示器环境下的截图方案"""
import sys, time, ctypes, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path
import uiautomation as uia

OUT = Path(__file__).parent / "avatar_test_output"
OUT.mkdir(exist_ok=True)

wc = uia.WindowControl(ClassName="mmui::MainWindow")
if not wc.Exists(3): sys.exit("微信未打开")
ctypes.windll.user32.SetForegroundWindow(wc.NativeWindowHandle)
time.sleep(0.5)

r = wc.BoundingRectangle
wr = (int(r.left), int(r.top), int(r.right), int(r.bottom))
print(f"微信窗口: ({wr[0]},{wr[1]}) → ({wr[2]},{wr[3]})")

# 选一个测试区域（窗口中心 200x200）
cx = (wr[0] + wr[2]) // 2
cy = (wr[1] + wr[3]) // 2
test_box = (cx - 100, cy - 100, cx + 100, cy + 100)
print(f"测试截取区域: {test_box}")

# ─── 方法1: ImageGrab 默认 ───
print("\n--- 方法1: ImageGrab.grab (默认) ---")
try:
    from PIL import ImageGrab
    img = ImageGrab.grab(bbox=test_box)
    p = str(OUT / "test_default.png")
    img.save(p)
    pixels = list(img.getdata())[:5]
    all_black = all(px == (0, 0, 0) for px in pixels)
    print(f"  size={img.size} all_black={all_black} → {os.path.getsize(p)} bytes")
except Exception as e:
    print(f"  [FAIL] {e}")

# ─── 方法2: ImageGrab all_screens=True ───
print("\n--- 方法2: ImageGrab.grab(all_screens=True) ---")
try:
    from PIL import ImageGrab
    img_full = ImageGrab.grab(all_screens=True)
    # all_screens 返回的图像可能有偏移，需要计算偏移量
    print(f"  全屏尺寸: {img_full.size}")
    # 虚拟屏幕原点
    vl = ctypes.windll.user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
    vt = ctypes.windll.user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
    print(f"  虚拟屏幕原点: ({vl},{vt})")
    # 计算裁剪坐标
    crop_left = test_box[0] - vl
    crop_top = test_box[1] - vt
    crop_right = test_box[2] - vl
    crop_bottom = test_box[3] - vt
    print(f"  裁剪坐标: ({crop_left},{crop_top},{crop_right},{crop_bottom})")
    img_crop = img_full.crop((crop_left, crop_top, crop_right, crop_bottom))
    p = str(OUT / "test_allscreens.png")
    img_crop.save(p)
    pixels = list(img_crop.getdata())[:5]
    all_black = all(px == (0, 0, 0) for px in pixels)
    print(f"  crop size={img_crop.size} all_black={all_black} → {os.path.getsize(p)} bytes")
except Exception as e:
    print(f"  [FAIL] {e}")

# ─── 方法3: Win32 BitBlt ───
print("\n--- 方法3: Win32 BitBlt ---")
try:
    import win32gui, win32ui, win32con
    from PIL import Image

    w = test_box[2] - test_box[0]
    h = test_box[3] - test_box[1]

    hdesktop = win32gui.GetDesktopWindow()
    desktop_dc = win32gui.GetWindowDC(hdesktop)
    img_dc = win32ui.CreateDCFromHandle(desktop_dc)
    mem_dc = img_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(img_dc, w, h)
    mem_dc.SelectObject(bmp)
    mem_dc.BitBlt((0, 0), (w, h), img_dc, (test_box[0], test_box[1]), win32con.SRCCOPY)

    bmp_info = bmp.GetInfo()
    bmp_bits = bmp.GetBitmapBits(True)
    img = Image.frombuffer("RGB", (bmp_info["bmWidth"], bmp_info["bmHeight"]),
                           bmp_bits, "raw", "BGRX", 0, 1)
    p = str(OUT / "test_win32.png")
    img.save(p)
    pixels = list(img.getdata())[:5]
    all_black = all(px == (0, 0, 0) for px in pixels)
    print(f"  size={img.size} all_black={all_black} → {os.path.getsize(p)} bytes")

    mem_dc.DeleteDC()
    win32gui.DeleteObject(bmp.GetHandle())
    win32gui.ReleaseDC(hdesktop, desktop_dc)
except Exception as e:
    print(f"  [FAIL] {e}")

# ─── 方法4: mss ───
print("\n--- 方法4: mss ---")
try:
    import mss
    with mss.mss() as sct:
        monitors = sct.monitors
        print(f"  显示器: {monitors}")
        region = {"left": test_box[0], "top": test_box[1],
                  "width": test_box[2] - test_box[0], "height": test_box[3] - test_box[1]}
        img = sct.grab(region)
        from PIL import Image
        pil_img = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
        p = str(OUT / "test_mss.png")
        pil_img.save(p)
        pixels = list(pil_img.getdata())[:5]
        all_black = all(px == (0, 0, 0) for px in pixels)
        print(f"  size={pil_img.size} all_black={all_black} → {os.path.getsize(p)} bytes")
except ImportError:
    print("  [SKIP] mss 未安装")
except Exception as e:
    print(f"  [FAIL] {e}")

# ─── 方法5: pyautogui ───
print("\n--- 方法5: pyautogui ---")
try:
    import pyautogui
    img = pyautogui.screenshot(region=(test_box[0], test_box[1],
                                       test_box[2]-test_box[0], test_box[3]-test_box[1]))
    p = str(OUT / "test_pyautogui.png")
    img.save(p)
    pixels = list(img.getdata())[:5]
    all_black = all(px == (0, 0, 0) for px in pixels)
    print(f"  size={img.size} all_black={all_black} → {os.path.getsize(p)} bytes")
except ImportError:
    print("  [SKIP] pyautogui 未安装")
except Exception as e:
    print(f"  [FAIL] {e}")

# ─── 方法6: PrintWindow ───
print("\n--- 方法6: Win32 PrintWindow (窗口级截图) ---")
try:
    import win32gui, win32ui, win32con
    from PIL import Image

    hwnd = wc.NativeWindowHandle
    wrect = win32gui.GetWindowRect(hwnd)
    ww = wrect[2] - wrect[0]
    wh = wrect[3] - wrect[1]
    print(f"  窗口句柄={hwnd} rect={wrect}")

    hwndDC = win32gui.GetWindowDC(hwnd)
    mfcDC = win32ui.CreateDCFromHandle(hwndDC)
    saveDC = mfcDC.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfcDC, ww, wh)
    saveDC.SelectObject(bmp)

    # PW_RENDERFULLCONTENT = 2  (Win 8.1+, 支持 DWM)
    result = ctypes.windll.user32.PrintWindow(hwnd, saveDC.GetSafeHdc(), 2)
    print(f"  PrintWindow 返回: {result}")

    bmp_info = bmp.GetInfo()
    bmp_bits = bmp.GetBitmapBits(True)
    img = Image.frombuffer("RGB", (bmp_info["bmWidth"], bmp_info["bmHeight"]),
                           bmp_bits, "raw", "BGRX", 0, 1)

    # 整窗
    p = str(OUT / "test_printwindow_full.png")
    img.save(p)
    pixels = list(img.getdata())[:5]
    all_black = all(px == (0, 0, 0) for px in pixels)
    print(f"  full: size={img.size} all_black={all_black} → {os.path.getsize(p)} bytes")

    # 裁剪头像区域
    crop_left = test_box[0] - wrect[0]
    crop_top = test_box[1] - wrect[1]
    crop_right = test_box[2] - wrect[0]
    crop_bottom = test_box[3] - wrect[1]
    img_crop = img.crop((crop_left, crop_top, crop_right, crop_bottom))
    p2 = str(OUT / "test_printwindow_crop.png")
    img_crop.save(p2)
    pixels = list(img_crop.getdata())[:5]
    all_black = all(px == (0, 0, 0) for px in pixels)
    print(f"  crop: size={img_crop.size} all_black={all_black} → {os.path.getsize(p2)} bytes")

    saveDC.DeleteDC()
    win32gui.DeleteObject(bmp.GetHandle())
    win32gui.ReleaseDC(hwnd, hwndDC)
except Exception as e:
    print(f"  [FAIL] {e}")

print(f"\n输出目录: {OUT}")
