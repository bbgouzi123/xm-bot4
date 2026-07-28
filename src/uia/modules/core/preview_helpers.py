"""微信头像大图预览 / 另存为流程的纯函数辅助（由原 core 模块抽出，逻辑不变）。"""
import os
import time
import threading
from typing import Optional

import win32gui
import win32process
import uiautomation as uia

from src.uia.retry import try_click
from .preview_window import (
    _collect_visible_top_hwnds_for_pid,
    _pick_new_preview_hwnd,
    _is_likely_qt_image_preview_class,
    _preview_save_name_matches,
    _pick_toolbar_save_button,
    _physical_click_uia_element,
    _try_toolbar_download_click,
    _physical_right_click_xy,
    _try_click_cmwnd_save_as,
    _close_qt_image_preview,
)
from .preview_dialog import _handle_save_as_dialog_common


def download_avatar_from_head_view(
    head_view,
    wxid: str,
    main_hwnd: int,
    exclude_hwnds: set = None,
    is_friend: bool = False,
    bot_wxid: Optional[str] = None,
) -> Optional[str]:
    """
    统一的头像大图预览下载保存方法。
    
    返回保存的 PNG 图片本地路径。
    """
    from src.crm.account_data import get_account_data_dir, get_active_account
    
    if is_friend:
        # 存放于：C:\Users\Administrator\.xm-ai-bot\accounts\<登录微信账户>\contacts_avatar\<好友微信号>.png
        active_bot = bot_wxid or get_active_account()
        save_dir = os.path.join(get_account_data_dir(active_bot), "contacts_avatar")
        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(save_dir, f"{wxid}.png")
    else:
        # 主账号自己存放于：C:\Users\Administrator\.xm-ai-bot\accounts\<主账号微信>.png
        from src.crm.account_data import ACCOUNTS_DIR
        save_dir = ACCOUNTS_DIR
        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(save_dir, f"{wxid}.png")

    try:
        from src.uia.retry.clicks import physical_click
        from src.uia.retry import ensure_wechat_foreground

        hv_rect = head_view.BoundingRectangle
        hv_x = (hv_rect.left + hv_rect.right) // 2
        hv_y = (hv_rect.top + hv_rect.bottom) // 2
        print(f"[UIA] 物理点击头像控件: ({hv_x}, {hv_y})")

        _, wx_pid = win32process.GetWindowThreadProcessId(main_hwnd)
        snap_exclude = {main_hwnd}
        if exclude_hwnds:
            snap_exclude.update(exclude_hwnds)
        before_hwnds = _collect_visible_top_hwnds_for_pid(wx_pid)

        active_hwnd = main_hwnd
        if exclude_hwnds:
            for h in exclude_hwnds:
                if h != main_hwnd:
                    if win32gui.IsWindow(h) and win32gui.IsWindowVisible(h):
                        active_hwnd = h
                        break

        if active_hwnd == main_hwnd:
            ensure_wechat_foreground(main_hwnd)
        else:
            import ctypes
            user32 = ctypes.windll.user32
            if user32.GetForegroundWindow() != active_hwnd:
                print(f"[UIA] 资料卡弹窗未处于前台，正在尝试置顶弹窗 hwnd={active_hwnd}")
                from src.uia.retry import force_foreground
                force_foreground(active_hwnd)

        physical_click(hv_x, hv_y, settle=0.1)
        time.sleep(0.2)

        preview = None
        preview_hwnd = None
        for _poll in range(28):
            after_hwnds = _collect_visible_top_hwnds_for_pid(wx_pid)
            preview_hwnd = _pick_new_preview_hwnd(
                before_hwnds, after_hwnds, snap_exclude, 220, 220
            )
            if preview_hwnd:
                preview = uia.ControlFromHandle(preview_hwnd)
                _cls = win32gui.GetClassName(preview_hwnd)
                print(f"[UIA] 预览窗口(差分): hwnd={preview_hwnd}, cls={_cls}")
                break

            new_wins = []
            def _enum_pw(h, _p):
                try:
                    if not win32gui.IsWindowVisible(h):
                        return
                    _, pid = win32process.GetWindowThreadProcessId(h)
                    if pid == wx_pid and h not in snap_exclude:
                        cls = win32gui.GetClassName(h)
                        r = win32gui.GetWindowRect(h)
                        w = r[2] - r[0]
                        ht = r[3] - r[1]
                        new_wins.append((h, cls, w, ht))
                except Exception:
                    pass

            win32gui.EnumWindows(_enum_pw, None)
            for h, cls, w, ht in new_wins:
                if (
                    _is_likely_qt_image_preview_class(cls)
                    and w > 200
                    and ht > 200
                ):
                    preview_hwnd = h
                    preview = uia.ControlFromHandle(h)
                    print(
                        f"[UIA] 预览窗口(枚举): hwnd={h}, cls={cls}, {w}x{ht}"
                    )
                    break
            if preview:
                break
            time.sleep(0.12)

        if not preview:
            print("[UIA] 预览窗口未找到，跳过头像保存")
            return None

        if not preview_hwnd:
            try:
                preview_hwnd = int(preview.NativeWindowHandle)
            except Exception:
                preview_hwnd = 0

        # 执行保存流程
        saved_ok = False

        # 1. 优先采用在预览图区域右键 → 另存为 (右键，Down两次，Enter)
        if preview_hwnd:
            print("[UIA] 优先尝试在预览图区域右键 → 另存为...")
            try:
                pr = preview.BoundingRectangle
                cx = (pr.left + pr.right) // 2
                cy = max(pr.top + 100, (pr.top + pr.bottom) // 2)
                _physical_right_click_xy(cx, cy)
                time.sleep(0.35)
                if _try_click_cmwnd_save_as():
                    time.sleep(0.8)
                    saved_ok = _handle_save_as_dialog_common(save_path)
            except Exception as _e:
                print(f"[UIA] 右键另存为失败: {_e}")

        # 2. 兜底方案 A：寻找并点击工具栏上的保存按钮
        if not saved_ok:
            print("[UIA] 右键另存为未成功，启动工具栏按钮寻找并点击兜底...")
            _candidates = []
            _all_btns = []

            def _search_download():
                try:
                    import comtypes
                    comtypes.CoInitialize()
                except Exception:
                    pass
                count = 0
                try:
                    for ctrl, _ in uia.WalkControl(preview, maxDepth=10):
                        count += 1
                        if count > 600:
                            break
                        try:
                            bc_type = getattr(ctrl, "ControlTypeName", "") or ""
                            bc_name = getattr(ctrl, "Name", "") or ""
                            if bc_type in ("ButtonControl", "SplitButtonControl"):
                                _all_btns.append(bc_name or f"<empty#{count}>")
                                if _preview_save_name_matches(bc_name):
                                    _candidates.append((ctrl, bc_name))
                        except Exception:
                            continue
                except Exception:
                    pass

            t2 = threading.Thread(target=_search_download, daemon=True)
            t2.start()
            t2.join(timeout=4.0)

            download_btn = _pick_toolbar_save_button(_candidates, preview)
            print(f"[UIA] 预览窗口按钮采样(含空名): {_all_btns[:25]}{'...' if len(_all_btns) > 25 else ''}")
            if download_btn:
                print("[UIA] 命中保存类控件：边界中心物理左键...")
                _physical_click_uia_element(download_btn)
                time.sleep(0.95)
                saved_ok = _handle_save_as_dialog_common(save_path)
                if not saved_ok:
                    print("[UIA] 物理点击未出另存为，回退 Invoke/Uia.Click...")
                    try_click(download_btn, max_retries=2, delay=0.3)
                    time.sleep(0.85)
                    saved_ok = _handle_save_as_dialog_common(save_path)

        # 3. 兜底方案 B：尝试工具栏下载区域物理点击
        if not saved_ok and preview_hwnd:
            print("[UIA] 保存按钮点击未成功，尝试工具栏下载区域物理点击...")
            _try_toolbar_download_click(preview_hwnd)
            time.sleep(0.6)
            saved_ok = _handle_save_as_dialog_common(save_path)

        # 关闭预览
        _t_close_start = time.time()
        print("[UIA] 开始关闭预览窗口...")
        _close_qt_image_preview(int(preview_hwnd) if preview_hwnd else 0, preview)
        print(f"[UIA] 关闭预览窗口耗时: {time.time() - _t_close_start:.2f}s")

        # 🌟 [关键优化] 强制微信主窗口置前并发送 GDI 重绘命令，瞬间刷掉 DWM 脏显存缓存中的大图残留（Ghost Window）
        if main_hwnd and win32gui.IsWindow(main_hwnd):
            try:
                from src.uia.retry import force_foreground
                force_foreground(main_hwnd)
                # 使窗口区域失效并强制触发同步重绘以刷新界面
                win32gui.InvalidateRect(main_hwnd, None, True)
                win32gui.UpdateWindow(main_hwnd)
            except Exception:
                pass

        if saved_ok:
            # 🌟 [关键优化] 大图已在视觉上关闭，此时我们在后台等待并验证磁盘文件是否落盘，消灭前台 5 秒视觉卡顿感
            from .preview_dialog import _wait_avatar_file
            if _wait_avatar_file(save_path, timeout=4.5):
                return save_path
            else:
                print(f"[UIA] 警告：另存为对话框已关闭但超时未检测到文件落盘: {save_path}")

    except Exception as e:
        print(f"[UIA] 头像提取异常: {e}")
    return None
