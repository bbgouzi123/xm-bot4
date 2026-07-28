"""
朋友圈条目解析与辅助工具模块

从 moment_interactor.py 拆分以满足 300 行代码上限：
- 条目解析器（对齐竞品 parse_moment_item_41x）
- 时间戳解析器（GAP 3: 超2天旧动态检测）
- 可见性检测
- 像素坐标点击互动区域
- 微信弹窗自动关闭（GAP 7）
"""
import logging
import random
import re
import time
import uiautomation as uia
import win32api
import win32con
import win32gui
from datetime import datetime, timedelta
from typing import Optional, Dict

from src.uia.retry import try_click, physical_click

logger = logging.getLogger(__name__)


# ==================== 朋友圈条目解析（对齐竞品） ====================

# 时间格式
_TIME_RE = (
    r'(刚刚|\d{1,2}分钟前|\d{1,2}小时前|\d{1,2}天前'
    r'|昨天\s*\d{1,2}:\d{2}|前天\s*\d{1,2}:\d{2}'
    r'|\d{1,2}月\d{1,2}日\s*\d{1,2}:\d{2}'
    r'|\d{4}年\d{1,2}月\d{1,2}日\s*\d{1,2}:\d{2})'
)

# 系统附加标记（从右侧剥离）
_SYSTEM_PATTERNS = [
    r'(包含\d+张图片)$', r'(\[图片\])$', r'(\[视频\])$',
    r'(视频号)$', r'(来自视频号)$', r'(分享链接)$',
    r'(分享视频)$', r'(分享图片)$', r'(直播中)$',
    r'( · .+)$', r'(位置)$', r'(IP属地 .+)$',
    r'(服务号)$', r'(公众号)$',
    r'(服务号\s*·\s*.+)$', r'(公众号\s*·\s*.+)$',
]


def parse_moment_item(name_str: str) -> Optional[Dict[str, str]]:
    """解析最新版微信朋友圈列表项的 Name 拼接字符串。
    对齐竞品 WeChatType.py L4938 _get_moment_content 实现。
    """
    if not name_str:
        return None
    s = name_str.strip()
    if not s:
        return None

    # 对齐竞品：首选冒号作为发布者和内容的分隔符
    parts = s.split(':', 1)
    if len(parts) < 2:
        parts = s.split('：', 1)  # 中文冒号也兼容下
    if len(parts) < 2:
        # 兼容旧版本使用空格分隔的后备方案
        idx = s.find(' ')
        if idx == -1:
            publisher = s
            content = ''
        else:
            publisher = s[:idx].strip()
            content = s[idx + 1:].strip()
    else:
        publisher = parts[0].strip()
        content = parts[1].strip()

    if not publisher:
        return None

    # 按行分割
    lines = [line.strip() for line in content.split('\n') if line.strip()]
    if not lines:
        return {'publisher': publisher, 'content': '', 'time_str': '', 'media_hint': ''}

    # 竞品时间正则表达式
    time_patterns = [
        r'^\d+分钟前$',
        r'^\d+小时前$',
        r'^\d+天前$',
        r'^刚刚$',
        r'^昨天$',
        r'^昨天\s*\d{1,2}:\d{1,2}$',
        r'^前天\s*\d{1,2}:\d{1,2}$',
        r'^\d{1,2}月\d{1,2}日\s*\d{1,2}:\d{1,2}$',
        r'^\d{4}年\d{1,2}月\d{1,2}日\s*\d{1,2}:\d{1,2}$'
    ]
    # 竞品系统辅助文字表达式
    system_patterns = [
        r'^包含\d+张图片$',
        r'^视频号$',
        r'^视频号直播,直播中$',
        r'^\s*视频\s*$',
        r'^分享图片$',
        r'^分享视频$',
        r'^分享链接$',
        r'^视频号\s*·\s*.*$',
        r'^.*\s*·\s*视频号$',
        r'^.*市\s*·\s*.*$',
        r'^.*省\s*·\s*.*$',
        r'^.*区\s*·\s*.*$'
    ]

    content_lines = []
    publish_time = ''
    found_time = False

    # 从后往前遍历每一行
    for line in reversed(lines):
        # 如果是系统标识，跳过它
        is_system = False
        for pattern in system_patterns:
            if re.match(pattern, line):
                is_system = True
                break
        if is_system:
            continue

        # 如果是时间行
        if not found_time:
            is_time = False
            for pattern in time_patterns:
                if re.match(pattern, line):
                    is_time = True
                    break
            if is_time:
                publish_time = line
                found_time = True
                continue

        # 否则，是真实的朋友圈文案内容，插入到前面
        content_lines.insert(0, line)

    real_content = '\n'.join(content_lines).strip()
    return {
        'publisher': publisher,
        'content': real_content,
        'time_str': publish_time,
        'media_hint': ''
    }


def parse_publish_timestamp(time_str: str) -> Optional[float]:
    """将朋友圈时间字符串解析为 UNIX 时间戳（GAP 3: 超2天旧动态检测用）。"""
    if not time_str:
        return None
    try:
        s = time_str.strip()
        now = time.time()
        if s == '刚刚':
            return now
        m = re.match(r'^(\d{1,2})分钟前$', s)
        if m:
            return now - int(m.group(1)) * 60
        m = re.match(r'^(\d{1,2})小时前$', s)
        if m:
            return now - int(m.group(1)) * 3600
        m = re.match(r'^(\d{1,2})天前$', s)
        if m:
            return now - int(m.group(1)) * 86400
        m = re.match(r'^(昨天|前天)\s*(\d{1,2}):(\d{2})$', s)
        if m:
            days = 1 if m.group(1) == '昨天' else 2
            dt = datetime.now() - timedelta(days=days)
            dt = dt.replace(hour=int(m.group(2)), minute=int(m.group(3)), second=0)
            return dt.timestamp()
        m = re.match(r'^(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})$', s)
        if m:
            year = int(m.group(1)) if m.group(1) else datetime.now().year
            dt = datetime(year, int(m.group(2)), int(m.group(3)),
                          int(m.group(4)), int(m.group(5)))
            if dt.timestamp() > now and not m.group(1):
                dt = dt.replace(year=year - 1)
            return dt.timestamp()
    except Exception:
        pass
    return None


# ==================== 可见性检测 ====================

def is_item_fully_visible(item, container) -> bool:
    """检测 item 底部约 50px 是否在容器可见范围内。"""
    try:
        ir = item.BoundingRectangle
        cr = container.BoundingRectangle
        strip_top = max(ir.top, ir.bottom - 50)
        inter_left = max(ir.left, cr.left)
        inter_top = max(strip_top, cr.top)
        inter_right = min(ir.right, cr.right)
        inter_bottom = min(ir.bottom, cr.bottom)
        return (inter_right - inter_left) > 15 and (inter_bottom - inter_top) > 40
    except Exception:
        return False


# ==================== 像素坐标点击互动按钮 ====================

def click_interaction_area(item) -> bool:
    """点击朋友圈列表项右下角区域以唤出"赞/评论"浮层。

    实测数据（2026-06-02 UIA 探测）：
        互动浮层触发区 ≈ item.right - 55, item.bottom - 22
        赞按钮坐标 (3183, 1115) → item.bottom ~ 1138 → dy ≈ 23
    """
    try:
        rect = item.BoundingRectangle
        if not rect or rect.right <= rect.left or rect.bottom <= rect.top:
            return False

        # 实测：dx=50~60, dy=18~25 命中互动图标区域
        dx = random.randint(50, 60)
        dy = random.randint(18, 25)

        x = rect.right - dx
        y = rect.bottom - dy
        logger.debug(f"[互动] 像素点击互动区: ({x},{y}) [dx={dx} dy={dy}]")
        physical_click(x, y, settle=random.uniform(0.1, 0.2))
        return True
    except Exception as e:
        logger.debug(f"[互动] 像素坐标点击互动区域失败: {e}")
        return False


# ==================== 朋友圈窗口最大化（对齐竞品） ====================

def maximize_moment_window(hwnd: int) -> bool:
    """将朋友圈窗口高度拉满到屏幕工作区（对齐竞品 position_wechat_window）。

    竞品核心逻辑：靠右、高度=工作区全高、宽度保持不变（最小680px）。
    """
    try:
        if not hwnd or not win32gui.IsWindow(hwnd):
            return False
        # 如果最小化了先恢复
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.1)
        # 获取工作区（排除任务栏）
        monitor = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
        info = win32api.GetMonitorInfo(monitor)
        work = info['Work']  # (left, top, right, bottom)
        work_h = work[3] - work[1]
        work_w = work[2] - work[0]
        # 保持当前宽度，但不小于 680
        rect = win32gui.GetWindowRect(hwnd)
        curr_w = rect[2] - rect[0]
        eff_w = max(curr_w, 680)
        new_w = min(eff_w, work_w)
        # 靠右对齐
        new_x = max(work[0], work[2] - new_w)
        new_y = work[1]
        win32gui.MoveWindow(hwnd, new_x, new_y, new_w, work_h, True)
        logger.info(f"[朋友圈] 窗口已最大化: ({new_x},{new_y}) {new_w}x{work_h}")
        return True
    except Exception as e:
        logger.debug(f"[朋友圈] 窗口最大化失败: {e}")
        return False


# ==================== 封面区域检测 ====================

def is_cover_item(item, list_ctrl) -> bool:
    """检测列表项是否为朋友圈顶部封面区域（非好友动态，需跳过）。

    核心判断标准（修复误判问题）：
    - 封面永远是列表中紧贴顶部的第一个大块条目
    - 条件 1: item 的 top 边缘在 list 的 top 上方或非常接近（差距 < 100px）
    - 条件 2: 高度 > 300px（封面含背景图+头像，通常 400px+）
    - 不再使用百分比判断，避免将有大图的正常动态误判为封面
    """
    try:
        ir = item.BoundingRectangle
        cr = list_ctrl.BoundingRectangle
        item_height = ir.bottom - ir.top
        # 封面必须紧贴列表顶部（item.top 在 list.top 附近或以上）
        top_gap = ir.top - cr.top
        if top_gap < 100 and item_height > 300:
            # 额外确认：封面区域通常不包含动态时间格式
            name = getattr(item, 'Name', '') or ''
            if not re.search(r'(分钟前|小时前|天前|刚刚|\d{1,2}月\d{1,2}日|昨天|前天)', name):
                return True
    except Exception:
        pass
    return False


# ==================== 微信弹窗自动关闭（GAP 7） ====================

def dismiss_popup() -> bool:
    """检测并关闭微信系统弹窗（"我知道了"、"确定" 等）。"""
    from src.utils.safe_uia import safe_exists
    try:
        pop = uia.WindowControl(Name='Weixin')
        if pop and safe_exists(pop, 0.5):
            for btn_name in ['我知道了', '确定', '知道了', 'OK']:
                btn = pop.ButtonControl(Name=btn_name)
                if btn and safe_exists(btn, 0.3):
                    try_click(btn, max_retries=2, delay=0.3)
                    time.sleep(0.3)
                    logger.info(f"[弹窗] 已自动关闭微信弹窗（按钮: {btn_name}）")
                    return True
    except Exception:
        pass
    return False


# ==================== 重新弹出互动浮层（对齐竞品 L2873） ====================

def reopen_interaction_popup(interaction_btn, sns_window) -> bool:
    """重新点击互动按钮（两个小点）以弹出赞/评论浮层。

    点赞后浮层会自动关闭，必须重新点击才能呼出评论入口。
    """
    from src.utils.safe_uia import safe_exists
    if interaction_btn is not None:
        if try_click(interaction_btn, max_retries=3, delay=0.3):
            logger.info("[互动] 重新点击互动按钮呼出浮层")
            return True
    # 兜底：在 sns_window 中查找评论按钮
    fallback_btn = sns_window.ButtonControl(Name='评论')
    if safe_exists(fallback_btn, 0.5):
        if try_click(fallback_btn, max_retries=3, delay=0.3):
            logger.info("[互动] 兜底点击评论按钮呼出浮层")
            return True
    logger.warning("[互动] 重新弹出浮层失败")
    return False


def find_toast_window(sns_window):
    """查找朋友圈赞/评论浮层。

    新版微信 (≥4.x): mmui::TimelineFloatMenu，顶层 WindowControl，需全局搜索。
    旧版微信 (竞品环境): SnsLikeToastWnd，PaneControl，从 sns_window 子树搜索。

    使用 safe_exists / safe_walk_control 防止 COM 断连导致进程崩溃。
    """
    import uiautomation as uia
    from src.utils.safe_uia import safe_exists, safe_walk_control
    # 优先：新版微信（实测 ClassName）
    try:
        toast = uia.WindowControl(ClassName='mmui::TimelineFloatMenu')
        if safe_exists(toast, 2.0):
            return toast
    except Exception:
        pass
    # 降级：旧版微信（竞品环境）
    try:
        toast = sns_window.PaneControl(ClassName='SnsLikeToastWnd')
        if safe_exists(toast, 1.0):
            return toast
    except Exception:
        pass
    # 兜底：遍历查找含"赞"按钮的 pane（使用安全遍历，防止 COM 崩溃）
    try:
        for p, _ in safe_walk_control(sns_window, max_depth=4):
            try:
                if safe_exists(p.ButtonControl(Name='赞'), 0.2):
                    return p
            except Exception:
                pass
    except Exception:
        pass
    return None

