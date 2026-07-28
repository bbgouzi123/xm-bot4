"""点击、滚动与基础重试能力。

所有物理鼠标操作（mouse_event）均自动穿透隐私保护遮罩，
调用方无需关心 bypass_shield 的存在。
"""

import contextlib
import ctypes
import random
import threading
import time
from src.utils.bezier import calculate_bezier_curve


# ==================== 隐私遮罩穿透 ====================

def _get_dpi_scale() -> float:
    """获取系统 DPI 缩放比例（安全独立实现）"""
    try:
        import ctypes
        hdc = ctypes.windll.user32.GetDC(0)
        log_x = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX = 88
        ctypes.windll.user32.ReleaseDC(0, hdc)
        return log_x / 96.0
    except Exception:
        return 1.0


def _get_shield_ctx():
    """获取隐私遮罩穿透上下文管理器。

    遮罩未启动或模块不存在时，安全地返回空上下文（零开销）。
    所有物理点击函数内部调用此方法，调用方无需感知。
    """
    try:
        from src.uia.privacy_shield import get_privacy_shield
        return get_privacy_shield().bypass_shield()
    except Exception:
        return contextlib.nullcontext()


def _get_shield_hide_ctx():
    """获取隐私遮罩隐藏上下文管理器。

    用于需要在遮罩下方弹出系统对话框（如“打开文件”对话框）的场景下，
    临时隐藏遮罩，完成操作后重新显示遮罩。
    """
    try:
        from src.uia.privacy_shield import get_privacy_shield
        return get_privacy_shield().bypass_shield(hide=True)
    except Exception:
        return contextlib.nullcontext()


# ==================== 物理点击原语（自动穿透遮罩） ====================

from .double_click_guard import guard_physical_click_frequency, guard_coordinate_click_frequency

def _trigger_ripple(x: int, y: int, color: int):
    """安全地在指定物理坐标触发操作波纹指示"""
    try:
        from src.utils.config_cache import config_cache
        cfg = config_cache.get("global_api_config") or {}
        if cfg.get("operation_ripple_enabled", True):
            from src.uia.uia_utils import UiaUtils
            UiaUtils.draw_click_ripple(x, y, color=color)
    except Exception:
        pass

_last_global_physical_click_time = 0.0
_global_click_lock = threading.Lock()

def physical_click(x: int, y: int, settle: float = 0.05, restore_cursor: bool = True, force: bool = False):
    """物理左键单击（自动穿透隐私保护遮罩，带贝塞尔平滑轨迹、防风控生理微颤与随机坐标微扰动）。"""
    global _last_global_physical_click_time

    from src.uia.input_guard import uia_lock
    uia_lock.check_interrupt()

    # 全局坐标点击频率控制，彻底防止短时间内对同一物理坐标的快速双击
    guard_coordinate_click_frequency(x, y)

    # 🌟 物理级防双击机制安全拦截 (全局限频，防止任何上层逻辑导致的 1.0s 内连续物理点击)
    with _global_click_lock:
        now = time.time()
        diff = now - _last_global_physical_click_time
        if diff < 1.0:
            wait_time = 1.0 - diff
            import logging
            logging.getLogger("WeChatDriver.Clicks").warning(f"[UIA] 监测到极速连续物理点击（间隔仅 {diff:.3f}s），底层强制休眠等待 {wait_time:.3f}s 以防止误触发双击")
            time.sleep(wait_time)
        _last_global_physical_click_time = time.time()

    if not force:
        from src.utils.user_activity import is_user_active
        from src.utils.stop_signal import stop_signal
        wait_start = time.time()
        while is_user_active(cooldown_ms=3000):
            uia_lock.check_interrupt()
            if time.time() - wait_start > 3.0:
                raise RuntimeError("避让用户操作超时（用户持续活跃中，主动释放 UIA 线程）")
            time.sleep(0.2)

    import win32api, win32con
    import random
    
    # 1. 坐标微扰动：避开完美的几何中心点，在周围 2 像素内随机游走
    x_perturbed = x + random.randint(-2, 2)
    y_perturbed = y + random.randint(-2, 2)

    old = win32api.GetCursorPos() if restore_cursor else None
    with _get_shield_ctx():
        # 2. 模拟移入扰动坐标：加入贝塞尔曲线平滑移动模拟真人轨迹
        try:
            curr_x, curr_y = win32api.GetCursorPos()
            distance = ((curr_x - x_perturbed) ** 2 + (curr_y - y_perturbed) ** 2) ** 0.5
            if distance > 15:
                steps = min(25, max(10, int(distance / 25)))
                path = calculate_bezier_curve(curr_x, curr_y, x_perturbed, y_perturbed, steps)
                for px, py in path:
                    win32api.SetCursorPos((px, py))
                    time.sleep(random.uniform(0.004, 0.010))
        except Exception as _me:
            pass
            
        win32api.SetCursorPos((int(x_perturbed), int(y_perturbed)))
        time.sleep(settle)
        
        # 3. 模拟生理微颤：产生 1 像素的极微移动并微休眠
        tremor_x = x_perturbed + random.choice([-1, 1])
        tremor_y = y_perturbed + random.choice([-1, 1])
        win32api.SetCursorPos((int(tremor_x), int(tremor_y)))
        time.sleep(0.03)
        win32api.SetCursorPos((int(x_perturbed), int(y_perturbed)))
        time.sleep(0.02)
        
        # 4. 物理左键点击
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(random.uniform(0.06, 0.10))   # 模拟物理左键按下持续开销
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        
        # 触发波纹指示（鼠标安全释放后触发，彻底杜绝输入捕获期间的多线程消息泵死锁）
        _trigger_ripple(int(x_perturbed), int(y_perturbed), 0xFF9900)  # BGR 格式：天蓝色
        
    # 等待微信处理点击事件后再移走光标
    time.sleep(0.15)
    if old:
        try:
            win32api.SetCursorPos(old)
        except Exception:
            pass


def physical_double_click(x: int, y: int, settle: float = 0.05, restore_cursor: bool = True, force: bool = False):
    """物理双击（自动穿透隐私保护遮罩，包含防风控生理微颤、随机坐标微扰动和真人双击间隔时间）。"""
    from src.uia.input_guard import uia_lock
    uia_lock.check_interrupt()

    if not force:
        from src.utils.user_activity import is_user_active
        from src.utils.stop_signal import stop_signal
        wait_start = time.time()
        while is_user_active(cooldown_ms=3000):
            uia_lock.check_interrupt()
            if time.time() - wait_start > 10.0:
                raise RuntimeError("避让用户操作超时（用户持续活跃中）")
            time.sleep(0.2)

    import win32api, win32con
    import random
    
    # 1. 坐标微扰动：避开完美的中心点，在周围 2 像素内随机游走
    x_perturbed = x + random.randint(-2, 2)
    y_perturbed = y + random.randint(-2, 2)

    old = win32api.GetCursorPos() if restore_cursor else None
    with _get_shield_ctx():
        # 2. 模拟移入坐标
        win32api.SetCursorPos((int(x_perturbed), int(y_perturbed)))
        time.sleep(settle)
        
        # 3. 模拟生理微颤：极微小的抖动，符合真人生理学特征
        tremor_x = x_perturbed + random.choice([-1, 1])
        tremor_y = y_perturbed + random.choice([-1, 1])
        win32api.SetCursorPos((int(tremor_x), int(tremor_y)))
        time.sleep(0.03)
        win32api.SetCursorPos((int(x_perturbed), int(y_perturbed)))
        time.sleep(0.02)
        
        # 4. 第一次点击按下与弹起，模拟物理按键时延
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(random.uniform(0.05, 0.08))
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        
        # 5. 模拟人手双击的生理间隔 (80ms - 130ms)，防止硬编码的超短延时被微信判定为机器检测
        time.sleep(random.uniform(0.08, 0.13))
        
        # 6. 第二次点击按下与弹起
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(random.uniform(0.05, 0.08))
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        
        # 触发波纹指示（鼠标安全释放后触发，彻底杜绝输入捕获期间的多线程消息泵死锁）
        _trigger_ripple(int(x_perturbed), int(y_perturbed), 0x0000FF)  # BGR 格式：红色
        
    time.sleep(0.15)
    if old:
        try:
            win32api.SetCursorPos(old)
        except Exception:
            pass


def physical_right_click(x: int, y: int, settle: float = 0.06, restore_cursor: bool = True):
    """物理右键单击（自动穿透隐私保护遮罩，带防风控生理微颤与随机坐标微扰动）。"""
    from src.uia.input_guard import uia_lock
    uia_lock.check_interrupt()

    from src.utils.user_activity import is_user_active
    from src.utils.stop_signal import stop_signal
    wait_start = time.time()
    while is_user_active(cooldown_ms=3000):
        uia_lock.check_interrupt()
        if time.time() - wait_start > 10.0:
            raise RuntimeError("避让用户操作超时（用户持续活跃中）")
        time.sleep(0.2)
    
    import win32api, win32con
    import random
    
    x_perturbed = x + random.randint(-2, 2)
    y_perturbed = y + random.randint(-2, 2)

    old = win32api.GetCursorPos() if restore_cursor else None
    with _get_shield_ctx():
        # 模拟移入扰动坐标
        win32api.SetCursorPos((int(x_perturbed), int(y_perturbed)))
        time.sleep(settle)
        
        # 模拟生理微颤
        tremor_x = x_perturbed + random.choice([-1, 1])
        tremor_y = y_perturbed + random.choice([-1, 1])
        win32api.SetCursorPos((int(tremor_x), int(tremor_y)))
        time.sleep(0.03)
        win32api.SetCursorPos((int(x_perturbed), int(y_perturbed)))
        time.sleep(0.02)
        
        # 物理右键点击
        win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
        time.sleep(0.07)
        win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
        
        # 触发波纹指示（鼠标安全释放后触发，彻底杜绝输入捕获期间的多线程消息泵死锁）
        _trigger_ripple(int(x_perturbed), int(y_perturbed), 0x00FFFF)  # BGR 格式：黄色
        
    time.sleep(0.12)
    if old:
        try:
            win32api.SetCursorPos(old)
        except Exception:
            pass


def physical_long_press(x: int, y: int, duration: float = 2.0, settle: float = 0.1, restore_cursor: bool = True):
    """物理长按（自动穿透隐私保护遮罩）。"""
    from src.utils.user_activity import is_user_active
    from src.utils.stop_signal import stop_signal
    from src.uia.input_guard import uia_lock
    wait_start = time.time()
    while is_user_active(cooldown_ms=3000):
        uia_lock.check_interrupt()
        if time.time() - wait_start > 10.0:
            raise RuntimeError("避让用户操作超时（用户持续活跃中）")
        time.sleep(0.2)

    import win32api, win32con
    old = win32api.GetCursorPos() if restore_cursor else None
    with _get_shield_ctx():
        win32api.SetCursorPos((int(x), int(y)))
        time.sleep(settle)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(duration)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    if old:
        try:
            win32api.SetCursorPos(old)
        except Exception:
            pass


# ==================== UIA 控件点击 ====================

def random_delay(min_s: float = 0.2, max_s: float = 0.5):
    """随机延迟，模拟人类操作。"""
    from src.uia.input_guard import uia_lock
    uia_lock.check_interrupt()
    time.sleep(random.uniform(min_s, max_s))


def try_click(element, max_retries: int = 3, delay: float = 0.3) -> bool:
    """安全点击，带重试（优先 SelectionItem/Invoke/LegacyIAccessible 防止抢标）。"""
    from src.uia.input_guard import uia_lock

    for i in range(max_retries):
        uia_lock.check_interrupt()
        try:
            if element and element.Exists(1):
                # 每次开始尝试点击时，在最前置拦截并发与极速跨调用连续点击，杜绝双击
                guard_physical_click_frequency(element)

                class_name = getattr(element, "ClassName", "") or ""
                is_custom_qt = class_name.startswith("mmui::")
                control_type = getattr(element, "ControlTypeName", "") or ""

                # 0. 优先尝试 SelectionItemPattern.Select（最适合列表项切换，免去鼠标移动和默认双击行为，绝对安全）
                # 💡 【Qt5 微信加固】微信 mmui:: 控件的 SelectionItem.Select 仅高亮聚焦，不触发打开会话动作，故必须跳过，走物理点击
                # 💡 MenuItem 控件调用 Select() 仅能实现高亮，不触发点击动作，因此菜单项也必须跳过
                if not is_custom_qt and control_type != "MenuItemControl":
                    try:
                        select_item = element.GetSelectionItemPattern()
                        if select_item:
                            select_item.Select()
                            time.sleep(delay)
                            return True
                    except Exception:
                        pass

                # 1. 优先尝试 InvokePattern
                invoke = None if is_custom_qt else element.GetInvokePattern()
                if invoke:
                    invoke.Invoke()
                    time.sleep(delay)
                    return True
                
                # 2. 尝试 LegacyIAccessiblePattern.DoDefaultAction (后台点击，免移鼠标)
                # 💡 【微信 4.x 加固】在微信的 Qt5 渲染下，对 ListItem 等控件调用 DoDefaultAction 
                # 会被微信默认判定为“无障碍双击”从而导致聊天窗口单独弹出！并且因为是后台 UIA 静默操作，它没有鼠标轨迹也无波纹。
                # 因此，我们只有在控件类型明确为 ButtonControl 时才使用它，其它类型（如列表项、面板等）一律跳过，走高精物理点击兜底。
                if not is_custom_qt and control_type == "ButtonControl":
                    try:
                        legacy = element.GetLegacyIAccessiblePattern()
                        if legacy:
                            legacy.DoDefaultAction()
                            time.sleep(delay)
                            return True
                    except Exception:
                        pass

                # 3. 降级为物理点击 (使用我们最稳定的 physical_click 穿透防双击)
                guard_physical_click_frequency(element)
                rect = element.BoundingRectangle
                if rect:
                    cx = (rect.left + rect.right) // 2
                    cy = (rect.top + rect.bottom) // 2
                    physical_click(cx, cy, restore_cursor=True)
                else:
                    with _get_shield_ctx():
                        element.Click(simulateMove=False)
                time.sleep(delay)
                return True
        except Exception:
            if i < max_retries - 1:
                time.sleep(0.5)
    return False


# 兼容旧版/反编译模块中的命名（如 monitor.moment_post）
try_click_element = try_click


# 导入被移动出去的辅助函数以保持向后兼容性
from .clicks_helper import (
    try_right_click,
    exists_with_timeout,
    smooth_click_at,
    click_at_absolute
)
