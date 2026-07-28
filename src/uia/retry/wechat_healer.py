"""
微信置顶唤醒与白屏自动刷新修复模块
从 window_ops 剥离以对齐单文件 300 行质量红线限制
"""
import ctypes
import logging
import time
import subprocess
import threading
from typing import Optional
import win32gui
import win32con

from src.utils.stop_signal import stop_signal
from src.utils.user_activity import is_user_active
from .tray import click_wechat_tray_icon
from .taskbar import click_wechat_taskbar_button

logger = logging.getLogger(__name__)
_grab_lock = threading.Lock()

def wakeup_wechat_via_process(hwnd: int) -> bool:
    """通过再次运行 WeChat.exe 进程唤醒微信，微信单实例机制会自动置顶并解冻渲染管线，解决白屏问题"""
    try:
        import win32process
        import psutil
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid:
            process = psutil.Process(pid)
            exe_path = process.exe()
            if exe_path:
                logger.info(f"[置前] 发现微信运行路径: {exe_path}，尝试二次启动以安全激活旧实例...")
                subprocess.Popen([exe_path], creationflags=subprocess.CREATE_NO_WINDOW)
                return True
    except Exception as e:
        logger.debug(f"[置前] 启动 WeChat.exe 激活实例异常: {e}")
    return False

def is_window_white_screen(hwnd: int) -> bool:
    """检测窗口是否处于白屏或冻结纯色状态（合并 UIA 树崩溃检测与屏幕像素采样检测）"""
    # ── 1. 优先使用 UIA 树崩溃检测：当主窗口存在却无任何子节点时，可 100% 断定 UIA 引擎已崩溃/白屏 ──
    try:
        from src.uia.modules.core.driver_registry import get_primary_driver
        driver = get_primary_driver()
        if driver and getattr(driver, "hwnd", 0) == hwnd and driver.root:
            from src.utils.safe_uia import safe_get_children, safe_exists
            if safe_exists(driver.root, timeout=0.5):
                children = safe_get_children(driver.root)
                if not children:
                    logger.warning(f"[白屏检测] ⚠️ 检测到微信主窗口 UIA 树无子节点 (Children=None)，判定为 UIA 引擎崩溃/白屏状态 (hwnd={hwnd})")
                    return True
    except Exception as e_uia:
        logger.debug(f"[白屏检测] 校验 UIA 树崩溃异常: {e_uia}")

    # ── 2. 像素采样检测白屏（兜底逻辑） ──
    try:
        from PIL import ImageGrab, ImageStat
        rect = win32gui.GetWindowRect(hwnd)
        if rect:
            x, y, r, b = rect
            w, h = r - x, b - y
            if w > 100 and h > 100:
                # 截取微信整个窗口的像素，保留 10 像素的微缩边框以防止 Windows 阴影和外框噪点影响
                # 必须包含最左侧的暗色导航栏。即使右侧聊天区全白，极值差也很大，避免“假死白屏”误判
                px = x + 10
                py = y + 10
                pr = r - 10
                pb = b - 10
                bbox = (px, py, pr, pb)
                
                with _grab_lock:
                    img = ImageGrab.grab(bbox=bbox)
                    
                if img:
                    extrema = img.convert("RGB").getextrema()
                    r_diff = extrema[0][1] - extrema[0][0]
                    g_diff = extrema[1][1] - extrema[1][0]
                    b_diff = extrema[2][1] - extrema[2][0]
                    
                    # 如果这片很大的区域中，R, G, B 的最大值与最小值差异均非常小（说明全屏为同一种纯色）
                    if r_diff <= 3 and g_diff <= 3 and b_diff <= 3:
                        r_val = extrema[0][0]
                        g_val = extrema[1][0]
                        b_val = extrema[2][0]
                        # 判定是否为白色白屏（微信普通白屏）或纯黑色白屏（暗黑模式冻结）
                        if (r_val > 245 and g_val > 245 and b_val > 245) or (r_val < 10 and g_val < 10 and b_val < 10):
                            return True
    except Exception as e:
        logger.debug(f"[白屏检测] 检测异常: {e}")
    return False

def fix_white_screen_after_show(hwnd: int):
    """ShowWindow 唤回后执行白屏修复组合拳"""
    user32 = ctypes.windll.user32
    logger.info(f"[白屏修复] 对 hwnd={hwnd} 执行窗口自绘重绘刷新...")
    try:
        x, y, r, b = win32gui.GetWindowRect(hwnd)
        w, h = r - x, b - y
        win32gui.MoveWindow(hwnd, x, y, w + 1, h + 1, True)
        time.sleep(0.05)
        win32gui.MoveWindow(hwnd, x, y, w, h, True)
        time.sleep(0.05)
    except Exception:
        pass
    try:
        user32.InvalidateRect(hwnd, None, True)
        user32.UpdateWindow(hwnd)
    except Exception:
        pass
    try:
        user32.RedrawWindow(hwnd, None, None, 0x0001 | 0x0100 | 0x0080 | 0x0400)
    except Exception:
        pass

def ensure_wechat_foreground(hwnd: int, max_wait: float = 3.0) -> bool:
    """确保微信窗口可见且在前台（支持白屏检测自愈与进程安全激活）"""
    user32 = ctypes.windll.user32
    wait_start = time.time()
    
    # ── 用户活跃避让机制 ──
    while is_user_active(cooldown_ms=3000):
        if stop_signal.is_stopped:
            return False
        if time.time() - wait_start > 3.0:
            logger.debug("[避让] 微信窗口置顶：用户持续活跃，跳过本次前台置顶")
            if hwnd and user32.GetForegroundWindow() == hwnd:
                return True
            return False
        time.sleep(0.2)

    def _sleep_or_interrupt(duration: float) -> bool:
        start = time.time()
        while time.time() - start < duration:
            if stop_signal.is_stopped:
                return False
            time.sleep(0.05)
        return True

    if not hwnd or not win32gui.IsWindow(hwnd):
        return False

    # ── 1. 已经是前台窗口，直接检测白屏并返回 ──
    if user32.GetForegroundWindow() == hwnd:
        _heal_white_screen_if_needed(hwnd)
        return True

    from .window_ops import force_foreground

    # ── 2. 区分可见性 ──
    is_visible = win32gui.IsWindowVisible(hwnd)
    is_iconic = user32.IsIconic(hwnd)

    # ── 3. 窗口可见但未置顶，优先用 API 强力置顶 ──
    if is_visible and not is_iconic:
        logger.info("[置前] 微信窗口当前可见但未处于前台，优先尝试使用 API 强力置顶...")
        if force_foreground(hwnd):
            logger.info("[置前] [OK] 微信窗口已通过 API 成功置顶")
            _heal_white_screen_if_needed(hwnd)
            return True

    if stop_signal.is_stopped:
        return False

    # ── 4. 窗口不可见或最小化，需要唤回可见 ──
    if not is_visible or is_iconic:
        logger.info(f"[置前] 微信窗口不可见或已最小化，正在唤回: hwnd={hwnd}")
        
        # A. 优先尝试进程双击激活（防白屏最安全姿势）
        activated = wakeup_wechat_via_process(hwnd)
        if activated:
            _sleep_or_interrupt(0.8)
            is_visible = win32gui.IsWindowVisible(hwnd)
            is_iconic = user32.IsIconic(hwnd)

        # B. 进程激活未成功，回退到托盘图标模拟点击
        if not is_visible or is_iconic:
            tray_ok = click_wechat_tray_icon()
            if stop_signal.is_stopped:
                return False
            if tray_ok:
                _sleep_or_interrupt(0.8)
                if win32gui.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
                    logger.info("[置前] [OK] 微信窗口已通过托盘唤回")
                else:
                    user32.ShowWindow(hwnd, win32con.SW_SHOW)
                    _sleep_or_interrupt(0.2)
                    user32.ShowWindow(hwnd, win32con.SW_RESTORE)
                    _sleep_or_interrupt(0.5)
            else:
                user32.ShowWindow(hwnd, win32con.SW_SHOW)
                _sleep_or_interrupt(0.2)
                user32.ShowWindow(hwnd, win32con.SW_RESTORE)
                _sleep_or_interrupt(0.5)

            # 等待可见性刷新稳定
            waited = 0.0
            found_visible = False
            while waited < max_wait:
                if stop_signal.is_stopped:
                    return False
                if win32gui.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
                    found_visible = True
                    break
                _sleep_or_interrupt(0.2)
                waited += 0.2

            if not found_visible:
                logger.warning("[置前] 微信窗口唤回超时")
                return False
            
            if not tray_ok and not activated:
                fix_white_screen_after_show(hwnd)

    if stop_signal.is_stopped:
        return False

    # ── 5. 如果窗口已可见但依然未置顶，尝试物理点击置顶 ──
    if user32.GetForegroundWindow() == hwnd:
        # 已经是前台（可能步骤3/4已异步完成），跳过昂贵的托盘/任务栏 UIA 遍历
        _heal_white_screen_if_needed(hwnd)
        return True

    tray_ok = click_wechat_tray_icon()
    if stop_signal.is_stopped:
        return False
    if tray_ok:
        _sleep_or_interrupt(0.8)
        if user32.GetForegroundWindow() == hwnd:
            logger.info("[置前] [OK] 微信已成功通过点击托盘置顶")
            _heal_white_screen_if_needed(hwnd)
            return True

    # 只有在托盘方式未成功且微信依然不是前台时，才尝试任务栏点击
    if user32.GetForegroundWindow() != hwnd:
        taskbar_ok = click_wechat_taskbar_button(hwnd=hwnd)
        if taskbar_ok:
            _sleep_or_interrupt(0.6)
            if user32.GetForegroundWindow() == hwnd:
                logger.info("[置前] [OK] 微信已成功通过点击任务栏置顶")
                _heal_white_screen_if_needed(hwnd)
                return True

    # ── 6. 最终 API 强力置前 ──
    if user32.GetForegroundWindow() != hwnd:
        result = force_foreground(hwnd)
        if not result:
            try:
                user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0002 | 0x0001 | 0x0040)
                user32.ShowWindow(hwnd, win32con.SW_SHOW)
                user32.SetForegroundWindow(hwnd)
                _sleep_or_interrupt(0.1)
                user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0002 | 0x0001 | 0x0040)
                result = user32.GetForegroundWindow() == hwnd
            except Exception:
                pass
    else:
        result = True

    if result:
        _heal_white_screen_if_needed(hwnd)
        _sleep_or_interrupt(0.2)
    return result

def _heal_white_screen_if_needed(hwnd: int):
    """如果检测到白屏，执行自愈机制。
    
    使用 Ctrl+Alt+W 快捷键进行展示/隐藏切换以触发重绘；
    若切换后窗口被隐藏了，则再次触发一次以保证窗口在前台可见。
    """
    try:
        if is_window_white_screen(hwnd):
            logger.warning("[置前] ⚠️ 检测到微信窗口目前处于白屏冻结状态，触发快捷键 Ctrl+Alt+W 自愈刷新...")
            
            def _send_ctrl_alt_w():
                user32 = ctypes.WinDLL("user32", use_last_error=True)
                user32.keybd_event(17, 0, 0, 0)  # Ctrl
                user32.keybd_event(18, 0, 0, 0)  # Alt
                user32.keybd_event(87, 0, 0, 0)  # W
                time.sleep(0.05)
                user32.keybd_event(87, 0, 2, 0)
                user32.keybd_event(18, 0, 2, 0)
                user32.keybd_event(17, 0, 2, 0)

            # 1. 发送第一下快捷键触发自绘/重绘
            _send_ctrl_alt_w()
            time.sleep(0.8)

            # 2. 检查当前窗口是否可见及最小化状态
            is_visible = win32gui.IsWindowVisible(hwnd)
            user32 = ctypes.windll.user32
            is_iconic = user32.IsIconic(hwnd)

            # 3. 如果不可见或被最小化了，再按一次唤出
            if not is_visible or is_iconic:
                logger.info("[置前] 白屏热键刷新后窗口处于隐藏或最小化状态，再次触发 Ctrl+Alt+W 唤起窗口...")
                _send_ctrl_alt_w()
                time.sleep(0.8)

            # 4. 终极兜底：如果两次快捷键后还是白屏，保留原有的点击托盘/任务栏逻辑
            if is_window_white_screen(hwnd):
                logger.warning("[置前] 快捷键自愈后仍呈白屏状态，触发点击托盘/任务栏终极兜底自愈...")
                click_wechat_tray_icon()
                time.sleep(0.6)
                if is_window_white_screen(hwnd):
                    click_wechat_taskbar_button(hwnd=hwnd)
                    time.sleep(0.6)
    except Exception as e:
        logger.debug(f"[置前] 白屏自动自愈出现异常: {e}")
