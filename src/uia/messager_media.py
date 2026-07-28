import os
import re
import time
import logging
import uiautomation as uia
import pyperclip
from typing import Optional

logger = logging.getLogger("WeChatDriver")

def is_control_valid(control) -> bool:
    try:
        r = control.BoundingRectangle if control else None
        return bool(r and r.left != 0 and control.ClassName)
    except Exception: return False

def right_click_menu_item(control, item_name_substr: str | list[str], is_self: bool = False) -> bool:
    """右键点击控件，并通过 UIA 定位或键盘导航选中目标菜单项"""
    if not is_control_valid(control):
        return False
    targets = [item_name_substr] if isinstance(item_name_substr, str) else item_name_substr

    try:
        act_ctrl = control
        cls_name = getattr(control, "ClassName", "")
        if cls_name == "mmui::ChatItemView":
            for child, _ in uia.WalkControl(control, maxDepth=4):
                c_cls = getattr(child, "ClassName", "")
                if c_cls in ("mmui::ChatImageItemView", "mmui::ChatVideoItemView", "mmui::ChatFileItemView", "mmui::ChatVoiceItemView", "mmui::ChatBubbleItemView", "mmui::ChatBubbleReferItemView"):
                    act_ctrl, cls_name = child, c_cls
                    break
        rect = act_ctrl.BoundingRectangle
        
        # 🚀 计算点击位置，若是图片/视频/引用气泡等，且宽度正常，则直击其几何中心，避免拉伸偏置跑偏
        from src.uia.message_direction_helper import get_dpi_scale
        scale = get_dpi_scale()
        
        if cls_name in ("mmui::ChatImageItemView", "mmui::ChatVideoItemView", "mmui::ChatFileItemView", "mmui::ChatBubbleReferItemView") or "Image" in cls_name:
            width = rect.right - rect.left
            if 0 < width < int(500 * scale):
                if not is_self:
                    click_x = rect.left + width // 4
                else:
                    click_x = rect.right - width // 4
                click_y = rect.top + (rect.bottom - rect.top) // 2
                logger.info(f"[UIA] 图片/媒体气泡使用 1/4 几何偏置: ({click_x}, {click_y}), class={cls_name}")
            else:
                if not is_self:
                    click_x = rect.left + int(120 * scale)
                else:
                    click_x = rect.right - int(120 * scale)
                click_y = rect.top + int(30 * scale)
                logger.info(f"[UIA] 图片/媒体气泡使用头像偏置: ({click_x}, {click_y}), class={cls_name}")
        else:
            if cls_name in ("mmui::ChatVoiceItemView", "mmui::ChatBubbleItemView", "mmui::ChatItemView"):
                if not is_self:
                    click_x = rect.left + int(120 * scale)
                else:
                    click_x = rect.right - int(120 * scale)
                click_y = rect.top + int((rect.bottom - rect.top) * 0.5)
            else:
                click_x = (rect.left + rect.right) // 2
                click_y = (rect.top + rect.bottom) // 2
                
        # 🚀 使用 uia.RightClick (自动适配多显示器DPI换算并模拟物理鼠标右键)
        try:
            from src.uia.retry.clicks import _get_shield_ctx
            with _get_shield_ctx():
                uia.RightClick(click_x, click_y)
        except Exception as click_err:
            logger.warning(f"[UIA] 模拟物理右键异常: {click_err}")
            uia.RightClick(click_x, click_y)
        
        time.sleep(0.4)  # 等待菜单渲染
        
        # 避免全局遍历，用最快速的非递归方式探测浅层菜单
        menu = uia.Control(ClassName='mmui::XMenu', searchDepth=2)
        if not menu.Exists(0.15, 0.05):
            menu = uia.Control(ClassName='CMenuWnd', searchDepth=2)
            
        if not menu.Exists(0.15, 0.05):
            # 🚀 模糊扫描 Desktop 子节点中符合 Menu 条件的顶级控件，增强兼容性
            try:
                desktop = uia.GetRootControl()
                for child in desktop.GetChildren():
                    cls = getattr(child, "ClassName", "")
                    ctype = getattr(child, "ControlTypeName", "")
                    if "Menu" in cls or "Menu" in ctype or cls == "mmui::XPopover":
                        menu = child
                        break
            except Exception as scan_ex:
                logger.debug(f"[UIA] 模糊扫描桌面菜单异常: {scan_ex}")
            
        if menu.Exists(0.3):
            children = menu.GetChildren()
            valid_children = [c for c in children if getattr(c, "Name", "")]
            found_names = [c.Name for c in valid_children]
            logger.info(f"[UIA] 右键菜单已弹出，可用项: {found_names}")
            
            target_idx = -1
            for idx, item in enumerate(valid_children):
                name = item.Name or ""
                if any(t in name for t in targets):
                    target_idx = idx
                    break
            
            if target_idx != -1:
                down_count = target_idx + 1
                logger.info(f"[UIA] 动态计算得到菜单项 [{valid_children[target_idx].Name}] 索引为 {target_idx}，键盘导航: Down * {down_count} + Enter")
                for _ in range(down_count):
                    uia.SendKeys('{Down}', waitTime=0.08)
                uia.SendKeys('{Enter}', waitTime=0.1)
                return True
            logger.warning(f"[UIA] 菜单匹配 {targets} 失败，可用项: {found_names}")
            
        # 键盘导航兜底策略：图片另存为 3 次 Down + Enter，复制为 1 次 Down + Enter
        primary_target = targets[0]
        if "另存" in primary_target:
            down_count = 3 if cls_name in ("mmui::ChatImageItemView", "mmui::ChatBubbleReferItemView") else 2
            logger.info(f"[UIA] UIA菜单查找未命中，键盘盲按另存为兜底: Down * {down_count}")
            for _ in range(down_count):
                uia.SendKeys('{Down}', waitTime=0.08)
            uia.SendKeys('{Enter}', waitTime=0.1)
            return True
        elif "复制" in primary_target or "copy" in primary_target.lower():
            logger.info(f"[UIA] UIA菜单查找未命中，键盘盲按复制兜底: Down * 1")
            uia.SendKeys('{Down}', waitTime=0.08)
            uia.SendKeys('{Enter}', waitTime=0.1)
            return True
        elif any(x in primary_target for x in ("文字", "转", "收起")):
            uia.SendKeys('{Down}', waitTime=0.08)
            uia.SendKeys('{Enter}', waitTime=0.1); return True
        logger.warning(f"[UIA] 未检测到任何右键菜单，且无对应键盘兜底映射，匹配 {targets} 失败")
        return False
    except Exception as e:
        logger.error(f"[UIA] 右键选中菜单项 {targets} 异常: {e}")
    return False


def translate_voice_to_text(item) -> str:
    if not is_control_valid(item): return ""
    def _c(s):
        s = re.sub(r'^语音\s*\d+[\s\\\"\'秒分]*(?:秒|分)?', '', s or "").strip()
        return re.sub(r'^[\\\"\'`\s\-\:\：]+', '', s).strip()
    
    deadline = time.time() + 2.0
    while time.time() < deadline:
        c = _c(item.Name)
        if c and c != (item.Name or "") and "翻译" not in c and "转写" not in c: return c
        time.sleep(0.2)
    from src.uia.input_guard import uia_lock
    with uia_lock("正在执行语音转文字，请勿操作键盘鼠标"):
        try:
            if not right_click_menu_item(item, ["转为文字", "语音转文字", "转文字", "收起文字"]): return ""
            deadline = time.time() + 5.0
            while time.time() < deadline:
                texts = [c.Name.strip() for c in item.GetChildren() if c.Name and c.Name != "语音" and not re.match(r'^\d+[\"\'秒秒]$', c.Name) and "翻译" not in c.Name and "转写" not in c.Name]
                if texts: return "。".join(texts)
                c = _c(item.Name)
                if c and c != (item.Name or "") and "翻译" not in c and "转写" not in c: return c
                time.sleep(0.3)
        except Exception as e:
            logger.error(f"[UIA] 语音气泡物理转文字异常: {e}")
        return ""

def save_chat_file(item, target_dir: str = None) -> str:
    """物理右键点击图片/文件消息气泡，通过另存为对话框下载到本地目录，并返回绝对路径"""
    if not is_control_valid(item): return ""
    if not target_dir: target_dir = os.path.join(os.path.expanduser("~"), ".xm-ai-bot", "downloads")
    os.makedirs(target_dir, exist_ok=True)
    
    from src.uia.input_guard import uia_lock
    with uia_lock("正在另存为图片/文件，请勿操作键盘鼠标"):
        dialog = None
        is_success = False
        try:
            if not right_click_menu_item(item, "另存为"): return ""
            dialog = uia.WindowControl(ClassName='#32770')
            if not dialog.Exists(5.0, 0.5):
                logger.warning("[UIA] 未检测到另存为窗口（等待 5 秒超时）"); return ""
                
            dialog_hwnd = dialog.NativeWindowHandle
            file_edit = dialog.EditControl(AutomationId='1001')
            if not file_edit.Exists(1.0): file_edit = dialog.EditControl(searchDepth=2, ClassName='Edit')
            if file_edit.Exists(1.0):
                default_name = file_edit.GetValuePattern().Value or f"download_{int(time.time())}"
                safe_name = re.sub(r'[\\/*?:"<>|]', "", default_name)
                local_path = os.path.abspath(os.path.join(target_dir, safe_name))
                
                file_edit.SetFocus()
                uia.SendKeys('{Ctrl}a', waitTime=0.08)
                uia.SendKeys('{Delete}', waitTime=0.08)
                pyperclip.copy(local_path)
                uia.SendKeys('{Ctrl}v', waitTime=0.1)
                time.sleep(0.3)
                
                save_btn = dialog.ButtonControl(AutomationId='1')
                if not save_btn.Exists(0.3):
                    for btn_name in ('保存(S)', '保存(&S)', '保存', 'Save'):
                        _btn = dialog.ButtonControl(Name=btn_name)
                        if _btn.Exists(0.1): save_btn = _btn; break
                if save_btn and save_btn.Exists(0.5):
                    try:
                        invoke = save_btn.GetInvokePattern()
                        if invoke: invoke.Invoke()
                        else: save_btn.Click(simulateMove=False)
                    except Exception:
                        try: save_btn.Click(simulateMove=False)
                        except Exception: pass
                    logger.info("[UIA] 保存按钮已点击")
                else:
                    logger.warning("[UIA] 未找到保存按钮控件，使用 Enter 键兜底触发保存")
                    uia.SendKeys('{Enter}', waitTime=0.1)
                
                time.sleep(0.5)
                _confirm_dlg = uia.WindowControl(searchDepth=1, ClassName='#32770', Name='确认另存为')
                if not _confirm_dlg.Exists(0.15):
                    _confirm_dlg = dialog.WindowControl(searchDepth=2, ClassName='#32770', Name='确认另存为')
                if not _confirm_dlg.Exists(0.15):
                    for wnd in uia.GetRootControl().GetChildren():
                        if wnd.ClassName == '#32770' and wnd.NativeWindowHandle != dialog_hwnd:
                            _confirm_dlg = wnd; break
                            
                if _confirm_dlg and _confirm_dlg.Exists(1.0, 0.3):
                    _yes_btn = _confirm_dlg.ButtonControl(AutomationId='6')
                    if not _yes_btn.Exists(0.2):
                        for _yn in ('是(Y)', '是(&Y)', '是', 'Yes'):
                            _yb = _confirm_dlg.ButtonControl(Name=_yn)
                            if _yb.Exists(0.1): _yes_btn = _yb; break
                    if _yes_btn and _yes_btn.Exists(0.2):
                        try: _yes_btn.Click(simulateMove=False)
                        except Exception: pass
                    else:
                        uia.SendKeys('{Alt}y', waitTime=0.1)
                time.sleep(0.3)
                
                _deadline = time.time() + 4.0
                _saved_ok = False
                while time.time() < _deadline:
                    try:
                        if os.path.isfile(local_path) and os.path.getsize(local_path) > 64:
                            _saved_ok = True; break
                    except OSError: pass
                    time.sleep(0.15)
                
                if _saved_ok:
                    logger.info(f"[UIA] 图片/文件已成功保存到物理路径（已验证落盘）: {local_path}")
                    is_success = True
                    return local_path
                else:
                    logger.warning("[UIA] 首次保存未检测到落盘，尝试 Enter 重试确认...")
                    uia.SendKeys('{Enter}', waitTime=0.1)
                    time.sleep(0.5)
                    if os.path.isfile(local_path) and os.path.getsize(local_path) > 64:
                        logger.info(f"[UIA] 图片/文件已成功保存到物理路径（重试后落盘）: {local_path}")
                        is_success = True
                        return local_path
                    logger.error(f"[UIA] 图片/文件保存失败：文件未落盘 {local_path}")
        except Exception as e:
            logger.error(f"[UIA] 图片/文件物理另存为异常: {e}")
        finally:
            try:
                if not is_success and dialog and dialog.Exists(0.2):
                    logger.warning("[UIA] 另存为图片/文件未成功落盘或发生异常，正在物理关闭残留的另存为窗口以防挂起...")
                    import win32gui
                    import win32con
                    hwnd = dialog.NativeWindowHandle
                    if hwnd and win32gui.IsWindow(hwnd):
                        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                    else:
                        dialog.SendKeys('{Esc}')
            except Exception as close_ex:
                logger.debug(f"[UIA] 物理关闭另存为窗口异常: {close_ex}")
            try:
                from src.uia.message_direction_helper import find_wechat_window
                import win32gui
                h = find_wechat_window()
                if h and win32gui.IsWindow(h): win32gui.SetForegroundWindow(h)
            except Exception: pass
            time.sleep(0.3)
        return ""

def locate_local_voice_file(account_id: str) -> Optional[str]:
    if not account_id: return None
    try:
        w_path = None
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Tencent\WeChat", 0, winreg.KEY_READ) as k:
                val = winreg.QueryValueEx(k, "FileSavePath")[0].strip()
                w_path = os.path.join(os.path.expanduser("~"), "Documents", "WeChat Files") if val.startswith("MyDocuments:") else (val if val.endswith("WeChat Files") else os.path.join(val, "WeChat Files"))
        except Exception: pass
        if not w_path or not os.path.exists(w_path):
            w_path = os.path.join(os.path.expanduser("~"), "Documents", "WeChat Files")
        voice_dir = os.path.join(w_path, account_id, "FileStorage", "Voice")
        if not os.path.exists(voice_dir): return None
        now, newest_file, newest_mtime = time.time(), None, 0
        for root, _, files in os.walk(voice_dir):
            for f in files:
                if f.endswith((".silk", ".amr")):
                    fp = os.path.join(root, f)
                    m = os.path.getmtime(fp)
                    if now - m < 20.0 and m > newest_mtime:
                        newest_mtime, newest_file = m, fp
        return newest_file
    except Exception as e:
        logger.error(f"[语音兜底] 搜寻微信物理语音缓存失败: {e}"); return None

def call_whisper_api(file_path: str) -> Optional[str]:
    try:
        api_url = os.getenv("WHISPER_API_URL", "").strip()
        api_key = os.getenv("WHISPER_API_KEY", "").strip()
        if not api_url: return None
        import requests
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        with open(file_path, "rb") as f:
            res = requests.post(
                f"{api_url}/v1/audio/transcriptions", headers=headers, 
                files={"file": (os.path.basename(file_path), f, "audio/octet-stream")}, 
                data={"model": "whisper-1", "language": "zh"}, timeout=10
            )
            if res.status_code == 200: return res.json().get("text", "").strip()
    except Exception as err:
        logger.error(f"[语音兜底] Whisper ASR 物理请求失败: {err}")
    return None
