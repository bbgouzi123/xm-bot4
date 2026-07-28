import time
import threading
import psutil
try:
    import win32gui
    import win32process
except ImportError:
    win32gui = None
    win32process = None

from src.utils.wechat_key_store import persist_wechat_key

import sys

# ── 互斥独占集合（解决问题 ❶ & ❺）────────────────────────────────────────────
# auto_get_key 运行期间会将目标 PID 写入此集合（claim），完成后释放（release）。
# wechat_key_monitor 在所有关键操作（initialize_hook / poll_key_data / cleanup_hook）
# 之前必须检查此集合，若 PID 被独占则完全跳过，防止：
#   ❶ DLL buffer 被 monitor 提前消费，导致 auto_get_key 拿不到密钥（90s 超时）
#   ❺ monitor finally 块卸载 auto_get_key 刚安装好的 Hook
#
# 🌟 关键修复：绑定到全局唯一的 sys 模块，防止打包等多重导入下锁不共享、状态割裂的问题。
if not hasattr(sys, "_xm_bot4_exclusive_pids"):
    sys._xm_bot4_exclusive_pids = set()

_EXCLUSIVE_LOCK = threading.Lock()


def claim_pid_exclusive(pid: int):
    """auto_get_key 开始前调用：宣告对该 PID 的独占权，monitor 将完全跳过此 PID。"""
    with _EXCLUSIVE_LOCK:
        sys._xm_bot4_exclusive_pids.add(pid)
    print(f"[自动密钥监控] 🔒 PID={pid} 已被 auto_get_key 独占，monitor 暂停对该进程的所有操作")


def release_pid_exclusive(pid: int):
    """auto_get_key 完成后调用：释放对该 PID 的独占权，monitor 恢复正常监控。"""
    with _EXCLUSIVE_LOCK:
        sys._xm_bot4_exclusive_pids.discard(pid)
    print(f"[自动密钥监控] 🔓 PID={pid} 独占已释放，monitor 恢复正常监控")


def _is_exclusive(pid: int) -> bool:
    """判断该 PID 当前是否被 auto_get_key 独占。"""
    with _EXCLUSIVE_LOCK:
        return pid in sys._xm_bot4_exclusive_pids


def run_auto_key_monitor_loop(key_service):
    """启动微信数据库密钥后台静默监听循环"""
    if win32gui is None or win32process is None:
        print("[自动密钥监控] 当前环境不支持 Win32 API，自动跳过密钥后台监听。")
        return
    print("[自动密钥监控] 启动微信数据库密钥后台静默监听...")
    
    hooked_pids = set()
    
    while True:
        # 找到拥有可见微信窗口的主进程 PID
        main_pids = set()
        for cls_name in ("WeChatMainWndForPC", "WeChatLoginWndForPC"):
            hwnd = 0
            while True:
                try:
                    hwnd = win32gui.FindWindowEx(0, hwnd, cls_name, None)
                    if not hwnd:
                        break
                    if win32gui.IsWindowVisible(hwnd):
                        _, pid = win32process.GetWindowThreadProcessId(hwnd)
                        if pid > 0:
                            main_pids.add(pid)
                except Exception:
                    break

        wx_pids = []
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                name = proc.info['name']
                pid = proc.info['pid']
                if name and name.lower() in ['wechat.exe', 'weixin.exe']:
                    # 必须是拥有主窗口/登录窗口的微信主进程，排除小程序、内置浏览器等非关键子进程
                    if pid in main_pids:
                        wx_pids.append(pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
                
        if not wx_pids:
            hooked_pids.clear()
            time.sleep(4)
            continue
            
        for pid in wx_pids:
            if pid in hooked_pids:
                continue

            # ❶ 若 auto_get_key 已独占该 PID，跳过本轮所有操作（注入、poll、cleanup）
            if _is_exclusive(pid):
                continue

            print(f"[自动密钥监控] 检测到微信启动 (PID: {pid})，正在静默挂钩以获取密钥...")
            time.sleep(1.5)

            # 注入前再次检查独占状态（sleep 期间 auto_get_key 可能已启动）
            if _is_exclusive(pid):
                print(f"[自动密钥监控] ⏸ PID={pid} 在等待期间被 auto_get_key 独占，本轮跳过注入")
                continue

            if not key_service.initialize_hook(pid):
                err_msg = key_service.get_last_error()
                if "Hook已经初始化" in err_msg or "already initialized" in err_msg:
                    print(f"[自动密钥监控] Hook 已经针对 PID {pid} 初始化，直接进入轮询提取状态。")
                else:
                    print(f"[自动密钥监控] 注入 PID {pid} 失败: {err_msg}。将在 15 秒后重试此 PID。")
                    hooked_pids.add(pid)
                    def unblock_pid(p):
                        time.sleep(15)
                        if p in hooked_pids:
                            hooked_pids.discard(p)
                    threading.Thread(target=unblock_pid, args=(pid,), daemon=True).start()
                    continue

            print(f"[自动密钥监控] 成功注入 PID {pid}，开始后台监听扫码/登录密钥...")
            hooked_pids.add(pid)
            
            start_time = time.time()
            success = False
            try:
                while time.time() - start_time < 90:
                    try:
                        p = psutil.Process(pid)
                        if not p.is_running():
                            print(f"[自动密钥监控] 微信进程 (PID: {pid}) 已退出，中止本次监听。")
                            break
                    except Exception:
                        print(f"[自动密钥监控] 微信进程 (PID: {pid}) 异常丢失，中止本次监听。")
                        break
                    
                    # 【优化】检查微信是否处于非认证（扫码/登录 pending）状态
                    is_pending = False
                    hwnd = 0
                    while True:
                        try:
                            hwnd = win32gui.FindWindowEx(0, hwnd, "WeChatLoginWndForPC", None)
                            if not hwnd:
                                break
                            if win32gui.IsWindowVisible(hwnd):
                                _, wnd_pid = win32process.GetWindowThreadProcessId(hwnd)
                                if wnd_pid == pid:
                                    is_pending = True
                                    break
                        except Exception:
                            break

                    # ❶ poll 前检查独占：若 auto_get_key 已接管此 PID，立即退出让其独占消费
                    if _is_exclusive(pid):
                        print(f"[自动密钥监控] ⏸ PID={pid} 已被 auto_get_key 独占，monitor 退出轮询，交还 DLL buffer")
                        break

                    key = key_service.poll_key_data()
                    if key and len(key) == 64:
                        print(f"[自动密钥监控] 🎉 成功静默截获微信 AES 密钥: {key[:6]}******{key[-6:]}")
                        
                        # ✅ 核心修复：通过 PID 查询 InstanceManagerV2 得到准确的 wxid，
                        # 直接写 persist_wechat_key(key, wxid) 到专属条目，
                        # 而不只写 last_key 全局字段导致 auto_get_key 无法精确匹配账号。
                        # 查找路径：PID → 所有 hwnd（win32process）→ InstanceManager 条目 → wxid
                        # ✅ [方案二修复] 带指数退避重试的 PID→wxid 精准绑定
                        # 根因：登录按钮点击后微信从登录窗口切换到主窗口，窗口句柄发生变化，
                        # InstanceManager 中记录的仍是旧登录窗口句柄，首次 EnumWindows 匹配失败。
                        # 修复策略：
                        #   A. 正向匹配（原逻辑）：EnumWindows 枚举 PID 下所有句柄与 InstanceManager 对比
                        #   B. 反向匹配（新增）：对 login_pending 实例，反查其存储的句柄是否归属该 PID，
                        #      覆盖"主窗口句柄尚未写回 InstanceManager"的过渡时间窗口
                        # 两种策略轮流执行，最多重试 5 次（累计等待约 7s），全部失败才降级写 last_key。
                        _wxid_for_pid = None
                        _MAX_WXID_RETRY = 5
                        _RETRY_DELAYS = [0, 1.0, 1.5, 2.0, 2.5]
                        for _retry in range(_MAX_WXID_RETRY):
                            if _retry > 0:
                                _wait = _RETRY_DELAYS[_retry] if _retry < len(_RETRY_DELAYS) else 2.5
                                print(f"[自动密钥监控] PID→wxid 第 {_retry} 次查询未果，{_wait:.1f}s 后重试（共 {_MAX_WXID_RETRY} 次）...")
                                time.sleep(_wait)
                            try:
                                from src.utils.instance_manager import InstanceManagerV2
                                import win32process as _w32p_km
                                import win32gui as _w32g_km
                                _mgr = InstanceManagerV2.get_instance()
                                _all_insts = _mgr.get_all_instances()

                                # 策略 A：枚举 PID 下所有窗口句柄，与 InstanceManager 正向匹配
                                _pid_hwnds = set()
                                def _enum_cb_km(hwnd, _):
                                    try:
                                        _, _wnd_pid = _w32p_km.GetWindowThreadProcessId(hwnd)
                                        if _wnd_pid == pid:
                                            _pid_hwnds.add(hwnd)
                                    except Exception:
                                        pass
                                _w32g_km.EnumWindows(_enum_cb_km, None)
                                for _inst_id, _inst_data in _all_insts.items():
                                    _inst_hwnd = _inst_data.get("window_handle")
                                    if _inst_hwnd and int(_inst_hwnd) in _pid_hwnds:
                                        _candidate = _inst_data.get("wxid") or (_inst_id if _inst_id.startswith("wxid_") else None)
                                        if _candidate:
                                            _wxid_for_pid = _candidate
                                        break

                                # 策略 B：对 login_pending/online 实例，反查其 window_handle 是否属于目标 PID
                                # 覆盖登录窗口→主窗口过渡期"主窗口句柄尚未写回 InstanceManager"的盲区
                                if not _wxid_for_pid:
                                    for _inst_id, _inst_data in _all_insts.items():
                                        if _inst_data.get("status") not in ("login_pending", "online"):
                                            continue
                                        _inst_hwnd = _inst_data.get("window_handle")
                                        if not _inst_hwnd:
                                            continue
                                        try:
                                            _, _h_pid = _w32p_km.GetWindowThreadProcessId(int(_inst_hwnd))
                                            if _h_pid == pid:
                                                _candidate = _inst_data.get("wxid") or (_inst_id if _inst_id.startswith("wxid_") else None)
                                                if _candidate:
                                                    _wxid_for_pid = _candidate
                                                    break
                                        except Exception:
                                            pass
                            except Exception as _e_inst:
                                print(f"[自动密钥监控] PID→wxid 第 {_retry + 1} 次查询异常: {_e_inst}")
                            if _wxid_for_pid:
                                break

                        if _wxid_for_pid:
                            persist_wechat_key(key, _wxid_for_pid)
                            print(f"[自动密钥监控] ✅ 密钥已精确绑定到 wxid={_wxid_for_pid}")
                        else:
                            # 经全部重试仍未匹配：降级写 last_key，打印警告供排查
                            persist_wechat_key(key)
                            print(f"[自动密钥监控] ⚠️ 经 {_MAX_WXID_RETRY} 次重试仍未能查到 PID={pid} 对应 wxid，"
                                  f"密钥写入 last_key 待后续绑定（可能是新账号首次登录）")
                        
                        success = True
                        break
                    
                    if is_pending:
                        # 【优化】在非认证/登录 pending 状态下，降低轮询频率至 2.0 秒以节省 CPU 并稳定生命周期
                        time.sleep(2.0)
                    else:
                        time.sleep(0.5)
            finally:
                # ❺ cleanup 前检查独占：若 PID 已被 auto_get_key 接管，跳过卸载
                # 防止 monitor finally 卸掉 auto_get_key 刚安装好的 Hook（问题❺）
                if _is_exclusive(pid):
                    print(f"[自动密钥监控] ⏸ PID={pid} 已被 auto_get_key 独占，跳过 cleanup（由 auto_get_key finally 负责卸载）")
                else:
                    print(f"[自动密钥监控] 正在清理并卸载 PID {pid} 的挂钩...")
                    try:
                        key_service.cleanup_hook()
                    except Exception:
                        pass
                    
            if success:
                return
                
        time.sleep(4)
