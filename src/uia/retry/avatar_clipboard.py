"""头像剪贴板提取能力。"""

import ctypes
import time


def capture_avatar_via_clipboard(
    wc_ctrl,
    save_path: str,
    right_x: int,
    wechat_hwnd: int = 0,
    anchor_elem=None,
) -> bool:
    """通过剪贴板获取头像：点击详情页头像 -> 预览窗口 -> 右键复制 -> 保存。"""
    try:
        import os
        from io import BytesIO

        import uiautomation as uia_lib
        import win32api
        import win32clipboard
        import win32con
        from PIL import Image, ImageGrab

        hwnd = wechat_hwnd or 0
        if not hwnd:
            try:
                hwnd = wc_ctrl.NativeWindowHandle
            except Exception:
                pass

        wr = wc_ctrl.BoundingRectangle
        win_top = int(wr.top)

        anchor = anchor_elem
        if not anchor:
            for label in ("微信号：", "微信号:", "地区：", "地区:"):
                try:
                    t = wc_ctrl.TextControl(Name=label)
                    if t.Exists(0.5) and t.BoundingRectangle.left >= right_x:
                        anchor = t
                        break
                except Exception:
                    continue
        if anchor:
            try:
                ar = anchor.BoundingRectangle
                avatar_cx, avatar_cy = int(ar.left) - 75, int(ar.top) - 25
            except Exception:
                avatar_cx, avatar_cy = right_x + 100, win_top + 100
        else:
            avatar_cx, avatar_cy = right_x + 100, win_top + 100

        try:
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.CloseClipboard()
        except Exception:
            pass

        from src.uia.retry.clicks import physical_click, physical_right_click

        if hwnd:
            from src.uia.retry.window_ops import ensure_wechat_foreground
            ensure_wechat_foreground(hwnd)
            time.sleep(0.2)
        physical_click(avatar_cx, avatar_cy, settle=0.15, restore_cursor=False)
        print(f"[剪贴板头像] 点击头像 ({avatar_cx}, {avatar_cy}), 等待预览窗口...")
        time.sleep(0.4)

        preview = None
        for _retry in range(3):
            _find_start = time.time()
            children = uia_lib.GetRootControl().GetChildren()
            print(f"[剪贴板头像]   桌面子窗口数: {len(children)} ({time.time() - _find_start:.2f}s)")
            for w in children:
                try:
                    cls = w.ClassName or ""
                    if not cls.startswith("mmui::") or cls == "mmui::MainWindow":
                        continue
                    _ew = time.time()
                    if not w.Exists(0.1):
                        print(f"[剪贴板头像]   mmui窗口 {cls} Exists=False ({time.time() - _ew:.2f}s)")
                        continue
                    pwr = w.BoundingRectangle
                    if pwr.width() > 200 and pwr.height() > 200:
                        preview = w
                        print(f"[剪贴板头像]   找到预览: {cls} {pwr.width()}x{pwr.height()} ({time.time() - _ew:.2f}s)")
                        break
                except Exception:
                    continue
            if preview:
                break
            time.sleep(0.25)
        if not preview:
            print("[剪贴板头像] 未找到预览窗口")
            return False
        print(f"[剪贴板头像] 找到预览窗口, 开始右键复制...")

        pr = preview.BoundingRectangle
        pcx, pcy = int((pr.left + pr.right) / 2), int((pr.top + pr.bottom) / 2)
        try:
            from src.uia.retry.window_ops import force_foreground
            force_foreground(preview.NativeWindowHandle)
        except Exception:
            pass
        time.sleep(0.15)
        physical_right_click(pcx, pcy, settle=0.15, restore_cursor=False)
        time.sleep(0.3)

        copy_x, copy_y = pcx + 55, pcy + 25
        physical_click(copy_x, copy_y, settle=0.15, restore_cursor=False)
        time.sleep(0.2)
        print(f"[剪贴板头像] 右键复制完成, 读取剪贴板...")

        img = None
        try:
            img = ImageGrab.grabclipboard()
            if not isinstance(img, Image.Image):
                img = None
        except Exception:
            img = None

        if img is None:
            try:
                import struct

                win32clipboard.OpenClipboard()
                try:
                    if win32clipboard.IsClipboardFormatAvailable(8):  # CF_DIB
                        dib_data = win32clipboard.GetClipboardData(8)
                        if dib_data and len(dib_data) > 40:
                            hdr_size = struct.unpack_from("<I", dib_data, 0)[0]
                            bpp = struct.unpack_from("<H", dib_data, 14)[0]
                            pixel_offset = 14 + hdr_size if bpp == 32 else 14 + hdr_size
                            file_size = 14 + len(dib_data)
                            bmp_header = struct.pack("<2sIHHI", b"BM", file_size, 0, 0, pixel_offset)
                            img = Image.open(BytesIO(bmp_header + dib_data)).convert("RGB")
                finally:
                    win32clipboard.CloseClipboard()
            except Exception as e:
                print(f"[剪贴板头像] win32clipboard 读取异常: {e}")

        # ESC 关闭预览窗口
        _esc_start = time.time()
        win32api.keybd_event(win32con.VK_ESCAPE, 0, 0, 0)
        time.sleep(0.05)
        win32api.keybd_event(win32con.VK_ESCAPE, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.15)

        # 检查是否还有残留预览窗口，如有再按一次 ESC
        _cleanup_start = time.time()
        try:
            children2 = uia_lib.GetRootControl().GetChildren()
            for w in children2:
                try:
                    cls = w.ClassName or ""
                    if cls.startswith("mmui::") and cls != "mmui::MainWindow":
                        pwr = w.BoundingRectangle
                        if pwr.width() > 200 and pwr.height() > 200:
                            win32api.keybd_event(win32con.VK_ESCAPE, 0, 0, 0)
                            time.sleep(0.05)
                            win32api.keybd_event(win32con.VK_ESCAPE, 0, win32con.KEYEVENTF_KEYUP, 0)
                            time.sleep(0.15)
                            print(f"[剪贴板头像]   清理残留预览窗口 ({time.time() - _cleanup_start:.2f}s)")
                            break
                except Exception:
                    continue
        except Exception:
            pass
        print(f"[剪贴板头像] ESC+清理耗时: {time.time() - _esc_start:.2f}s")

        if not img:
            print("[剪贴板头像] 剪贴板中无图片（CF_BITMAP 和 CF_DIB 均未获取到）")
            return False

        if img.mode != "RGB":
            img = img.convert("RGB")
        os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
        img.save(save_path)
        _ok = os.path.exists(save_path) and os.path.getsize(save_path) > 100
        print(f"[剪贴板头像] 保存完成: {save_path} (size={os.path.getsize(save_path) if _ok else 0})")
        return _ok
    except Exception as e:
        print(f"[剪贴板头像] 失败: {e}")
        try:
            import win32api
            import win32con

            win32api.keybd_event(win32con.VK_ESCAPE, 0, 0, 0)
            time.sleep(0.05)
            win32api.keybd_event(win32con.VK_ESCAPE, 0, win32con.KEYEVENTF_KEYUP, 0)
        except Exception:
            pass
        return False
