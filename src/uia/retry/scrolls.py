"""物理滚动与模拟真人滑动能力（拆分自原 clicks.py 以对齐 300 行限额规范）"""

import time
import random

def human_scroll(
    element,
    min_times: int = 3,
    max_times: int = 6,
    min_delay: float = 0.6,
    max_delay: float = 1.8,
) -> bool:
    """人类滑动模拟（随机落点 + 随机幅度 + 随机停留）。

    与 physical_click 对齐：
    - 操作前等待用户停止鼠标/键盘活动（3 秒冷却）
    - 滚动完成后将鼠标光标还原到操作前的位置
    """
    from src.utils.user_activity import is_user_active
    from src.utils.stop_signal import stop_signal
    while is_user_active(cooldown_ms=3000):
        if stop_signal.is_stopped:
            return False
        time.sleep(0.2)

    try:
        import win32api
        from .clicks import random_delay

        rect = element.BoundingRectangle
        if not rect:
            return False
        rw = rect.right - rect.left
        rh = rect.bottom - rect.top
        if rw <= 0 or rh <= 0:
            return False

        safe_margin_x = max(10, min(100, rw // 5))
        safe_margin_y = max(10, min(100, rh // 5))
        if rw <= safe_margin_x * 2 or rh <= safe_margin_y * 2:
            x = (rect.left + rect.right) // 2
            y = (rect.top + rect.bottom) // 2
        else:
            x = random.randint(rect.left + safe_margin_x, rect.right - safe_margin_x)
            y = random.randint(rect.top + safe_margin_y, rect.bottom - safe_margin_y)

        # 保存原鼠标位置，操作完成后还原
        old_pos = win32api.GetCursorPos()
        try:
            win32api.SetCursorPos((x, y))
            time.sleep(random.uniform(0.05, 0.15))
            times = random.randint(min_times, max_times)
            element.WheelDown(wheelTimes=times)
            random_delay(min_delay, max_delay)
        finally:
            try:
                win32api.SetCursorPos(old_pos)
            except Exception:
                pass
        return True
    except Exception as e:
        print(f"[防风控] 随机极坐标滑动执行失败: {e}")
        return False
