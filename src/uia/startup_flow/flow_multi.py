"""
多微信实例启动识别流程（flow_multi.py）

场景：客户电脑上已提前登录好多个微信，程序启动时需要全部识别。

与 ensure_wechat_ready() 的区别：
  - ensure_wechat_ready()：单开场景，只找面积最大的一个主窗口
  - ensure_all_wechat_ready()：多开场景，逐一识别所有主界面微信，
    每个都确保 UIA 树可用，最终返回所有就绪的 hwnd 列表
"""

import time
import threading
import win32gui
from typing import List
from .utils import _log
from .state import _enum_wechat_windows, detect_wechat_state
from .toolbar import find_nav_toolbar
from .refresh import force_accessibility_refresh
from .narrator import start_narrator, stop_narrator
from .window_ops import force_focus_window, _ensure_window_on_screen


def ensure_all_wechat_ready() -> List[int]:
    """识别并激活系统中所有已登录的微信主界面实例。

    策略（大厂标准顺序）：
    1. 枚举所有满足尺寸条件的主界面窗口（可见 + ≥500×400）
    2. 按进程创建时间升序排列（保证第一个实例最稳定）
    3. 【关键】先做布局判断，窗口重叠或超出屏幕则立即平铺 → 等待渲染稳定
    4. 对每个窗口依次：唤醒 → 尝试 UIA 树 → 失败时强制刷新
    5. 用全局 Narrator（仅启动一次）兜底激活 Qt Accessibility
    6. 返回所有识别成功的 hwnd 列表

    Returns:
        [hwnd, hwnd, ...] 识别成功的主界面句柄列表（空列表表示失败）
    """
    state = detect_wechat_state()
    main_wins = state.get("main_windows", [])  # [(hwnd, w, h), ...]

    if not main_wins:
        _log("多开识别", "未发现已登录的微信主界面")
        return []

    _log("多开识别", f"发现 {len(main_wins)} 个已登录微信主界面，开始逐一识别...")

    # ── 按进程创建时间升序排列（老实例优先，更稳定）──────────────────────
    ordered_hwnds = _sort_hwnds_by_create_time([h for h, w, ht in main_wins])
    _log("多开识别", f"识别顺序: {ordered_hwnds}")

    # ── 【必须先于识别】诊断 + 布局检查 + 平铺 ──────────────────────────
    # 原因：识别时需要精确操作窗口 UI（点击导航栏/头像等），
    # 若窗口相互遮挡，物理点击会打到错误的窗口，导致 UIA 操作混乱。
    from .multi_open_diag import assess_multiopen, notify_multiopen_status
    assessment = assess_multiopen(len(ordered_hwnds))
    notify_multiopen_status(assessment)          # 异步推送，不阻塞后续流程

    # 根据判决决定平铺模式
    use_compact = assessment["verdict"] in ("compact", "warn")

    if _need_tile(ordered_hwnds):
        _log("多开识别",
             f"⚠ 检测到窗口布局不整（重叠或超出屏幕），"
             f"正在{'紧凑' if use_compact else '标准'}模式平铺排列...")
        try:
            from src.utils.window_utils import tile_all_wechat_windows
            tile_all_wechat_windows(ordered_hwnds, compact=use_compact)
            time.sleep(1.0)  # 等待 DWM 渲染稳定后再识别
            _log("多开识别", "✅ 布局平铺完成，开始识别")
        except Exception as _te:
            _log("多开识别", f"⚠ 平铺异常（继续识别）: {_te}")
    else:
        _log("多开识别", "✅ 窗口布局正常，直接开始识别")

    # ── 第一轮：不启动 Narrator，静默尝试 ───────────────────────────────
    first_pass_ok = []
    first_pass_fail = []

    for hwnd in ordered_hwnds:
        ok = _try_activate_single(hwnd, retry=1)
        if ok:
            first_pass_ok.append(hwnd)
            _log("多开识别", f"  ✓ hwnd={hwnd} 第一轮识别成功")
        else:
            first_pass_fail.append(hwnd)
            _log("多开识别", f"  ⚠ hwnd={hwnd} 第一轮未识别，加入待刷新队列")

    # ── 第二轮：对失败的开启 Narrator 后重试 ─────────────────────────────
    if first_pass_fail:
        _log("多开识别", f"开启讲述人，对 {len(first_pass_fail)} 个窗口进行二次刷新...")
        start_narrator()
        try:
            second_pass_ok = []
            for hwnd in first_pass_fail:
                force_accessibility_refresh(hwnd, escalate=True)
                time.sleep(0.5)
                ok = _try_activate_single(hwnd, retry=3)
                if ok:
                    second_pass_ok.append(hwnd)
                    _log("多开识别", f"  ✓ hwnd={hwnd} 第二轮（Narrator）识别成功")
                else:
                    _log("多开识别", f"  ❌ hwnd={hwnd} 第二轮仍失败，跳过")
            first_pass_ok.extend(second_pass_ok)
        finally:
            stop_narrator()

    total = len(first_pass_ok)
    _log("多开识别", f"识别完成: {total}/{len(ordered_hwnds)} 个微信实例就绪")
    return first_pass_ok


def _sort_hwnds_by_create_time(hwnds: List[int]) -> List[int]:
    """按进程创建时间升序排列，创建最早的排第一（最稳定）。"""
    import win32process
    import psutil

    def _create_time(h):
        try:
            _, pid = win32process.GetWindowThreadProcessId(h)
            return psutil.Process(pid).create_time()
        except Exception:
            return float("inf")

    return sorted(hwnds, key=_create_time)


def _rects_overlap(r1: tuple, r2: tuple) -> bool:
    """判断两个 (left, top, right, bottom) 矩形是否相交（重叠）。"""
    l1, t1, r1e, b1 = r1
    l2, t2, r2e, b2 = r2
    return not (r1e <= l2 or r2e <= l1 or b1 <= t2 or b2 <= t1)


def _need_tile(hwnds: List[int]) -> bool:
    """判断当前 hwnd 列表是否需要平铺。

    需要平铺的条件（满足其一即触发）：
    - 多窗口：任意两个窗口矩形存在重叠
    - 单窗口：窗口部分或全部超出所有显示器可见区域
    """
    if not hwnds:
        return False

    # 收集各窗口矩形
    rects = []
    for h in hwnds:
        try:
            r = win32gui.GetWindowRect(h)
            rects.append(r)  # (left, top, right, bottom)
        except Exception:
            rects.append(None)

    valid_rects = [(h, r) for h, r in zip(hwnds, rects) if r]

    if len(valid_rects) <= 1:
        # 单窗口：检测是否超出屏幕（win32api.GetSystemMetrics 0/1 只拿主屏，用 EnumDisplayMonitors 更准）
        if not valid_rects:
            return False
        _, r = valid_rects[0]
        try:
            from win32api import EnumDisplayMonitors
            all_areas = [m[2] for m in EnumDisplayMonitors()]  # (left, top, right, bottom)
            for area in all_areas:
                if r[0] >= area[0] and r[1] >= area[1] and r[2] <= area[2] and r[3] <= area[3]:
                    return False  # 完全在某个屏幕内，不需要平铺
        except Exception:
            pass
        return True  # 超出屏幕或获取失败，触发平铺

    # 多窗口：检测两两相交
    for i in range(len(valid_rects)):
        for j in range(i + 1, len(valid_rects)):
            if _rects_overlap(valid_rects[i][1], valid_rects[j][1]):
                return True  # 有重叠，触发平铺
    return False


def _try_activate_single(hwnd: int, retry: int = 1) -> bool:
    """对单个窗口执行唤醒 → 确保在屏幕内 → 尝试查找 UIA 导航栏。

    Returns:
        True 表示 UIA 导航栏找到（窗口可用），False 表示失败
    """
    try:
        # 确保可见（最小化则先还原）
        if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
            force_focus_window(hwnd)
            time.sleep(0.5)

        _ensure_window_on_screen(hwnd)

        # 查找导航栏
        _, nav = find_nav_toolbar(hwnd, max_retries=retry)
        return nav is not None

    except Exception as e:
        _log("多开识别", f"hwnd={hwnd} 激活异常: {e}")
        return False
