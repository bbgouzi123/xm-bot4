import os
import time
import subprocess
try:
    import win32gui
    import win32process
except ImportError:
    win32gui = None
    win32process = None
import psutil

def find_wechat_path() -> str:
    """通过注册表精准查找微信安装路径"""
    from src.utils.wechat_launcher import get_wechat_path
    return get_wechat_path()

def kill_wechat():
    """强力清理残留的微信进程以保证环境纯净"""
    _NO_WINDOW = subprocess.CREATE_NO_WINDOW
    subprocess.run(["taskkill", "/F", "/IM", "WeChat.exe", "/T"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    subprocess.run(["taskkill", "/F", "/IM", "Weixin.exe", "/T"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    time.sleep(1.5)


def get_wechat_pid(timeout=25, instance_index=None):
    """基于 Win32 API / 环境变量高频探测窗体或进程，避免 RPA 焦点丢失与多实例混淆问题"""
    start = time.time()
    while time.time() - start < timeout:
        # 1. 优先根据独立的隔离实例环境变量精准定位 PID
        if instance_index is not None:
            for p in psutil.process_iter(['pid', 'name', 'environ', 'cwd', 'cmdline']):
                try:
                    name = p.info.get('name') or ''
                    if name.lower() in ('wechat.exe', 'weixin.exe'):
                        env_vars = p.info.get('environ') or {}
                        if env_vars.get("XM_WECHAT_INSTANCE") == str(instance_index):
                            return p.info['pid']
                        # 隔离目录匹配兜底
                        cwd = p.info.get('cwd') or ''
                        cmdline = p.info.get('cmdline') or []
                        cmdline_str = " ".join(cmdline).lower()
                        target_dir_token = f"instance_{instance_index}"
                        if target_dir_token in cwd.lower() or target_dir_token in cmdline_str:
                            return p.info['pid']
                except Exception:
                    pass

        # 2. 兜底通过窗口类名或标题检索
        target_pid = [0]
        
        def enum_cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd).strip().lower()
            cls = win32gui.GetClassName(hwnd).lower()
            is_wechat = (
                title in ('微信', 'wechat', 'weixin')
                or 'wechat' in title
                or 'weixin' in title
                or 'wechatmainwndforpc' in cls
                or 'wechatloginwndforpc' in cls
                or 'qt51514qwindowicon' in cls
            )
            if is_wechat:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid > 0:
                    if psutil.pid_exists(pid):
                        try:
                            proc = psutil.Process(pid)
                            if proc.name().lower() in ('wechat.exe', 'weixin.exe'):
                                target_pid[0] = pid
                                return False
                        except Exception:
                            pass
            return True
            
        try:
            win32gui.EnumWindows(enum_cb, None)
        except Exception:
            pass
            
        if target_pid[0] > 0:
            return target_pid[0]
        time.sleep(0.5)
    return None

def clean_and_launch_wechat(wechat_path, instance_index, instance_id, log, pre_click_hook_cb=None):
    """
    清理本实例残留的进程并拉起新的隔离实例。
    返回已拉起的微信进程 PID。
    """
    launched_pid = None
    if instance_index is not None:
        log(f"正在清理残留的微信实例 {instance_index}，保持该隔离环境纯净...")
        killed = False
        
        # 方法 1：从共享内存窗口句柄定位 PID
        target_pids = set()
        try:
            from src.utils.instance_manager import InstanceManagerV2
            import win32process
            manager = InstanceManagerV2.get_instance()
            for inst_id, inst_data in manager.get_all_instances().items():
                if inst_id == instance_id or inst_data.get("wxid") == instance_id or inst_id == f"instance_{instance_index}":
                    hwnd = inst_data.get("window_handle")
                    if hwnd and win32gui.IsWindow(hwnd):
                        _, w_pid = win32process.GetWindowThreadProcessId(hwnd)
                        if w_pid > 0:
                            target_pids.add(w_pid)
        except Exception as e_pid:
            log(f"句柄查找待杀进程异常: {e_pid}")

        # 方法 2：综合匹配并终止进程
        for p in psutil.process_iter(['pid', 'name', 'environ', 'cwd', 'cmdline']):
            try:
                name = p.info.get('name') or ''
                pid = p.info.get('pid')
                if name.lower() in ('wechat.exe', 'weixin.exe'):
                    if pid in target_pids:
                        p.kill()
                        killed = True
                        continue
                        
                    env_vars = p.info.get('environ') or {}
                    inst_val = env_vars.get("XM_WECHAT_INSTANCE")
                    
                    if inst_val == str(instance_index):
                        p.kill()
                        killed = True
                        continue
                        
                    cwd = p.info.get('cwd') or ''
                    cmdline = p.info.get('cmdline') or []
                    cmdline_str = " ".join(cmdline).lower()
                    target_dir_token = f"instance_{instance_index}"
                    if target_dir_token in cwd.lower() or target_dir_token in cmdline_str:
                        p.kill()
                        killed = True
                        continue
            except Exception:
                pass
        if killed:
            time.sleep(1.5)
    else:
        log("正在清理旧的微信进程，准备干净的环境...")
        kill_wechat()

    log(f"正在启动微信进程 ({wechat_path})...")
    
    if instance_index is not None:
        try:
            from src.utils.mutex_killer import build_isolated_env, apply_filesave_path, restore_filesave_path, kill_wechat_mutex
            from src.utils.isolate_container_manager import is_isolate_container_available, get_isolate_container_manager
            
            if is_isolate_container_available():
                log("[隔离舱] 检测到自研安全隔离舱可用，该方案物理免疫全局互斥锁，跳过 Mutex 清理步骤")
            else:
                log("正在清除全局微信多开 Mutex 互斥锁，以打通新隔离实例的启动通道...")
                # 🌟 [安全守护防御] 将 NtQueryObject / DuplicateHandle 等易卡死内核级暗杀放入独立守护线程，强设 3.0s 超时
                # 彻底消除由于 Windows 僵尸进程句柄引发的启动无限期卡死灾难
                import threading
                mutex_res = {}
                
                def _mutex_worker():
                    try:
                        mutex_res["data"] = kill_wechat_mutex()
                    except Exception as ex_m:
                        mutex_res["error"] = str(ex_m)
                        
                t_mutex = threading.Thread(target=_mutex_worker, daemon=True)
                t_mutex.start()
                t_mutex.join(timeout=3.0)
                
                if t_mutex.is_alive():
                    log("⚠️ Mutex 清理执行超时 (3.0s)，已被强制安全跳过，防止多开启动卡死")
                else:
                    data = mutex_res.get("data")
                    if data and data.get("success"):
                        log(f"Mutex 清理执行完毕，暗杀成功数: {data.get('killed_count')}")
                        for detail_line in data.get("details", []):
                            log(f"  [Mutex] {detail_line}")
                    else:
                        err_msg = mutex_res.get("error") or (data.get("error") if data else "未知异常")
                        log(f"Mutex 清理异常/失败: {err_msg}")
            
            if is_isolate_container_available():
                log("[隔离舱] 检测到安全隔离舱可用，正在使用安全隔离舱启动微信实例...")
                container_mgr = get_isolate_container_manager()
                socks_port = 10800 + instance_index
                ok, msg = container_mgr.launch_wechat_in_container(wechat_path, instance_index, socks_port=socks_port)
                log(f"[隔离舱] 启动结果: {msg}")
                if not ok:
                    raise Exception(msg)
                
                import re
                m_pid = re.search(r"PID=(\d+)", msg)
                if m_pid:
                    launched_pid = int(m_pid.group(1))
                    log(f"[隔离舱] 提取到启动 PID: {launched_pid}")
            else:
                log("[隔离舱] 未检测到安全隔离舱，降级为常规环境变量隔离启动...")
                env_dict, instance_dir = build_isolated_env(instance_index)
                process_name = os.path.basename(wechat_path).lower()
                original_filesave = apply_filesave_path(instance_dir, process_name)
                
                popen_kwargs = dict(
                    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                    close_fds=True,
                )
                if env_dict:
                    popen_kwargs["env"] = env_dict
                if instance_dir:
                    popen_kwargs["cwd"] = os.path.dirname(wechat_path)
                
                p_obj = subprocess.Popen([wechat_path], **popen_kwargs)
                launched_pid = p_obj.pid
                log(f"[启动] 常规隔离模式启动，PID: {launched_pid}")
                
                time.sleep(1.5)
                if original_filesave:
                    restore_filesave_path(process_name, original_filesave)
        except Exception as e_launch:
            log(f"使用隔离环境启动失败: {e_launch}，降级为默认启动。")
            p_obj = subprocess.Popen([wechat_path], cwd=os.path.dirname(wechat_path), shell=False)
            launched_pid = p_obj.pid
            log(f"[启动] 降级默认启动，PID: {launched_pid}")
    else:
        p_obj = subprocess.Popen([wechat_path], cwd=os.path.dirname(wechat_path), shell=False)
        launched_pid = p_obj.pid
        log(f"[启动] 默认启动，PID: {launched_pid}")

    log("等待微信窗体渲染并就绪...")
    pid = launched_pid
    if pid:
        if not psutil.pid_exists(pid):
            log(f"警告: 启动的 PID {pid} 似乎不存在，尝试重新探测 PID...")
            pid = None
        else:
            log(f"已直接定位启动的微信进程 PID={pid}，无需二次扫描。")
            
    if not pid:
        pid = get_wechat_pid(timeout=45, instance_index=instance_index)
        
    if not pid:
        log("错误：启动超时，未在45秒内检测到对应PID或微信窗体")
        return None

    # ── 自动检测登录窗口并触发 UIA 点击「进入微信」 ──
    try:
        from src.uia.startup_flow.narrator import start_narrator, stop_narrator
        start_narrator()
        
        login_hwnd = None
        for i in range(20):
            def find_hwnd_cb(h, _):
                if win32gui.IsWindowVisible(h):
                    _, w_pid = win32process.GetWindowThreadProcessId(h)
                    if w_pid == pid:
                        cls = win32gui.GetClassName(h)
                        if "WeChatLoginWndForPC" in cls or "Qt51514QWindowIcon" in cls:
                            from src.uia.startup_flow.utils import is_wechat_main_window
                            if not is_wechat_main_window(h):
                                nonlocal login_hwnd
                                login_hwnd = h
                                return False
                return True
            
            win32gui.EnumWindows(find_hwnd_cb, None)
            if login_hwnd:
                break
            time.sleep(0.5)

        if login_hwnd:
            log(f"检测到登录窗口句柄 hwnd={login_hwnd}，进行多开冲突检测并尝试点击登录...")
            
            # ── 新增：在检测到新登录窗口句柄后，立即关联到现有实例，防止 do_scan_sync 扫描到该窗口并注册为新分身 ──
            if instance_id:
                try:
                    from src.utils.instance_manager import InstanceManagerV2
                    manager = InstanceManagerV2.get_instance()
                    all_insts = manager.get_all_instances()
                    target_key = None
                    if instance_id in all_insts:
                        target_key = instance_id
                    else:
                        for k, v in all_insts.items():
                            if v.get("wxid") == instance_id:
                                target_key = k
                                break
                    if target_key:
                        manager.update_instance(target_key, {"window_handle": login_hwnd, "status": "login_pending"})
                        log(f"[自动关联] 已成功将实例 {target_key} 的窗口句柄更新为 {login_hwnd}，状态设为 login_pending")
                except Exception as e_upd:
                    log(f"[自动关联] 更新实例窗口句柄异常: {e_upd}")

            # ── 关键修复：在点击「进入微信」之前先注入 Hook，确保 sqlite3_key 调用能被拦截 ──
            # 原因：WeChat 在用户点击「进入微信」后立刻打开数据库，若 Hook 在点击后才注入则时机已晚。
            if pre_click_hook_cb:
                try:
                    pre_click_hook_cb(pid)
                except Exception as e_pre:
                    log(f"[Hook预注入] 提前注入异常: {e_pre}")

            from src.uia.startup_flow.login import smart_click_login_or_switch
            smart_click_login_or_switch(login_hwnd, instance_id=instance_id)
        else:
            log("未检测到待登录窗口，可能已自动登录或无需点击")
    except Exception as e_click:
        log(f"自动点击登录按钮异常: {e_click}")
    finally:
        try:
            stop_narrator()
        except Exception:
            pass

    return pid
