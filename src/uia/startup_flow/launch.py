import os
import time
import ctypes
import subprocess
from .utils import _log
from .state import _enum_wechat_windows
from .window_ops import _activate_taskbar_window

def launch_wechat(exe_path: str) -> int | None:
    """
    启动微信并等待窗口出现。
    【关键】显式传递包含 QT_ACCESSIBILITY=1 的环境变量给子进程，
    确保微信启动时 Qt 框架能初始化 Accessibility Provider。

    Returns:
        新窗口的 hwnd，或 None
    """
    _log("启动", f"正在启动微信: {exe_path}")
    try:
        # 【根因修复】os.startfile (ShellExecute) 走 Explorer/Shell 路径，
        # 新进程的环境来自 Shell 而非调用进程，QT_ACCESSIBILITY=1 无法可靠传递。
        # 改用 subprocess.Popen 显式传递 env，并用三重标志脱离终端 Job Object：
        #   CREATE_NEW_PROCESS_GROUP (0x200)    — 新进程组，不接收 Ctrl+C
        #   DETACHED_PROCESS (0x8)              — 脱离控制台
        #   CREATE_BREAKAWAY_FROM_JOB (0x01000000) — 脱离终端 Job Object，Ctrl+C/关终端不杀微信
        os.environ["QT_ACCESSIBILITY"] = "1"
        # 广播环境变量变更（兜底，确保注册表中也有）
        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF, 0x001A, 0, "Environment", 2, 5000,
            ctypes.byref(ctypes.c_ulong(0)),
        )
        time.sleep(0.5)

        _log("启动", "通过 Popen + BREAKAWAY_FROM_JOB 启动微信（显式传递 QT_ACCESSIBILITY）...")
        try:
            subprocess.Popen(
                [exe_path],
                env={**os.environ, "QT_ACCESSIBILITY": "1"},
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP   # 0x200
                    | subprocess.DETACHED_PROCESS         # 0x8
                    | 0x01000000                          # CREATE_BREAKAWAY_FROM_JOB
                ),
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            # Job 不允许 breakaway 时降级：去掉 CREATE_BREAKAWAY_FROM_JOB
            _log("启动", "Job 不允许 breakaway，降级启动...")
            subprocess.Popen(
                [exe_path],
                env={**os.environ, "QT_ACCESSIBILITY": "1"},
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.DETACHED_PROCESS
                ),
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception as e:
        _log("启动", f"启动失败: {e}")
        return None

    # 等待微信窗口出现（登录窗口或主窗口均可）
    _log("启动", "等待微信窗口出现...")
    for i in range(20):
        time.sleep(1.0)
        wins = _enum_wechat_windows()
        # 优先找可见窗口
        visible = [(h, w, ht, vis) for h, w, ht, vis in wins if vis]
        if visible:
            best = max(visible, key=lambda x: x[1] * x[2])
            _log("启动", f"✓ 微信窗口已出现 hwnd={best[0]} {best[1]}x{best[2]}")
            # 【修复】立即将窗口激活到前台，防止被折叠到任务栏
            _activate_taskbar_window(best[0])
            return best[0]
        # 如果没有可见窗口 but have invisible windows, try to activate
        if wins:
            hwnd_candidate = wins[0][0]
            _log("启动", f"微信窗口已创建 but 不可见，尝试激活 hwnd={hwnd_candidate}")
            _activate_taskbar_window(hwnd_candidate)
            time.sleep(0.5)
            # Re-detect
            wins2 = _enum_wechat_windows()
            visible2 = [(h, w, ht, vis) for h, w, ht, vis in wins2 if vis]
            if visible2:
                best = max(visible2, key=lambda x: x[1] * x[2])
                _log("启动", f"✓ 微信窗口已激活 hwnd={best[0]} {best[1]}x{best[2]}")
                return best[0]
    _log("启动", "⚠ 等待微信窗口超时")
    return None
