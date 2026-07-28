import os
import threading
import time
import win32process
from .utils import _log
from src.wechat_4x.key_service import KeyService

def inject_hook_for_login(login_hwnd):
    """
    如果当前没有配置密钥，在处理登录窗口（点击登录/扫码）前，先注入 Hook 监听开库事件
    """
    hex_key = os.environ.get("WECHAT_4X_KEY_HEX") or os.environ.get("WCDB_HEX_KEY")
    if not hex_key:
        try:
            _, pid = win32process.GetWindowThreadProcessId(login_hwnd)
            if pid:
                _log("Hook", f"检测到登录窗口 (PID: {pid})，正在尝试注入 Native Hook...")
                key_service = KeyService()
                if key_service.initialize_hook(pid):
                    _log("Hook", "注入 Native Hook 成功，启动后台密钥监听线程...")
                    
                    def async_poll_key():
                        try:
                            t_start = time.time()
                            captured_key = None
                            while time.time() - t_start < 120:
                                key = key_service.poll_key_data()
                                if key and len(key) == 64:
                                    captured_key = key
                                    break
                                # 打印并清理日志消息
                                msg, _ = key_service.get_status_message()
                                while msg:
                                    print(f"[Hook后台] {msg}")
                                    msg, _ = key_service.get_status_message()
                                time.sleep(0.2)
                            
                            if captured_key:
                                print(f"[Hook后台] 🎉 成功自动捕获微信密钥: {captured_key[:6]}******{captured_key[-6:]}")
                                from src.utils.wechat_key_store import persist_wechat_key
                                persist_wechat_key(captured_key)
                            else:
                                print("[Hook后台] ❌ 未能捕获到微信密钥，超时退出")
                        except Exception as ex:
                            print(f"[Hook后台] 线程发生异常: {ex}")
                        finally:
                            key_service.cleanup_hook()
                            
                    threading.Thread(target=async_poll_key, daemon=True).start()
                else:
                    err = key_service.get_last_error()
                    _log("Hook", f"❌ 注入 Native Hook 失败: {err}")
        except Exception as hook_err:
            _log("Hook", f"⚠️ 注入 Hook 流程异常: {hook_err}")

def check_and_restart_wechat_if_needed(state, result):
    """
    检查是否启用增强版 4x 驱动且本地未配置密钥。如果是，且微信已在运行，强制重启微信以进行 Hook 提取密钥
    """
    use_enhanced = os.environ.get("WECHAT_ENHANCED_4X", "0") == "1"
    if not use_enhanced:
        try:
            from src.utils.config_cache import config_cache
            use_enhanced = config_cache.get("enable_enhanced_4x", False)
        except Exception:
            pass
            
    if use_enhanced and state["running"]:
        hex_key = os.environ.get("WECHAT_4X_KEY_HEX") or os.environ.get("WCDB_HEX_KEY")
        if not hex_key:
            # ── 智能优化：尝试在强杀前通过运行中的进程推断 wxid 并从本地 KeyStore 加载密钥 ──
            try:
                from .state import _enum_wechat_windows
                wins = _enum_wechat_windows()
                if wins:
                    hwnd = wins[0][0]
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    if pid:
                        import psutil
                        import re
                        from src.utils.wechat_key_store import clean_wxid, get_persisted_wechat_key
                        
                        proc = psutil.Process(pid)
                        inferred_wxid = None
                        for f in proc.open_files():
                            path = f.path
                            match = re.search(r"[\\/](?:WeChat Files|WeXin Files|xwechat_files|WeChatFiles)[\\/]([^\\/]+)", path, re.IGNORECASE)
                            if match:
                                val = match.group(1)
                                if val and val.lower() not in {"all users", "all_users", "backup", "finderlive", "common", "global"}:
                                    inferred_wxid = clean_wxid(val)
                                    break
                        
                        if inferred_wxid:
                            cached_key = get_persisted_wechat_key(inferred_wxid)
                            if cached_key:
                                os.environ["WECHAT_4X_KEY_HEX"] = cached_key
                                os.environ["WCDB_HEX_KEY"] = cached_key
                                _log("启动", f"🎉 [无感热启动] 通过本地 KeyStore 成功为运行中的 {inferred_wxid} 加载了密钥，跳过强退重启！")
                                return state
            except Exception as infer_err:
                _log("启动", f"⚠️ 尝试在启动前无感推断并加载密钥发生异常: {infer_err}")

            # 只有当本地没有任何可用密钥缓存时，才执行强退重启提取方案
            hex_key = os.environ.get("WECHAT_4X_KEY_HEX") or os.environ.get("WCDB_HEX_KEY")
            if not hex_key:
                _log("启动", "⚠️ 开启了增强型驱动且本地未发现 WCDB 密钥，同时微信已在运行。")
                _log("启动", "准备强制关闭并重启微信，以便在登录时自动注入 Hook 并提取密钥...")
                from .process import exit_wechat_via_tray, kill_wechat, wait_wechat_exit
                from .state import detect_wechat_state
                
                _cached_exe_path = state.get("exe_path")
                if not exit_wechat_via_tray():
                    kill_wechat()
                wait_wechat_exit()
                state = detect_wechat_state()
                if not state.get("exe_path") and _cached_exe_path:
                    state["exe_path"] = _cached_exe_path
                result["wechat_restarted"] = True
    return state
