import time
import threading
import logging

logger = logging.getLogger("WeChatDriver.Clicks")

_global_click_lock = threading.Lock()
_global_click_timestamps = {}  # element_id -> timestamp

_global_coord_click_lock = threading.Lock()
_global_coord_click_timestamps = [] # 列表存 (x, y, timestamp)

def guard_physical_click_frequency(element):
    """全局物理点击频率保护：同一个元素点击间隔必须大于 1.0s，杜绝误触发系统双击"""
    try:
        if not element:
            return
        
        elem_id = None
        # 🌟 1. 优先提取会话项的纯文本昵称作为绝对身份锁（防止微信重绘列表导致 RuntimeId 变化逃逸）
        try:
            name = element.Name
            if name:
                from src.uia.session import clean_session_name, parse_session_name
                parsed = parse_session_name(name.strip())
                cleaned = clean_session_name(parsed.get("name", "") if parsed else name)
                if cleaned:
                    elem_id = f"session:{cleaned}"
        except Exception:
            pass

        # 🌟 2. 备用提取 RuntimeId
        if not elem_id:
            try:
                elem_id = element.GetRuntimeId()
            except Exception:
                pass
            
        cx, cy = None, None
        try:
            r = element.BoundingRectangle
            if r:
                cx = (r.left + r.right) // 2
                cy = (r.top + r.bottom) // 2
                if not elem_id:
                    elem_id = f"rect:{r.left},{r.top},{r.right},{r.bottom}"
        except Exception:
            pass

        # 3. 优先在坐标级做校验与时间同步
        if cx is not None and cy is not None:
            guard_coordinate_click_frequency(cx, cy)

        # 4. 然后在元素级做双重校验
        if elem_id:
            with _global_click_lock:
                now = time.time()
                last_time = _global_click_timestamps.get(elem_id, 0.0)
                diff = now - last_time
                
                # 仅放行极小微时间片内（如 20ms）的多线程重入碰撞，超过 20ms 且小于 1.0s 的全部强制拦截并拉开
                if diff < 0.02:
                    return

                if diff < 1.0:
                    wait_time = 1.0 - diff
                    logger.warning(
                        f"[防双击] 会话/元素 '{elem_id}' 逻辑/物理点击过于频繁(间隔仅 {diff:.3f}s)，强制延迟等待 {wait_time:.3f}s 以彻底杜绝双击弹出窗口"
                    )
                    time.sleep(wait_time)
                    now = time.time()
                _global_click_timestamps[elem_id] = now
                
                if len(_global_click_timestamps) > 100:
                    expired = [k for k, t in _global_click_timestamps.items() if now - t > 5.0]
                    for k in expired:
                        _global_click_timestamps.pop(k, None)
    except Exception:
        pass

def guard_coordinate_click_frequency(x: int, y: int):
    """防止同一区域被频繁物理点击，彻底避免被操作系统识别为双击"""
    try:
        global _global_coord_click_timestamps
        now = time.time()
        with _global_coord_click_lock:
            _global_coord_click_timestamps = [item for item in _global_coord_click_timestamps if now - item[2] < 3.0]
            
            for old_x, old_y, last_time in _global_coord_click_timestamps:
                dist = ((old_x - x) ** 2 + (old_y - y) ** 2) ** 0.5
                # 将微偏移拦截距离从 5 像素扩展到 30 像素，防御由于滚动/红点插入引起的微位移导致的物理防线穿透
                if dist <= 30:
                    diff = now - last_time
                    
                    # 仅放行极小微时间片内（如 20ms）的多线程并发重入
                    if diff < 0.02:
                        return
                        
                    if diff < 1.0:
                        wait_time = 1.0 - diff
                        logger.warning(
                            f"[防双击] 坐标 ({x}, {y}) 与历史点击点 ({old_x}, {old_y}) 过于接近(距离 {dist:.1f}px，间隔仅 {diff:.3f}s)，强制延迟等待 {wait_time:.3f}s"
                        )
                        time.sleep(wait_time)
                        now = time.time()
                    break
            _global_coord_click_timestamps.append((x, y, now))
    except Exception:
        pass
