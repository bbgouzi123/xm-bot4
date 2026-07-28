import os
import win32gui
import win32process
from src.uia.modules.core.connect import _is_wechat_title
from .utils import _log

# 已经诊断输出过的非标准微信窗口 hwnd 集合（避免重复刷屏）
_diag_seen_hwnds: set = set()

# 微信 4.x Chromium 内部窗口 ClassName，属于正常架构，无需诊断
_CHROMIUM_INTERNAL_CLASSES = frozenset({
    "Chrome_WidgetWin_0",
    "Chrome_WidgetWin_1",
    "Chrome_RenderWidgetHostHWND",
    "Intermediate D3D Window",
})

def _enum_wechat_windows() -> list:
    """枚举所有微信顶级窗口，返回 [(hwnd, w, h, visible), ...]"""
    results = []
    # 诊断：收集所有"疑似微信"窗口，帮助排查沙箱窗口不可见问题
    _diag_extra = []

    def _cb(hwnd, _):
        try:
            title = win32gui.GetWindowText(hwnd)
            cls = win32gui.GetClassName(hwnd)
            # 标准匹配
            if (cls.endswith("Qt51514QWindowIcon")
                    and _is_wechat_title(title)
                    and win32gui.IsWindow(hwnd)):
                r = win32gui.GetWindowRect(hwnd)
                w = r[2] - r[0]
                h = r[3] - r[1]
                vis = win32gui.IsWindowVisible(hwnd)
                results.append((hwnd, w, h, bool(vis)))
            # 诊断：捕获标题含"微信"但 ClassName 不同的窗口（可能是沙箱包装）
            # 跳过 Chromium 内部窗口：微信 4.x 多进程架构正常产物，不是幽灵窗口
            elif ("微信" in title
                  and not cls.endswith("Qt51514QWindowIcon")
                  and cls not in _CHROMIUM_INTERNAL_CLASSES
                  and win32gui.IsWindow(hwnd)):
                r = win32gui.GetWindowRect(hwnd)
                w = r[2] - r[0]
                h = r[3] - r[1]
                _diag_extra.append((hwnd, cls, title, w, h, win32gui.IsWindowVisible(hwnd)))
        except Exception:
            pass

    win32gui.EnumWindows(_cb, None)

    # 诊断输出（只在首次发现时打印，避免循环等待中刷屏）
    if _diag_extra:
        for hwnd, cls, title, w, h, vis in _diag_extra:
            if hwnd not in _diag_seen_hwnds:
                _diag_seen_hwnds.add(hwnd)
                _log("诊断", f"发现非标准微信窗口: hwnd={hwnd} cls='{cls}' "
                     f"title='{title}' {w}x{h} visible={vis}")

    return results


def detect_wechat_state() -> dict:
    """
    检测微信当前完整状态。

    Returns:
        {
            "running": bool,
            "main_windows": [(hwnd, w, h)] — 可见 ≥500×400,
            "login_windows": [(hwnd, w, h)] — 可见但较小（登录界面）,
            "hidden_windows": [(hwnd, w, h)] — 不可见,
            "exe_path": str | None,
        }
    """
    all_wins = _enum_wechat_windows()
    state = {
        "running": len(all_wins) > 0,
        "main_windows": [],         # 可见 + 大尺寸 = 主界面
        "login_windows": [],        # 可见 + 小尺寸 = 登录界面
        "hidden_windows": [],       # 不可见
        "exe_path": None,
    }
    for hwnd, w, h, vis in all_wins:
        if vis and w >= 500 and h >= 400:
            state["main_windows"].append((hwnd, w, h))
        elif vis:
            state["login_windows"].append((hwnd, w, h))
        else:
            state["hidden_windows"].append((hwnd, w, h))

    # 获取 exe 路径
    if all_wins:
        try:
            _, pid = win32process.GetWindowThreadProcessId(all_wins[0][0])
            import psutil
            state["exe_path"] = psutil.Process(pid).exe()
        except Exception:
            pass
    if not state["exe_path"]:
        from src.utils.wechat_launcher import get_wechat_path
        state["exe_path"] = get_wechat_path()
    return state
