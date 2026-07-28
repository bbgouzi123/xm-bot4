"""Windows 另存为对话框的处理辅助方法。"""
import os
import time
import ctypes
import uiautomation as uia


def _wait_avatar_file(path: str, timeout: float = 4.5) -> bool:
    """另存为点「保存」后轮询磁盘，避免对话框假成功（路径未进编辑框等）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if os.path.isfile(path) and os.path.getsize(path) > 64:
                return True
        except OSError:
            pass
        time.sleep(0.12)
    return False


def _handle_save_as_dialog_common(save_path: str, timeout: float = 4.5) -> bool:
    """处理 Windows 另存为对话框的通用方法"""
    save_dlg = uia.WindowControl(ClassName="#32770")
    if not save_dlg.Exists(3, 0.5):
        return False
    print("[UIA] 另存为对话框已弹出")
    try:
        try:
            save_dlg.SetActive(True)
        except Exception:
            pass
        edit = save_dlg.EditControl(AutomationId="1001")
        if not edit.Exists(0.5):
            for ctrl, _ in uia.WalkControl(save_dlg, maxDepth=6):
                try:
                    if getattr(ctrl, "ControlTypeName", "") == "EditControl" and ctrl.Exists(0.2):
                        edit = ctrl
                        break
                except Exception:
                    continue

        if edit and edit.Exists(0.3):
            try:
                edit.SetFocus()
                # 🌟 [核心增强] 物理点击输入框中心，确保在多屏/高 DPI 环境下键盘焦点 100% 切换过去
                r = edit.BoundingRectangle
                if r.right - r.left > 4 and r.bottom - r.top > 4:
                    from src.uia.retry.clicks import physical_click
                    cx = (r.left + r.right) // 2
                    cy = (r.top + r.bottom) // 2
                    physical_click(cx, cy, settle=0.15)
            except Exception:
                pass
            import pyperclip
            try:
                edit.SendKeys("{Ctrl}a{Delete}", waitTime=0.1)
                pyperclip.copy(save_path)
                time.sleep(0.1)  # 给系统剪贴板写入一定的时序缓冲时间
                edit.SendKeys("{Ctrl}v", waitTime=0.2)
            except Exception as e:
                print(f"[UIA] 剪贴板粘贴失败: {e}，回退到 SendKeys...")
                edit.SendKeys("{Ctrl}a{Delete}", waitTime=0.1)
                edit.SendKeys(save_path, waitTime=0.15)
            print(f"[UIA] 另存为路径: {save_path}")
        else:
            print("[UIA] 警告：未定位文件名编辑框，保存可能落在默认下载目录")

        # 🌟 [关键精调] 粘贴完成后延迟 150ms，给另存为对话框足够的时间把文件名同步进内部变量中
        time.sleep(0.15)

        # 优先使用 Enter 回车提交，这是 Windows 另存为最稳健且无冲突的触发保存手段
        try:
            if edit and edit.Exists(0.2):
                edit.SendKeys("{Enter}", waitTime=0.1)
        except Exception:
            pass

        # 寻找保存按钮作为回车失效时的兜底
        save_btn = None
        for btn_name in ("保存(S)", "保存(&S)", "保存", "Save"):
            b = save_dlg.ButtonControl(Name=btn_name)
            if b.Exists(0.1):
                save_btn = b
                break
        if not save_btn:
            save_btn = save_dlg.ButtonControl(AutomationId="1")

        # 如果敲完 Enter 键后对话框依然存在，则执行点击保存按钮兜底
        if save_dlg.Exists(0.2) and save_btn and save_btn.Exists(0.2):
            invoke = save_btn.GetInvokePattern()
            try:
                if invoke:
                    invoke.Invoke()
                else:
                    save_btn.Click(simulateMove=False)
            except Exception:
                try:
                    save_btn.Click(simulateMove=False)
                except Exception:
                    pass
            print("[UIA] 兜底保存按钮已点击")
            
            # 等文件系统响应 + 可能弹出"确认另存为"覆盖对话框
            time.sleep(0.2)
            VK_RETURN = 0x0D
            ctypes.windll.user32.keybd_event(VK_RETURN, 0, 0, 0)
            time.sleep(0.02)
            ctypes.windll.user32.keybd_event(VK_RETURN, 0, 0x0002, 0)

        # 🌟 [关键优化] 仅轮询等待另存为对话框本身关闭，不再在此处死等磁盘文件落盘，消灭视觉延时
        dlg_closed = False
        for _ in range(12):  # 最多等待 0.6s
            if not save_dlg.Exists(0.05):
                dlg_closed = True
                break
            time.sleep(0.05)

        if not dlg_closed:
            # 再次尝试回车，处理可能的被覆盖提示挂起
            try:
                save_dlg.SendKeys("{Enter}")
            except Exception:
                pass
            time.sleep(0.1)
            dlg_closed = not save_dlg.Exists(0.1)

        return dlg_closed
    except Exception as e:
        print(f"[UIA] 处理另存为对话框异常: {e}")
    return False
