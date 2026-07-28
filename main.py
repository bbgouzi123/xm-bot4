"""
xm-bot4 AI Bot - Python Backend
Entry point: exposes ASGI app after environment initialization.
"""
from __future__ import annotations

# 💡 重定向打包运行环境下的临时缓存文件夹，避开管家类清理工具对 Temp 目录的 DLL 误删
import app.temp_redirect

# 💡 屏蔽损坏的 brotli 库避免网络库在特定打包环境下抛出 AttributeError 异常，强制回退至标准 gzip 传输编码
import sys
sys.modules['brotli'] = None
sys.modules['brotlicffi'] = None
sys.modules['brotlipy'] = None

import os
import sys

# 💡 Configure standard output and standard error encoding to prevent UnicodeEncodeError on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(errors='backslashreplace')
        sys.stderr.reconfigure(errors='backslashreplace')
    except Exception:
        pass

# 💡 针对旧版 APScheduler 序列化任务可能依赖 src.uia.task_runner 导致的反序列化失败的重定向兼容已移至 app/runtime_preamble.py (在 configure_sys_path 中执行)

# 💡 强制将整个 Python 进程的 COM 套间初始化为单线程套间 (STA, 2)
# 这能从根本上保证 WinForms WebView2 GUI 主线程在正确的 STA 模式下运行，防止由于套间不兼容引发的 RPC_E_DISCONNECTED (0x80010108) 崩溃
sys.coinit_flags = 2  # 2 代表 COINIT_APARTMENTTHREADED (STA)

# 💡 针对 PyInstaller 打包运行环境下的 DLL 寻找路径进行极其早期的补丁，以彻底解决 pythonnet 加载 Python.Runtime.dll 时解析 runtime 符号失败的问题
if getattr(sys, 'frozen', False):
    _exe_dir = os.path.dirname(sys.executable)
    _internal_dir = os.path.join(_exe_dir, "_internal")
    
    # 1. 扩充 PATH 环境变量，使得 clr_loader 能通过 LoadLibrary 找到主 Python DLL
    os.environ["PATH"] = _exe_dir + os.path.pathsep + _internal_dir + os.path.pathsep + os.environ.get("PATH", "")
    try:
        os.add_dll_directory(_exe_dir)
    except Exception:
        pass
    try:
        os.add_dll_directory(_internal_dir)
    except Exception:
        pass
    try:
        _runtime_dir = os.path.join(_internal_dir, "pythonnet", "runtime")
        if os.path.exists(_runtime_dir):
            os.environ["PATH"] = _runtime_dir + os.path.pathsep + os.environ["PATH"]
            os.add_dll_directory(_runtime_dir)
            # 🔧 设置 DEVPATH 让 .NET Framework Fusion 直接从此目录加载程序集，
            # 解决 netfx 模式下 Failed to resolve Python.Runtime.Loader.Initialize 的问题
            os.environ["DEVPATH"] = _runtime_dir + os.path.pathsep + os.environ.get("DEVPATH", "")
    except Exception:
        pass

    # 2. 显式锁定 PYTHONNET_PYDLL 环境变量，防止 pythonnet 的 clr_loader 在复杂环境下定位 python312.dll 错误
    try:
        _dll_name = f"python{sys.version_info.major}{sys.version_info.minor}.dll"
        _candidates = [
            os.path.join(_exe_dir, _dll_name),
            os.path.join(_internal_dir, _dll_name),
            os.path.join(_internal_dir, "DLLs", _dll_name),
        ]
        for _path in _candidates:
            if os.path.exists(_path):
                os.environ["PYTHONNET_PYDLL"] = _path
                break
    except Exception:
        pass

# ==================== 超级早期诊断 ====================
# 在任何第三方模块加载之前就写日志，确保即使后续 import 失败（DLL 缺失、
# .pyd 加载出错等），也能在日志文件中留下「Python 解释器已启动」的痕迹。
# 如果 early_boot.log 完全不存在，说明问题在 PyInstaller bootloader 层面
# （杀毒拦截、DLL 缺失导致 PE 加载失败等）。
if getattr(sys, 'frozen', False):
    try:
        _early_log_dir = os.path.join(
            os.environ.get("APPDATA", os.path.expanduser("~")),
            "xm-bot4", "logs"
        )
        os.makedirs(_early_log_dir, exist_ok=True)
        _early_log = os.path.join(_early_log_dir, "early_boot.log")
        with open(_early_log, "a", encoding="utf-8") as _f:
            import datetime as _dt
            _f.write(f"\n{'='*60}\n")
            _f.write(f"[{_dt.datetime.now().isoformat()}] EXE 启动 - Python 解释器已初始化\n")
            _f.write(f"  sys.executable  = {sys.executable}\n")
            _f.write(f"  sys.version     = {sys.version}\n")
            _f.write(f"  os.getcwd()     = {os.getcwd()}\n")
            _f.write(f"  平台            = {sys.platform} / {os.name}\n")
            _f.write(f"  sys.argv        = {sys.argv}\n")
            try:
                import platform as _pf
                _f.write(f"  Windows 版本    = {_pf.platform()}\n")
                _f.write(f"  架构            = {_pf.machine()}\n")
            except Exception:
                pass
            _f.write(f"{'='*60}\n")
            del _dt
    except Exception:
        pass

# ==================== 崩溃日志文件机制 ====================
# console=False 打包环境下 stdout/stderr 无可用输出，
# 必须将所有异常写入日志文件才能诊断"程序无声退出"问题

def _get_crash_log_path() -> str:
    """获取崩溃日志文件路径"""
    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    log_dir = os.path.join(appdata, "xm-bot4", "logs")
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, "crash.log")

# ==================== 原生崩溃捕获（faulthandler）====================
# segfault / access violation 无法被 Python 异常捕获，
# 必须用 faulthandler 将 C 层面的崩溃堆栈写入文件
_fault_log_path = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "xm-bot4", "logs", "native_crash.log"
)
os.makedirs(os.path.dirname(_fault_log_path), exist_ok=True)
try:
    import faulthandler
    _fault_file = open(_fault_log_path, "a", encoding="utf-8")
    faulthandler.enable(file=_fault_file, all_threads=True)
except Exception:
    pass

# 💡 针对旧版 COM RPC 崩溃防护初始化移至 configure_sys_path 之后 (以防导入 ModuleNotFoundError)


def _write_crash_log(message: str) -> None:
    """将崩溃信息写入日志文件（追加模式），确保任何环境下都能记录"""
    try:
        import datetime
        log_path = _get_crash_log_path()
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"[{datetime.datetime.now().isoformat()}]\n")
            f.write(message)
            f.write(f"\n{'='*60}\n")
    except Exception:
        pass  # 日志写入本身不能导致更多问题


def _install_global_exception_hooks() -> None:
    """安装全局异常钩子，捕获所有线程的未处理异常"""
    import threading

    # 主线程未捕获异常钩子
    def _sys_excepthook(exc_type, exc_value, exc_tb):
        import traceback
        tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        msg = f"[FATAL] 主线程未捕获异常:\n{tb_str}"
        _write_crash_log(msg)
        print(msg, file=sys.stderr)

    sys.excepthook = _sys_excepthook

    # 子线程未捕获异常钩子（Python 3.8+）
    if hasattr(threading, "excepthook"):
        _original_thread_excepthook = threading.excepthook

        def _thread_excepthook(args):
            import traceback
            tb_str = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
            thread_name = args.thread.name if args.thread else "Unknown"
            msg = f"[FATAL] 子线程 '{thread_name}' 未捕获异常:\n{tb_str}"
            _write_crash_log(msg)
            print(msg, file=sys.stderr)
            # 调用原始钩子以保持默认行为
            if _original_thread_excepthook is not threading.__excepthook__:
                try:
                    _original_thread_excepthook(args)
                except Exception:
                    pass

        threading.excepthook = _thread_excepthook


# ==================== 心跳存活与守护机制 ====================
def _start_worker_heartbeat_loop() -> None:
    """启动工作子进程的心跳循环线程以自证存活状态"""
    import threading
    import time
    def _loop():
        appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
        hb_file = os.path.join(appdata, "xm-bot4", "heartbeat.txt")
        while True:
            try:
                with open(hb_file, "w", encoding="utf-8") as f:
                    f.write(str(time.time()))
            except Exception:
                pass
            time.sleep(5)
    t = threading.Thread(target=_loop, daemon=True)
    t.name = "WorkerHeartbeatThread"
    t.start()


def entry_point():
    is_worker = "--worker" in sys.argv

    if not is_worker:
        # 主入口：启动守护进程模式
        from app.supervisor import run_supervisor
        run_supervisor()
        return

    # 工作进程模式：执行原有的全部初始化流程
    try:
        # 写入 PID 文件，供守护进程进行精准强杀
        appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
        pid_file = os.path.join(appdata, "xm-bot4", "worker.pid")
        os.makedirs(os.path.dirname(pid_file), exist_ok=True)
        with open(pid_file, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        _start_worker_heartbeat_loop()
    except Exception:
        pass

    # 尽早安装全局异常钩子
    _write_crash_log("阶段 1/6: 安装全局异常钩子")
    _install_global_exception_hooks()

    try:
        _write_crash_log("阶段 2/6: 配置运行时路径与 DLL 搜索")
        from app.runtime_preamble import configure_sys_path, install_runtime_hooks
        install_runtime_hooks()
        configure_sys_path()

        # ==================== COM RPC 崩溃防护 ====================
        # 在 UIA 操作微信朋友圈时，控件引用失效会导致 COM RPC 断连 (0x80010108/0x8001010d)，
        # 这是 C 层面的致命异常，Python try/except 无法捕获，进程会直接退出。
        # 必须在进程启动早期安装 SEH 过滤器来降级这些异常。
        _write_crash_log("阶段 2.5/6: 安装 COM RPC 崩溃防护 (SEH 过滤器)")
        try:
            from src.utils.safe_uia import install_com_crash_guard
            install_com_crash_guard()
        except Exception as guard_e:
            _write_crash_log(f"安装 COM RPC 崩溃防护失败: {guard_e}")

        _write_crash_log("阶段 3/6: 导入核心模块 (bootstrap / factory / cleanup)")
        from app.bootstrap import main as bootstrap_main, register_app
        from app.factory import create_app
        from src.utils.cleanup import graceful_cleanup

        # 💡 提前并在后台线程中激活模拟讲述人，预热 UIA COM 连接以避免阻塞，同时确保生命周期内引用计数不归零
        _write_crash_log("阶段 3.5/6: 启动后台模拟讲述人预热与生命周期保持")
        try:
            import threading
            def _preheat_narrator():
                try:
                    from src.uia.startup_flow.narrator import start_narrator
                    start_narrator(source="preheat")
                    _write_crash_log("✓ 后台模拟讲述人预热并激活成功")
                except Exception as preheat_err:
                    _write_crash_log(f"后台预热激活模拟讲述人失败: {preheat_err}")
            
            threading.Thread(target=_preheat_narrator, daemon=True, name="NarratorPreheatThread").start()
        except Exception as preheat_thread_err:
            _write_crash_log(f"创建讲述人预热线程失败: {preheat_thread_err}")

        _write_crash_log("阶段 4/6: 创建 FastAPI 应用 (注册路由/中间件)")
        app = create_app()
        register_app(app)

        # Console control handler (Windows only)
        if sys.platform == "win32":
            try:
                import ctypes
                @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
                def _console_ctrl_handler(dwCtrlType):
                    if dwCtrlType in (0, 2):
                        print("\n[Close] Received console interrupt signal, terminating engine...")
                        try:
                            graceful_cleanup()
                        except Exception:
                            pass
                        os._exit(0)
                    return 1

                global_ctrl_handler = _console_ctrl_handler
                ctypes.windll.kernel32.SetConsoleCtrlHandler(global_ctrl_handler, 1)
            except Exception:
                pass

        _write_crash_log("阶段 5/6: 进入 GUI 引导 (环境自检 → WebView 窗口)")
        bootstrap_main()
        _write_crash_log("阶段 6/6: bootstrap_main() 已返回")


        # Keep console open in packaged environment
        if getattr(sys, "frozen", False):
            print("\n" + "-" * 60)
            print("Main program has finished.")
            try:
                input("Press [Enter] to exit this window...")
            except (EOFError, KeyboardInterrupt, RuntimeError):
                pass

    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        import traceback
        err_msg = f"后端主入口异常退出: {e}\n\n{traceback.format_exc()}"
        print(f"\n" + "!" * 60)
        print(err_msg)
        print("!" * 60)

        # 写入崩溃日志文件
        _write_crash_log(err_msg)

        # 上报致命崩溃到 xm-sentinel（含本地日志文件附件）
        try:
            from xm_py_server.sentinel import report_crash_with_logs
            import time as _t
            report_crash_with_logs(
                message=f"FATAL: {type(e).__name__}: {e}",
                stack_trace=traceback.format_exc(),
                context={"stage": "entry_point"},
                source="backend",
                # 自动读取 %APPDATA%/xm-bot4/logs/ 下全部日志文件
                max_lines_per_file=800,
            )
            # 等待后台上传线程完成（最多 8 秒，进程退出前发出）
            _t.sleep(8)
        except Exception:
            pass
        
        if getattr(sys, "frozen", False):
            # 弹出一个原生 Windows 对话框，防止 console=False 时死得无声无息
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(0, f"程序启动失败：\n{e}\n\n详情请查看日志文件。", "xm-bot4 - 致命错误", 0x10)
            except Exception:
                pass

            print("\n程序遇到致命错误已停止运行。")
            print(f"您可以查看日志文件排查问题: %APPDATA%/xm-bot4/logs/crash.log")
            print("=" * 60)
            try:
                input("请按 [回车键] 退出此窗口...")
            except (EOFError, KeyboardInterrupt, RuntimeError):
                pass
        sys.exit(1)


if __name__ == "__main__":
    entry_point()
