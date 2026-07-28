import time
import logging
import uiautomation as uia
from src.uia.elements import WxClass, WxName
from src.uia.retry import random_delay, try_click, exists_with_timeout

logger = logging.getLogger("WeChatDriver")

def _is_wechat_title(title: str) -> bool:
    if not title:
        return False
    t = title.strip()
    if len(t) >= 3:
        if t.startswith("[#]") or t.startswith("[#]"):
            t = t[3:].strip()
    if len(t) >= 3:
        if t.endswith("[#]") or t.endswith("[#]"):
            t = t[:-3].strip()
    if t == "微信":
        return True
    if t.endswith("微信") and t.startswith("[") and "] " in t:
        suffix = t.split("] ", 1)[1]
        if suffix == "微信":
            return True
    if t.endswith("微信") and t.startswith("[") and "]" in t:
        parts = t.split("]", 1)
        if len(parts) == 2 and parts[1].strip() == "微信":
            return True
    return False

def find_sidebar_moments_nav(driver_obj):
    """新版 PC 微信：侧栏「朋友圈」与「微信」「通讯录」同级（mmui::XTabBarItem）。"""
    from src.utils.safe_uia import safe_walk_control
    from src.uia.startup_flow.refresh import force_accessibility_refresh
    import win32gui
    
    try:
        driver_obj.root.Refind()
        force_accessibility_refresh(driver_obj.hwnd, driver_obj.root)
    except Exception as ref_e:
        logger.debug(f"[UIA] root refind/refresh error: {ref_e}")
        
    try:
        navbar = driver_obj.root.ToolBarControl(ClassName="mmui::MainTabBar")
        if navbar.Exists(1.5):
            all_buttons = []
            for ctrl, _ in safe_walk_control(navbar, max_depth=3):
                try:
                    cls = ctrl.ClassName or ""
                    type_name = ctrl.ControlTypeName or ""
                    if cls == "mmui::XTabBarItem" or type_name == "ButtonControl":
                        if ctrl.Name == WxName.MOMENTS_NAV or ctrl.Name == "朋友圈":
                            logger.error(f"[UIA] 成功在 MainTabBar 内部直接匹配到 Name 为 '{ctrl.Name}' 的朋友圈按钮")
                            return ctrl
                        
                        rect = ctrl.BoundingRectangle
                        if rect:
                            if not any(abs(b.BoundingRectangle.top - rect.top) < 8 for b in all_buttons):
                                all_buttons.append(ctrl)
                except Exception:
                    continue
            
            if len(all_buttons) >= 4:
                all_buttons.sort(key=lambda x: x.BoundingRectangle.top if x.BoundingRectangle else 0)
                logger.error(f"[UIA] 侧栏朋友圈 Name 匹配未果，通过 Y 坐标排序选取第 4 个按钮: Name='{all_buttons[3].Name}' ClassName='{all_buttons[3].ClassName}'")
                return all_buttons[3]
    except Exception as e:
        logger.error(f"[UIA] 侧栏导航检索异常: {e}")

    fallback = None
    for ctrl, _depth in safe_walk_control(driver_obj.root, max_depth=12):
        try:
            if (ctrl.Name == WxName.MOMENTS_NAV or ctrl.Name == "朋友圈") and ctrl.ControlTypeName == "ButtonControl":
                if ctrl.ClassName == WxClass.TAB_ITEM:
                    return ctrl
                if fallback is None:
                    fallback = ctrl
        except Exception:
            continue
    if fallback:
        logger.error(f"[UIA] 全局遍历定位到朋友圈按钮: Name='{fallback.Name}'")
        return fallback

    try:
        win_left, win_top, _, _ = win32gui.GetWindowRect(driver_obj.hwnd)
        sidebar_buttons = []
        
        for ctrl, _depth in safe_walk_control(driver_obj.root, max_depth=12):
            try:
                if getattr(ctrl, "ControlTypeName", "") != "ButtonControl":
                    continue
                rect = ctrl.BoundingRectangle
                if not rect:
                    continue
                w = rect.right - rect.left
                h = rect.bottom - rect.top
                if (win_left <= rect.left <= win_left + 85) and (rect.top > win_top + 80) and (20 <= w <= 65) and (20 <= h <= 65):
                    cls = getattr(ctrl, "ClassName", "")
                    if "XImage" in cls or "TabBarItem" in cls or cls == "QWidget":
                        if not any(abs(b["rect"].left - rect.left) < 5 and abs(b["rect"].top - rect.top) < 5 for b in sidebar_buttons):
                            sidebar_buttons.append({
                                "ctrl": ctrl,
                                "rect": rect,
                                "top": rect.top
                            })
            except Exception:
                continue
        
        if sidebar_buttons:
            sidebar_buttons.sort(key=lambda x: x["top"])
            if len(sidebar_buttons) >= 4:
                target_btn = sidebar_buttons[3]["ctrl"]
                logger.info(f"[UIA] 成功通过左侧边栏全局物理 Y 坐标排序定位到朋友圈按钮")
                return target_btn
    except Exception as e:
        logger.error(f"[UIA] 物理定位侧栏朋友圈按钮发生异常: {e}")
        
    return None

def wait_moments_window(timeout: float = 5.0):
    """检出朋友圈独立窗口"""
    import win32gui
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hwnd = win32gui.FindWindow(WxClass.SNS_WINDOW, None)
        if not hwnd:
            hwnd = win32gui.FindWindow(None, WxName.SNS_WINDOW_TITLE)
        
        if hwnd and win32gui.IsWindow(hwnd):
            try:
                sns_win = uia.ControlFromHandle(hwnd)
                if sns_win and exists_with_timeout(sns_win, 0.5):
                    return sns_win
            except Exception:
                pass
        time.sleep(0.2)

    try:
        sns_win = uia.WindowControl(ClassName=WxClass.SNS_WINDOW)
        if sns_win and exists_with_timeout(sns_win, 0.5):
            return sns_win
    except Exception:
        pass
        
    try:
        sns_by_name = uia.WindowControl(Name=WxName.SNS_WINDOW_TITLE)
        if sns_by_name and exists_with_timeout(sns_by_name, 0.5):
            return sns_by_name
    except Exception:
        pass
        
    return None

def open_moments_window(driver_obj):
    """打开朋友圈"""
    if not driver_obj.is_connected():
        return None

    existing_win = wait_moments_window(timeout=1.0)
    if existing_win:
        logger.info("[朋友圈] 检测到朋友圈窗口已存在，直接复用")
        return existing_win

    try:
        driver_obj.SwitchToThisWindow()
        random_delay(0.2, 0.4)
        
        nav_moments = find_sidebar_moments_nav(driver_obj)
        
        if not nav_moments:
            import win32gui as _w32
            import win32con
            if driver_obj.hwnd and _w32.IsWindow(driver_obj.hwnd):
                if _w32.IsIconic(driver_obj.hwnd):
                    _w32.ShowWindow(driver_obj.hwnd, win32con.SW_RESTORE)
                    random_delay(0.3, 0.5)
                
                left, top, right, bottom = _w32.GetWindowRect(driver_obj.hwnd)
                width = right - left
                height = bottom - top
                
                if height < 850:
                    logger.info(f"[朋友圈] 检测到侧栏按钮隐藏，执行窗口温和拉伸调高 (当前高度: {height}px -> 目标: 850px)")
                    _w32.SetWindowPos(driver_obj.hwnd, win32con.HWND_TOP, left, top, width, 850, win32con.SWP_NOMOVE | win32con.SWP_NOACTIVATE)
                    random_delay(0.5, 0.8)
                    
                    nav_moments = find_sidebar_moments_nav(driver_obj)
        if nav_moments and exists_with_timeout(nav_moments, 1):
            try_click(nav_moments, max_retries=2, delay=0.3)
            random_delay(1.2, 1.8)
            sns_win = wait_moments_window(timeout=5.0)
            if sns_win:
                return sns_win
            
            logger.error("[朋友圈] try_click 触发失败，尝试第二重物理 Click(simulateMove=True) 兜底")
            try:
                nav_moments.Click(simulateMove=True)
            except Exception as click_e:
                logger.error(f"[朋友圈] 物理 Click 异常: {click_e}")
            random_delay(1.2, 1.8)
            sns_win = wait_moments_window(timeout=4.0)
            if sns_win:
                return sns_win
                
            logger.info("[朋友圈] 侧栏「朋友圈」已点但未检出窗口，尝试旧版「发现」流程…")

        discover_btn = driver_obj.root.ButtonControl(Name=WxName.DISCOVERY_NAV)
        if discover_btn and exists_with_timeout(discover_btn, 1):
            try_click(discover_btn, max_retries=2, delay=0.3)
            random_delay(0.5, 0.8)

        sns_cell = None
        try:
            sns_cell = driver_obj.root.ButtonControl(
                Name="朋友圈", ClassName=WxClass.DISCOVER_CELL)
        except Exception:
            pass

        if not sns_cell or not exists_with_timeout(sns_cell, 1):
            sns_cell = driver_obj._find_child(
                driver_obj.root, name="朋友圈",
                class_name=WxClass.DISCOVER_CELL, depth=10)

        if sns_cell:
            try_click(sns_cell, max_retries=2, delay=0.3)
            random_delay(1.5, 2.0)
        else:
            logger.error("未找到朋友圈入口（侧栏与发现页均未匹配，请检查微信版本与登录状态）")
            return None

        sns_win = wait_moments_window(timeout=5.0)
        if sns_win:
            return sns_win

        logger.error("未找到打开的朋友圈窗口")
        return None

    except Exception as e:
        logger.error(f"打开朋友圈失败: {e}")
        return None

def close_moments(driver_obj, moment_window):
    """关闭朋友圈窗口"""
    try:
        import win32gui as _w32
        uia.SendKeys('{Esc}')
        random_delay(0.3, 0.5)
        
        hwnd = _w32.FindWindow(None, '朋友圈')
        if hwnd and _w32.IsWindow(hwnd):
            _w32.PostMessage(hwnd, 0x0010, 0, 0)
            random_delay(0.3, 0.5)
        
        try:
            from src.uia.privacy_shield import get_privacy_shield
            get_privacy_shield().force_sync()
        except Exception:
            pass

        logger.info("[朋友圈] 窗口已关闭")
    except Exception as e:
        logger.warning(f"[朋友圈] 关闭窗口异常: {e}")

def ensure_moments_foreground():
    """将朋友圈窗口置顶置于前台"""
    import win32gui as _w32
    import win32con
    hwnd = _w32.FindWindow(None, '朋友圈')
    if hwnd and _w32.IsWindow(hwnd):
        if _w32.IsIconic(hwnd):
            _w32.ShowWindow(hwnd, win32con.SW_RESTORE)
        try:
            from src.uia.retry.window_ops import force_foreground
            force_foreground(hwnd)
            logger.info("[朋友圈] 置顶置于前台成功")
        except Exception as e:
            logger.warning(f"[朋友圈] 置顶前台尝试失败: {e}")
