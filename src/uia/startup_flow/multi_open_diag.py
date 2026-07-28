"""
多开可行性诊断模块（multi_open_diag.py）

在排列布局和识别微信前，评估屏幕空间与设备性能，
通过系统消息实时告知用户当前多开状态及任何潜在问题。

判决结果（verdict）：
  comfortable — 屏幕空间充足，使用标准宽松布局
  compact     — 屏幕空间偏紧，自动切换紧凑布局
  insufficient — 屏幕无法容纳所有微信，发出警告
  perf_warn   — 设备内存/CPU 不足，发出性能警告
"""

import psutil
from .utils import _log

# ── 布局阈值（单位：px，指每个微信可分配到的宽度） ────────────────────────
_WIDTH_COMFORTABLE = 600   # ≥600px/窗口 → 舒适
_WIDTH_COMPACT     = 500   # 500-599px/窗口 → 紧凑
# < 500px 则 insufficient

# ── 性能阈值 ─────────────────────────────────────────────────────────────────
_WECHAT_RAM_MB = 800       # 每个微信估算内存（MB）
_RAM_RESERVE_MB = 1500     # 系统保留内存（MB），低于此视为危险
_MIN_CPU_CORES = 2         # 建议最少 CPU 核心数（每个微信）


def assess_multiopen(n_windows: int) -> dict:
    """评估 n_windows 个微信实例的多开可行性。

    Returns:
        {
          "n_windows":         int,
          "total_width_px":    int,   # 所有显示器总可用宽度
          "width_per_win":     int,   # 每窗口可分配宽度
          "screen_verdict":    "comfortable" | "compact" | "insufficient",
          "ram_available_mb":  int,
          "ram_required_mb":   int,
          "cpu_cores":         int,
          "perf_ok":           bool,
          "verdict":           "ok" | "compact" | "warn",
          "issues":            [str],  # 问题描述（给用户看）
          "suggestions":       [str],  # 操作建议
        }
    """
    issues = []
    suggestions = []

    # ── 屏幕空间评估 ──────────────────────────────────────────────────────
    total_width = _get_total_screen_width()
    width_per_win = total_width // max(n_windows, 1)

    if width_per_win >= _WIDTH_COMFORTABLE:
        screen_verdict = "comfortable"
    elif width_per_win >= _WIDTH_COMPACT:
        screen_verdict = "compact"
        issues.append(
            f"屏幕总宽度 {total_width}px，{n_windows} 个微信平均每个仅 {width_per_win}px，"
            f"空间偏紧，已自动切换紧凑布局（最小宽度 {_WIDTH_COMPACT}px）"
        )
        suggestions.append("建议连接更大分辨率显示器，或减少同时运行的微信数量以获得更好体验")
    else:
        screen_verdict = "insufficient"
        issues.append(
            f"屏幕空间严重不足：总宽度 {total_width}px，{n_windows} 个微信每个仅能分配 "
            f"{width_per_win}px（最低要求 {_WIDTH_COMPACT}px）。部分微信窗口将被叠放"
        )
        suggestions.append("请减少微信数量，或扩展显示器分辨率/接入副屏，否则自动化操作可能互相干扰")

    # ── 设备性能评估 ──────────────────────────────────────────────────────
    ram_available = _get_available_ram_mb()
    ram_required  = n_windows * _WECHAT_RAM_MB + _RAM_RESERVE_MB
    cpu_cores     = psutil.cpu_count(logical=False) or 1
    cpu_ok        = cpu_cores >= n_windows * _MIN_CPU_CORES

    perf_ok = ram_available >= ram_required and cpu_ok

    if ram_available < ram_required:
        shortage = ram_required - ram_available
        issues.append(
            f"内存不足：运行 {n_windows} 个微信需要约 {ram_required}MB 可用内存，"
            f"当前仅剩 {ram_available}MB（短缺约 {shortage}MB）"
        )
        suggestions.append("建议关闭其他应用释放内存，或降低多开数量")

    if not cpu_ok:
        issues.append(
            f"CPU 核心偏少：{n_windows} 个微信建议 ≥{n_windows * _MIN_CPU_CORES} 个物理核心，"
            f"当前仅 {cpu_cores} 个，可能引起 UI 卡顿"
        )
        suggestions.append("CPU 核心不足时多开响应可能偏慢，属正常现象，并非软件 bug")

    # ── 综合判决 ──────────────────────────────────────────────────────────
    if screen_verdict == "insufficient" or not perf_ok:
        verdict = "warn"
    elif screen_verdict == "compact":
        verdict = "compact"
    else:
        verdict = "ok"

    result = {
        "n_windows":        n_windows,
        "total_width_px":   total_width,
        "width_per_win":    width_per_win,
        "screen_verdict":   screen_verdict,
        "ram_available_mb": ram_available,
        "ram_required_mb":  ram_required,
        "cpu_cores":        cpu_cores,
        "perf_ok":          perf_ok,
        "verdict":          verdict,
        "issues":           issues,
        "suggestions":      suggestions,
    }
    _log("多开诊断", f"n={n_windows} 屏宽={total_width}px/窗口={width_per_win}px "
         f"RAM可用={ram_available}MB 判决={verdict}")
    return result


def notify_multiopen_status(assessment: dict):
    """根据诊断结果，向前端推送系统通知。

    通知分级：
      ok         → 仅打印日志，不弹通知（正常情况无需打扰用户）
      compact    → info 级通知（告知已压缩布局）
      warn       → warning 级通知（详列问题及建议）
    """
    verdict  = assessment["verdict"]
    n        = assessment["n_windows"]
    issues   = assessment["issues"]
    suggests = assessment["suggestions"]

    if verdict == "ok":
        _log("多开诊断", f"✅ {n} 个微信多开环境评估通过，布局充裕")
        return

    if verdict == "compact":
        title = f"📐 {n} 个微信：屏幕空间偏紧，已自动压缩布局"
        body  = issues[0] if issues else "已切换紧凑排列模式"
        category = "info"
    else:  # warn
        title = f"⚠️ {n} 个微信多开可能受限"
        body  = "\n".join(issues) + ("\n💡 建议：" + "；".join(suggests) if suggests else "")
        category = "warning"

    _log("多开诊断", f"发送系统通知: {title}")
    _send_notification(title, body, category)


def _get_total_screen_width() -> int:
    """获取所有显示器工作区宽度之和（排除任务栏）。"""
    try:
        from win32api import EnumDisplayMonitors, GetMonitorInfo
        total = 0
        for mon in EnumDisplayMonitors():
            info = GetMonitorInfo(mon[0])
            work = info["Work"]  # (left, top, right, bottom)
            total += work[2] - work[0]
        return total if total > 0 else 1920
    except Exception:
        return 1920  # fallback


def _get_available_ram_mb() -> int:
    """获取当前系统可用物理内存（MB）。"""
    try:
        return int(psutil.virtual_memory().available / 1024 / 1024)
    except Exception:
        return 4096  # fallback


def _send_notification(title: str, body: str, category: str = "system"):
    """异步推送通知到前端（不阻塞识别流程）。"""
    import threading
    import asyncio

    def _do():
        try:
            from src.utils.alert_notifier import alert_notifier
            asyncio.run(alert_notifier.send_user_notification(
                title=title, body=body, category=category
            ))
        except Exception as e:
            _log("多开诊断", f"通知发送失败: {e}")

    threading.Thread(target=_do, daemon=True).start()
