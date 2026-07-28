"""
最早执行的运行环境：DLL 搜索路径、.env、Qt/DPI、控制台编码。
必须在 import src / FastAPI 之前调用。
"""

from __future__ import annotations

import os
import sys
import warnings


def _backend_root() -> str:
    """backend-python 根目录（本文件位于 app/ 下）。"""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _ensure_pywin32_system32_on_path() -> None:
    if sys.platform != "win32" or not hasattr(os, "add_dll_directory"):
        return
    try:
        import site
    except ImportError:
        return
    try:
        bases = list(site.getsitepackages())
    except Exception:
        bases = []
    try:
        u = site.getusersitepackages()
        if u:
            bases.append(u)
    except Exception:
        pass
    for base in bases:
        d = os.path.join(base, "pywin32_system32")
        if os.path.isdir(d):
            try:
                os.add_dll_directory(os.path.abspath(d))
            except (OSError, ValueError, AttributeError):
                pass
            return


def _ensure_clr_loader_dll_path() -> None:
    if sys.platform != "win32" or not hasattr(os, "add_dll_directory"):
        return
    try:
        import site
    except ImportError:
        return
    try:
        bases = list(site.getsitepackages())
    except Exception:
        bases = []
    try:
        u = site.getusersitepackages()
        if u:
            bases.append(u)
    except Exception:
        pass
    arch = "amd64" if sys.maxsize > 2**32 else "x86"
    for base in bases:
        d = os.path.join(base, "clr_loader", "ffi", "dlls", arch)
        if os.path.isfile(os.path.join(d, "ClrLoader.dll")):
            try:
                os.add_dll_directory(os.path.abspath(d))
            except (OSError, ValueError, AttributeError):
                pass
            return


def install_runtime_hooks() -> None:
    """DLL 路径、.env、Qt/DPI、stdout/stderr、无缓冲日志。"""
    _ensure_pywin32_system32_on_path()
    _ensure_clr_loader_dll_path()

    # 🛡️ 猴子补丁：防止 pywebview 在窗口销毁、句柄失效后调用 evaluate_js 抛出 WinForms 内部异常导致 Traceback 刷屏
    try:
        import webview
        _orig_eval = webview.Window.evaluate_js
        def _patched_eval(self, script, *args, **kwargs):
            try:
                if not getattr(self, 'gui', None):
                    return None
                return _orig_eval(self, script, *args, **kwargs)
            except Exception:
                return None
        webview.Window.evaluate_js = _patched_eval
    except Exception:
        pass

    warnings.filterwarnings(
        "ignore",
        message="Revert to STA COM threading mode",
        category=UserWarning,
    )

    if os.name == "nt":
        import ctypes
        import winreg
    else:
        ctypes = None
        winreg = None

    br = _backend_root()
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(br, ".env"))
        load_dotenv(os.path.join(br, "..", ".env"))
        load_dotenv(os.path.abspath(os.path.join(br, "..", "..", "..", ".env.global")))
        if getattr(sys, "frozen", False):
            _exe_dir = os.path.dirname(os.path.abspath(sys.executable))
            load_dotenv(os.path.join(_exe_dir, ".env"), override=True)
    except ImportError:
        pass

    os.environ["QT_ACCESSIBILITY"] = "1"
    if os.name == "nt" and ctypes:
        try:
            ctypes.windll.kernel32.SetEnvironmentVariableW("QT_ACCESSIBILITY", "1")
            if winreg:
                try:
                    _env_key = winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER,
                        r"Environment",
                        0,
                        winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE,
                    )
                    try:
                        _existing, _ = winreg.QueryValueEx(_env_key, "QT_ACCESSIBILITY")
                    except FileNotFoundError:
                        _existing = None
                    if _existing != "1":
                        winreg.SetValueEx(_env_key, "QT_ACCESSIBILITY", 0, winreg.REG_SZ, "1")
                        HWND_BROADCAST = 0xFFFF
                        WM_SETTINGCHANGE = 0x001A
                        ctypes.windll.user32.SendMessageTimeoutW(
                            HWND_BROADCAST,
                            WM_SETTINGCHANGE,
                            0,
                            "Environment",
                            2,
                            5000,
                            ctypes.byref(ctypes.c_ulong(0)),
                        )
                    winreg.CloseKey(_env_key)
                except Exception:
                    pass
        except Exception:
            pass

    if os.name == "nt" and ctypes:
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            try:
                shcore = ctypes.windll.shcore
                if hasattr(shcore, "SetProcessDpiAwareness"):
                    shcore.SetProcessDpiAwareness(2)
            except (AttributeError, OSError):
                try:
                    ctypes.windll.user32.SetProcessDPIAware()
                except Exception:
                    pass

    def _is_stream_broken(stream) -> bool:
        """检测 stdout/stderr 的底层 fd 是否已被关闭（WinForms 接管时常见）"""
        if stream is None:
            return True
        try:
            fd = stream.fileno()
            os.fstat(fd)  # 如果 fd 被关闭会抛 OSError
            return False
        except (OSError, ValueError, AttributeError):
            return True

    if _is_stream_broken(sys.stdout):
        sys.stdout = open(os.devnull, "w")
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass

    if _is_stream_broken(sys.stderr):
        sys.stderr = open(os.devnull, "w")
    try:
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass

    os.environ["PYTHONUNBUFFERED"] = "1"


def configure_sys_path() -> str:
    """插入 backend-python 与 monorepo packages/python；返回 backend 根目录。"""
    br = _backend_root()
    if br not in sys.path:
        sys.path.insert(0, br)
    pkgs = os.path.abspath(os.path.join(br, "..", "..", "..", "packages", "python"))
    if os.path.isdir(pkgs) and pkgs not in sys.path:
        sys.path.insert(0, pkgs)

    # 💡 针对旧版 APScheduler 序列化任务可能依赖 src.uia.task_runner 导致的反序列化失败进行重定向兼容
    try:
        import src.utils.uia_task_runner
        sys.modules['src.uia.task_runner'] = src.utils.uia_task_runner
    except Exception:
        pass

    return br
