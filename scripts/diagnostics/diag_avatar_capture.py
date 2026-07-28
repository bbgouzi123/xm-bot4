r"""
测试剪贴板方案获取微信头像 v3：
点击联系人 → 点击详情页头像 → 预览窗口右键复制 → 剪贴板读取 → 关闭预览
"""
import sys, os, time, ctypes
from pathlib import Path
from io import BytesIO
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import uiautomation as uia
from PIL import Image, ImageGrab
import win32api, win32con, win32clipboard


OUT = Path(__file__).parent / "avatar_test_output"
OUT.mkdir(exist_ok=True)


def ex(ctrl, t=1.0):
    try:
        return ctrl is not None and ctrl.Exists(t)
    except:
        return False


def bring_front(hwnd):
    try:
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        time.sleep(0.3)
    except:
        pass


def clear_clipboard():
    win32clipboard.OpenClipboard()
    win32clipboard.EmptyClipboard()
    win32clipboard.CloseClipboard()


def get_clipboard_image():
    """从剪贴板获取图片（支持 CF_BITMAP 和 CF_DIB）"""
    # 方法A: ImageGrab
    try:
        img = ImageGrab.grabclipboard()
        if isinstance(img, Image.Image):
            return img
    except Exception as e:
        print(f"    [DEBUG] grabclipboard: {e}")
    # 方法B: win32clipboard 读取 CF_DIB（微信常用此格式）
    try:
        import struct
        from io import BytesIO
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(8):  # CF_DIB
                dib = win32clipboard.GetClipboardData(8)
                if dib and len(dib) > 40:
                    hdr_size = struct.unpack_from('<I', dib, 0)[0]
                    file_size = 14 + len(dib)
                    bmp_header = struct.pack('<2sIHHI', b'BM', file_size, 0, 0, 14 + hdr_size)
                    img = Image.open(BytesIO(bmp_header + dib))
                    return img.convert("RGB")
        finally:
            win32clipboard.CloseClipboard()
    except Exception as e:
        print(f"    [DEBUG] CF_DIB: {e}")
    return None


def press_key(vk):
    win32api.keybd_event(vk, 0, 0, 0)
    time.sleep(0.05)
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.1)


def right_click(x, y):
    win32api.SetCursorPos((x, y))
    time.sleep(0.15)
    win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
    time.sleep(0.05)
    win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)


def left_click(x, y):
    win32api.SetCursorPos((x, y))
    time.sleep(0.15)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.05)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def find_preview_window():
    """查找微信图片预览窗口"""
    for w in uia.GetRootControl().GetChildren():
        try:
            cls = w.ClassName or ""
            if not cls.startswith("mmui::"):
                continue
            if cls == "mmui::MainWindow":
                continue
            if not w.Exists(0.3):
                continue
            wr = w.BoundingRectangle
            if wr.width() > 200 and wr.height() > 200:
                return w
        except:
            continue
    return None


def get_visible_contacts(cl):
    """获取当前可见的联系人行"""
    contacts = []
    for item in cl.GetChildren():
        if "ContactsCellItemView" in (item.ClassName or "") and (item.Name or "").strip():
            try:
                rr = item.BoundingRectangle
                if rr.width() > 0 and rr.height() > 0:
                    contacts.append(item)
            except:
                continue
    return contacts


def find_anchor_text(wc, right_x):
    """找右侧资料卡中的锚点文字"""
    for label in ("微信号：", "微信号:", "地区：", "地区:"):
        t = wc.TextControl(Name=label)
        if ex(t, 0.5):
            try:
                if t.BoundingRectangle.left >= right_x:
                    return t
            except:
                pass
    return None


def main():
    print("=" * 60)
    print("  剪贴板方案获取微信头像测试 v3")
    print("=" * 60)

    wc = uia.WindowControl(ClassName="mmui::MainWindow")
    if not ex(wc, 3):
        print("[X] 微信未打开")
        return
    hwnd = wc.NativeWindowHandle
    bring_front(hwnd)
    time.sleep(0.5)

    wr = wc.BoundingRectangle
    win_left = int(wr.left)
    win_top = int(wr.top)
    win_w = int(wr.right) - win_left
    right_x = win_left + int(win_w * 0.4)

    # 确保在通讯录
    btn = wc.ButtonControl(Name="通讯录")
    if ex(btn, 2):
        btn.Click()
        time.sleep(1)
    cl = wc.ListControl(Name="通讯录")
    if not ex(cl, 2):
        print("[X] 无列表")
        return
    # 展开联系人
    if not any("ContactsCellItemView" in (i.ClassName or "") for i in cl.GetChildren()):
        for item in cl.GetChildren():
            if (item.Name or "").strip().startswith("联系人"):
                item.Click()
                time.sleep(2)
                break

    # 收集联系人名称
    target_names = []
    for item in cl.GetChildren():
        if "ContactsCellItemView" in (item.ClassName or "") and (item.Name or "").strip():
            target_names.append(item.Name.strip())
            if len(target_names) >= 3:
                break
    print(f"待测联系人: {target_names}")

    success = 0
    for idx, name in enumerate(target_names):
        print(f"\n{'~'*50}")
        print(f"  [{idx+1}/{len(target_names)}] '{name}'")
        print(f"{'~'*50}")

        # 每次重新获取行引用（防止虚拟化列表引用失效）
        bring_front(hwnd)
        time.sleep(0.3)

        row = None
        for item in cl.GetChildren():
            if "ContactsCellItemView" in (item.ClassName or "") and (item.Name or "").strip() == name:
                try:
                    rr = item.BoundingRectangle
                    if rr.width() > 0:
                        row = item
                        break
                except:
                    continue

        if not row:
            print("  [SKIP] 找不到可见行")
            continue

        # ── 步骤1: 点击联系人
        bring_front(hwnd)
        time.sleep(0.2)
        row.Click()
        time.sleep(1.2)

        # ── 步骤2: 定位头像位置
        # 头像在 "微信号：" 文字的左侧，垂直略偏上
        anchor = find_anchor_text(wc, right_x)
        if anchor:
            ar = anchor.BoundingRectangle
            # 头像中心：在锚点文字左边约 75px，垂直向上约 25px
            avatar_cx = int(ar.left) - 75
            avatar_cy = int(ar.top) - 25
            print(f"  2. 锚点 '{anchor.Name}' at ({int(ar.left)},{int(ar.top)})")
        else:
            # 估算
            avatar_cx = right_x + 100
            avatar_cy = win_top + 100
            print(f"  2. 无锚点，估算位置")
        print(f"     头像点击位置: ({avatar_cx},{avatar_cy})")

        # ── 步骤3: 清空剪贴板
        clear_clipboard()

        # ── 步骤4: 点击头像
        bring_front(hwnd)
        time.sleep(0.2)
        left_click(avatar_cx, avatar_cy)
        time.sleep(1.5)

        # ── 步骤5: 查找预览窗口
        preview = find_preview_window()
        if not preview:
            time.sleep(1.0)
            preview = find_preview_window()

        if not preview:
            print("  [FAIL] 未找到预览窗口")
            press_key(win32con.VK_ESCAPE)
            time.sleep(0.5)
            continue

        print(f"  5. 预览窗口: {preview.ClassName} {int(preview.BoundingRectangle.width())}x{int(preview.BoundingRectangle.height())}")

        # ── 步骤6: 右键预览窗口中央
        pr = preview.BoundingRectangle
        pcx = int((pr.left + pr.right) / 2)
        pcy = int((pr.top + pr.bottom) / 2)
        print(f"  6. 预览窗口 rect: ({int(pr.left)},{int(pr.top)},{int(pr.right)},{int(pr.bottom)})")
        print(f"     右键位置: ({pcx},{pcy})")
        bring_front(preview.NativeWindowHandle)
        time.sleep(0.3)
        right_click(pcx, pcy)
        time.sleep(0.8)

        # ── 步骤7: 点击"复制"
        # 右键菜单出现在右键位置，"复制"是第一项
        # 从用户截图看菜单宽约 150px，每项高约 35px
        # "复制"在右键位置正下方约 25px，水平居中偏右约 55px
        copy_x = pcx + 55
        copy_y = pcy + 25
        print(f"  7. 点击复制 ({copy_x},{copy_y})...")
        left_click(copy_x, copy_y)
        time.sleep(0.5)

        # ── 步骤8: 读取剪贴板
        img = get_clipboard_image()
        if img:
            img = img.convert("RGB")
            p = str(OUT / f"{name}_clipboard.png")
            img.save(p)
            print(f"  8. [OK] 头像: {img.size} → {os.path.getsize(p)} bytes")
            success += 1
        else:
            print("  8. [FAIL] 剪贴板无图片")

        # ── 步骤9: 关闭预览窗口
        press_key(win32con.VK_ESCAPE)
        time.sleep(0.5)
        if find_preview_window():
            press_key(win32con.VK_ESCAPE)
            time.sleep(0.5)

    print(f"\n{'='*60}")
    print(f"  结果: {success}/{len(target_names)} 成功")
    print(f"  输出: {OUT}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
