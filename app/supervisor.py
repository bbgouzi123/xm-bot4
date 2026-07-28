import os
import sys
import time
import subprocess

_single_lock_file = None


def _process_has_visible_window(pid: int) -> bool:
    """检查指定 PID 的进程是否拥有可见的顶层窗口（Windows 专用）。

    用于守护进程在强杀前判断工作进程是否仍在正常运行 GUI：
    如果窗口还在显示，说明用户仍在操作界面，不应因心跳暂停而误杀。
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32

        EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        found = [False]

        def callback(hwnd, _lparam):
            # 获取窗口所属进程 PID
            window_pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
            if window_pid.value == pid and user32.IsWindowVisible(hwnd):
                found[0] = True
                return False  # 停止枚举
            return True  # 继续枚举

        user32.EnumWindows(EnumWindowsProc(callback), 0)
        return found[0]
    except Exception:
        return False


def _write_crash_log(message: str) -> None:
    """将崩溃和守护日志写入 crash.log"""
    try:
        import datetime
        appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
        log_dir = os.path.join(appdata, "xm-bot4", "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "crash.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"[{datetime.datetime.now().isoformat()}]\n")
            f.write(message)
            f.write(f"\n{'='*60}\n")
    except Exception:
        pass


def _check_single_instance() -> bool:
    """利用文件锁确保只有一个打包实例运行"""
    global _single_lock_file
    try:
        appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
        lock_dir = os.path.join(appdata, "xm-bot4")
        os.makedirs(lock_dir, exist_ok=True)
        lock_path = os.path.join(lock_dir, "xm-bot4.lock")
        
        # 尝试独占写入并锁定首字节
        _single_lock_file = open(lock_path, "w", encoding="utf-8")
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(_single_lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(_single_lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except Exception:
        return False


def run_supervisor():
    """守护主进程，看管工作子进程，并在工作进程异常退出或心跳超时卡死时自动拉起。"""
    # ── 单实例检测（打包模式下强制限制） ──
    if getattr(sys, 'frozen', False):
        if not _check_single_instance():
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0,
                    "程序已经在一个窗口中运行！\n\n请检查系统右下角托盘图标，请勿重复启动。\n如果已意外卡死，请在任务管理器中结束相关进程后重试。",
                    "xm-bot4 - 提示",
                    0x30  # MB_ICONWARNING
                )
            except Exception:
                pass
            sys.exit(0)

    # 构建启动 Worker 的命令行参数
    cmd = [sys.executable] if getattr(sys, 'frozen', False) else [sys.executable, sys.argv[0]]
    worker_args = [arg for arg in sys.argv[1:] if arg != '--worker']
    cmd.extend(['--worker'] + worker_args)

    _write_crash_log(f"[守护进程] 启动工作子进程: {' '.join(cmd)}")

    last_restarts = []
    max_restarts = 5
    restart_time_window = 60  # 秒
    # 心跳超时 180 秒（打包版冷启动 WebView2/CLR 初始化重，且后台线程原生崩溃可能
    # 暂时阻塞心跳线程但不一定导致进程永久卡死）
    heartbeat_timeout = 180

    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    hb_file = os.path.join(appdata, "xm-bot4", "heartbeat.txt")

    while True:
        try:
            # 启动时清除旧心跳文件
            if os.path.exists(hb_file):
                try:
                    os.remove(hb_file)
                except Exception:
                    pass

            # 自动清理老旧的 _MEI 临时目录以防磁盘满
            try:
                import tempfile
                import shutil
                temp_dir = tempfile.gettempdir()
                now = time.time()
                for item in os.listdir(temp_dir):
                    if item.startswith("_MEI") and os.path.isdir(os.path.join(temp_dir, item)):
                        path = os.path.join(temp_dir, item)
                        try:
                            mtime = os.path.getmtime(path)
                            if now - mtime > 86400:  # 24小时
                                current_mei = getattr(sys, "_MEIPASS", None)
                                if current_mei and os.path.abspath(path) == os.path.abspath(current_mei):
                                    continue
                                _write_crash_log(f"[守护进程] 发现遗留临时解压目录，执行清理: {path}")
                                shutil.rmtree(path, ignore_errors=True)
                        except Exception as e:
                            pass
            except Exception as e:
                _write_crash_log(f"[守护进程] 清理临时目录失败: {e}")

            # 检查磁盘空间红线
            try:
                import shutil
                usage = shutil.disk_usage(appdata)
                free_gb = usage.free / (1024 ** 3)
                if free_gb < 1.0:
                    _write_crash_log(f"[守护进程] [警告] 磁盘可用空间不足: {free_gb:.2f} GB")
                    try:
                        from src.utils.alert_notifier import alert_notifier
                        import asyncio
                        import threading
                        body = f"系统盘当前剩余空间为 {free_gb:.2f} GB。建议清理磁盘，避免因空间不足导致微信接收缓存写入错误或数据库损坏。"
                        def run_alert():
                            try:
                                loop = asyncio.new_event_loop()
                                asyncio.set_event_loop(loop)
                                loop.run_until_complete(
                                    alert_notifier.send_user_notification("⚠️ 磁盘空间过低警告", body, "system")
                                )
                            except Exception:
                                pass
                        threading.Thread(target=run_alert, daemon=True).start()
                    except Exception:
                        pass
            except Exception as ds_e:
                _write_crash_log(f"[守护进程] 检查磁盘空间异常: {ds_e}")

            # 启动工作进程，显式设置 close_fds=True 避免继承主进程的文件锁句柄
            p = subprocess.Popen(cmd, close_fds=True)
            start_time = time.time()

            # 循环轮询心跳与进程状态
            while p.poll() is None:
                time.sleep(2)
                
                # 启动宽限期：给子进程 40 秒用来初始化并写入第一次心跳
                # （打包版需要加载 WebView2/CLR/.NET 异常钩子，比开发模式慢 2~3 倍）
                if time.time() - start_time < 40:
                    continue

                if os.path.exists(hb_file):
                    try:
                        mtime = os.path.getmtime(hb_file)
                        if time.time() - mtime > heartbeat_timeout:
                            # 强杀前先检查工作进程是否仍有可见窗口
                            # 有窗口 = 用户正在操作界面，不应强杀（可能是后台线程原生崩溃
                            # 暂时阻塞了心跳线程，但 GUI 主线程仍在正常运行）
                            if _process_has_visible_window(p.pid):
                                _write_crash_log(
                                    f"[守护进程] 心跳超时 ({int(time.time() - mtime)}s)，"
                                    f"但工作进程 PID={p.pid} 仍有可见窗口，暂不强杀（等待用户操作或自动恢复）。"
                                )
                                # 重置心跳文件时间戳，再给一轮宽限期
                                try:
                                    with open(hb_file, "w", encoding="utf-8") as f:
                                        f.write(str(time.time()))
                                except Exception:
                                    pass
                                continue

                            _write_crash_log(f"[守护进程] 警告: 检测到工作子进程心跳超时 ({int(time.time() - mtime)} 秒未更新) 且无可见窗口，判定为卡死，将强行终止并重新拉起。")
                            
                            # 尝试使用 pid 文件进行精准强杀
                            killed_by_pid = False
                            try:
                                pid_file = os.path.join(appdata, "xm-bot4", "worker.pid")
                                if os.path.exists(pid_file):
                                    with open(pid_file, "r", encoding="utf-8") as f:
                                        target_pid = int(f.read().strip())
                                    if target_pid > 0:
                                        import psutil
                                        if psutil.pid_exists(target_pid):
                                            _write_crash_log(f"[守护进程] 精准杀灭 PID 文件记录的工作进程 (PID={target_pid})...")
                                            proc = psutil.Process(target_pid)
                                            proc.kill()
                                            killed_by_pid = True
                            except Exception as pe:
                                _write_crash_log(f"[守护进程] 读取/杀灭 pid 异常: {pe}")
                            
                            if not killed_by_pid:
                                p.terminate()
                                time.sleep(2)
                                if p.poll() is None:
                                    p.kill()
                            break
                    except Exception:
                        pass
                else:
                    # 启动超过 90 秒仍未写入心跳文件，判定为启动卡死
                    # （打包版首次启动可能需要 60~80 秒完成 PyInstaller 解包 + WebView2 初始化）
                    if time.time() - start_time > 90:
                        _write_crash_log("[守护进程] 警告: 工作子进程启动超时且未发送首次心跳，将强行终止并重新拉起。")
                        
                        # 尝试使用 pid 文件进行精准强杀
                        killed_by_pid = False
                        try:
                            pid_file = os.path.join(appdata, "xm-bot4", "worker.pid")
                            if os.path.exists(pid_file):
                                with open(pid_file, "r", encoding="utf-8") as f:
                                    target_pid = int(f.read().strip())
                                if target_pid > 0:
                                    import psutil
                                    if psutil.pid_exists(target_pid):
                                        _write_crash_log(f"[守护进程] 精准杀灭 PID 文件记录的工作进程 (PID={target_pid})...")
                                        proc = psutil.Process(target_pid)
                                        proc.kill()
                                        killed_by_pid = True
                        except Exception as pe:
                            _write_crash_log(f"[守护进程] 读取/杀灭 pid 异常: {pe}")
                            
                        if not killed_by_pid:
                            p.terminate()
                            time.sleep(2)
                            if p.poll() is None:
                                p.kill()
                        break

            # 此时进程要么退出，要么被上面 break 强杀
            exit_code = p.wait()
            _write_crash_log(f"[守护进程] 工作子进程已退出，退出码: {exit_code}")

            # 正常退出（如用户关闭窗口、主动调用 close_app）退出码为 0
            if exit_code == 0:
                _write_crash_log("[守护进程] 检测到正常退出，守护进程退出。")
                sys.exit(0)

            # 异常退出，记录重启时间
            now = time.time()
            last_restarts = [t for t in last_restarts if now - t < restart_time_window]

            if len(last_restarts) >= max_restarts:
                msg = f"[守护进程] 工作进程在短时间内发生频繁崩溃（{restart_time_window} 秒内崩溃 {len(last_restarts)} 次），为避免死循环已停止拉起。"
                _write_crash_log(msg)
                try:
                    import ctypes
                    ctypes.windll.user32.MessageBoxW(
                        0,
                        "程序运行中频繁发生致命错误，已停止自动重启。\n\n详情请查看日志：%APPDATA%/xm-bot4/logs/crash.log",
                        "xm-bot4 - 频繁崩溃提示",
                        0x10  # MB_ICONERROR
                    )
                except Exception:
                    pass
                sys.exit(exit_code)

            last_restarts.append(now)
            _write_crash_log(f"[守护进程] 将在 2 秒后重启工作进程 (第 {len(last_restarts)} 次)...")
            time.sleep(2)

        except KeyboardInterrupt:
            _write_crash_log("[守护进程] 收到 KeyboardInterrupt 信号，主动退出。")
            try:
                p.terminate()
            except Exception:
                pass
            sys.exit(0)
        except Exception as e:
            _write_crash_log(f"[守护进程] 运行异常: {e}")
            time.sleep(5)
