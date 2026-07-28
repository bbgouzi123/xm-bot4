"""微信大图预览窗口检测、关闭及物理点击交互辅助方法。"""
import time
from typing import Optional

import win32api
import win32con
import win32gui
import win32process
import uiautomation as uia

from src.uia.retry import try_click


def _is_likely_qt_image_preview_class(cls: str) -> bool:
    """微信 PC 图片查看器窗口：勿写死 Qt51514，避免小版本升级后枚举不到预览窗。"""
    if not cls:
        return False
    return cls.startswith("Qt") and "QWindowIcon" in cls


def _collect_visible_top_hwnds_for_pid(pid: int) -> set:
    found: set = set()

    def _cb(h, _):
        try:
            if not win32gui.IsWindowVisible(h):
                return
            _, p = win32process.GetWindowThreadProcessId(h)
            if p == pid:
                found.add(h)
        except Exception:
            pass

    win32gui.EnumWindows(_cb, None)
    return found


def _pick_new_preview_hwnd(
    before: set,
    after: set,
    exclude: set,
    min_w: int = 220,
    min_h: int = 220,
) -> Optional[int]:
    """点击头像后新增的顶层窗口里，取面积最大的 Qt 图片预览类窗口。"""
    newcomers = (after - before) - exclude
    best = (0, None)
    for h in newcomers:
        try:
            cls = win32gui.GetClassName(h)
            r = win32gui.GetWindowRect(h)
            w, ht = r[2] - r[0], r[3] - r[1]
            if w < min_w or ht < min_h:
                continue
            if not _is_likely_qt_image_preview_class(cls):
                continue
            area = w * ht
            if area > best[0]:
                best = (area, h)
        except Exception:
            continue
    return best[1]


def _preview_save_name_matches(name: str) -> bool:
    if not (name or "").strip():
        return False
    n = name.strip()
    exact = (
        "下载",
        "保存",
        "另存为",
        "保存图片",
        "下载原图",
        "Save",
        "Download",
    )
    if n in exact:
        return True
    for hint in ("另存", "保存图片", "下载", "Save", "Download", "Save As"):
        if hint in n:
            return True
    return False


def _physical_right_click_xy(x: int, y: int) -> None:
    from src.uia.retry.clicks import physical_right_click
    physical_right_click(x, y, settle=0.06)
    time.sleep(0.15)


def _try_click_cmwnd_save_as() -> bool:
    """微信右键菜单多为 CMenuWnd，菜单项 Name 常为「另存为...」。"""
    menu_hwnd = None
    # 增加轮询时间最大至 1.2 秒，确保在卡顿或低配电脑下右键菜单能够完全加载完毕
    for _ in range(24):
        h = win32gui.FindWindow("CMenuWnd", "")
        if h and win32gui.IsWindowVisible(h):
            menu_hwnd = h
            break
        # 兼容部分 Qt Popup 菜单类名
        def _enum_qt_popups(hwnd, extra):
            if win32gui.IsWindowVisible(hwnd):
                cls = win32gui.GetClassName(hwnd)
                if cls.startswith("Qt") and "Popup" in cls:
                    extra.append(hwnd)
            return True
        
        qt_popups = []
        try:
            win32gui.EnumWindows(_enum_qt_popups, qt_popups)
        except Exception:
            pass
        if qt_popups:
            menu_hwnd = qt_popups[0]
            break
        time.sleep(0.05)
    
    if menu_hwnd:
        try:
            menu = uia.ControlFromHandle(menu_hwnd)
            for ctrl, _ in uia.WalkControl(menu, maxDepth=4):
                try:
                    if getattr(ctrl, "ControlTypeName", "") != "MenuItemControl":
                        continue
                    nm = getattr(ctrl, "Name", "") or ""
                    if (
                        "另存" in nm
                        or nm in ("保存图片", "Save Image")
                        or "Save As" in nm
                    ):
                        if try_click(ctrl, max_retries=2, delay=0.25):
                            print(f"[UIA] 成功点击另存为菜单项: {nm}")
                            return True
                except Exception:
                    continue
        except Exception:
            pass

    # 🌟 键盘导航优先采用 win32api 物理模拟以确保微信焦点菜单 100% 触发另存为 (Down, Down, Enter)
    try:
        # 给菜单极短暂的缓冲激活期，确保菜单获得键盘焦点
        time.sleep(0.2)
        print("[UIA] 尝试通过 win32api 键盘模拟 (Down, Down, Enter) 触发另存为...")
        # 第一次 Down
        win32api.keybd_event(win32con.VK_DOWN, 0, 0, 0)
        time.sleep(0.12)  # 稍微增大延时，防按键发送过快被系统漏掉
        win32api.keybd_event(win32con.VK_DOWN, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.12)
        
        # 第二次 Down
        win32api.keybd_event(win32con.VK_DOWN, 0, 0, 0)
        time.sleep(0.12)
        win32api.keybd_event(win32con.VK_DOWN, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.12)
        
        # 回车 Enter
        win32api.keybd_event(win32con.VK_RETURN, 0, 0, 0)
        time.sleep(0.15)
        win32api.keybd_event(win32con.VK_RETURN, 0, win32con.KEYEVENTF_KEYUP, 0)
        return True
    except Exception as e:
        print(f"[UIA] 键盘另存为兜底异常: {e}")
        
    return False


def _try_toolbar_download_click(preview_hwnd: int) -> None:
    """工具栏「下载」多为无 Name 的图标按钮：在标题栏/工具栏右侧区域尝试物理左键。"""
    try:
        from src.uia.retry.clicks import physical_click

        r = win32gui.GetWindowRect(preview_hwnd)
        w = r[2] - r[0]
        h = r[3] - r[1]
        if w < 80 or h < 80:
            return
        # 下载在顶栏偏右；多取几个点兼容不同宽度/DPI
        ys = (max(22, int(h * 0.055)), max(28, int(h * 0.07)))
        xs = (0.84, 0.88, 0.82, 0.90)
        for frac_x in xs:
            for frac_y in ys:
                x = int(r[0] + w * frac_x)
                y = int(r[1] + h * frac_y)
                physical_click(x, y, settle=0.05, restore_cursor=False)
                time.sleep(0.04)
            time.sleep(0.35)
    except Exception:
        pass


def _physical_click_uia_element(ctrl, settle_s: float = 0.07) -> bool:
    """对 UIA 控件边界中心做真实左键点击（Qt/微信工具栏上 Invoke 常无效）。"""
    try:
        from src.uia.retry.clicks import physical_click

        r = ctrl.BoundingRectangle
        rw = r.right - r.left
        rh = r.bottom - r.top
        if rw < 2 or rh < 2:
            return False
        x = (r.left + r.right) // 2
        y = (r.top + r.bottom) // 2
        physical_click(x, y, settle=settle_s)
        return True
    except Exception:
        return False


def _close_qt_image_preview(preview_hwnd: int, preview_ctrl) -> None:
    """关闭微信大图预览 — 物理点击右上角 × 按钮，配合 WM_CLOSE 消息与图片中心物理点击多重保障。"""
    if not preview_hwnd and not preview_ctrl:
        return

    t0 = time.time()

    if preview_hwnd:
        try:
            from src.uia.retry.clicks import physical_click

            if not win32gui.IsWindow(preview_hwnd):
                print(f"[UIA-close] 预览窗口已不存在，跳过关闭")
                return

            try:
                r = win32gui.GetWindowRect(preview_hwnd)
            except Exception as w_err:
                if "1400" in str(w_err) or getattr(w_err, "winerror", None) == 1400:
                    print(f"[UIA-close] 预览窗口已提前关闭 (1400)")
                    return
                raise

            w = r[2] - r[0]
            h = r[3] - r[1]
            if w < 50 or h < 50:
                print(f"[UIA-close] 预览窗口尺寸异常: {w}x{h}, 跳过")
                return

            # 1. 率先异步发送标准的 WM_CLOSE 消息给微信大图预览线程，促其以最快速度从内存中销毁
            try:
                win32gui.PostMessage(preview_hwnd, win32con.WM_CLOSE, 0, 0)
            except Exception:
                pass

            # 2. 同时执行物理点击右上角 X 按钮
            close_x = r[2] - 23
            close_y = r[1] + 18
            print(f"[UIA-close] 点击右上角关闭按钮: ({close_x}, {close_y})")
            physical_click(close_x, close_y, settle=0.05, restore_cursor=False)

            # 3. 等待窗口销毁/不可见
            for _ in range(12):  # 最多 0.6s
                time.sleep(0.05)
                try:
                    if not win32gui.IsWindow(preview_hwnd) or not win32gui.IsWindowVisible(preview_hwnd):
                        print(f"[UIA-close] 物理点击与WM_CLOSE关闭成功, 耗时: {time.time() - t0:.3f}s")
                        return
                except Exception:
                    print(f"[UIA-close] 窗口已销毁, 耗时: {time.time() - t0:.3f}s")
                    return

            # 4. 兜底方案：物理点击大图正中心，这是微信图片查看器最天然的退出方式
            print(f"[UIA-close] 窗口依然处于激活，尝试物理点击大图中心退出...")
            cx = (r[0] + r[2]) // 2
            cy = (r[1] + r[3]) // 2
            physical_click(cx, cy, settle=0.05, restore_cursor=False)
            time.sleep(0.15)
        except Exception as e:
            if "1400" not in str(e) and getattr(e, "winerror", None) != 1400:
                print(f"[UIA-close] 点击关闭异常: {e}")
            else:
                print(f"[UIA-close] 预览窗口已不存在(1400)")

    # 兜底再次 WM_CLOSE
    if preview_hwnd:
        try:
            win32gui.PostMessage(preview_hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception:
            pass

    print(f"[UIA-close] 总关闭动作执行完毕，耗时: {time.time() - t0:.3f}s")


def _pick_toolbar_save_button(candidates: list, preview) -> Optional[object]:
    """同名「保存」可能在树里出现多次：取靠近预览窗口顶部的按钮（工具栏）。"""
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0][0]
    try:
        pr = preview.BoundingRectangle
        ph = max(1, pr.bottom - pr.top)

        def rank(item):
            ctrl, name = item
            rr = ctrl.BoundingRectangle
            cy = (rr.top + rr.bottom) / 2
            rel_y = (cy - pr.top) / ph
            nm = (name or "").strip()
            prefer = 0.0 if nm == "保存" else 0.03
            return rel_y + prefer

        return min(candidates, key=rank)[0]
    except Exception:
        return candidates[0][0]
