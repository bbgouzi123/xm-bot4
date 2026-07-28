import os
import time
import threading
from .key_service import KeyService
from .launcher_helper import find_wechat_path, kill_wechat, get_wechat_pid, clean_and_launch_wechat

class WeChatHookController:
    """
    负责完整的微信进程生命周期管理、窗体探测与密钥挂钩逻辑
    取代原先依赖 Frida 和 UIAutomation 的脆弱做法
    """
    def __init__(self):
        self.key_service = KeyService()

    def find_wechat_path(self) -> str:
        return find_wechat_path()

    def kill_wechat(self):
        kill_wechat()

    def get_wechat_pid(self, timeout=25, instance_index=None):
        return get_wechat_pid(timeout=timeout, instance_index=instance_index)

    def auto_get_key(self, timeout=90, instance_id=None, log_cb=None):
        """自动化挂钩提取密钥主流程"""
        logs = []
        def log(msg):
            print(f"[*] {msg}")
            logs.append(msg)
            if log_cb:
                try:
                    log_cb(msg)
                except Exception:
                    pass

        wechat_path = self.find_wechat_path()
        if not wechat_path:
            log("错误：未在当前系统中找到微信安装路径")
            return None

        # ── 智能隔离与精确定位 ──
        instance_index = None
        if instance_id:
            try:
                from src.utils.instance_manager import InstanceManagerV2
                import re
                manager = InstanceManagerV2.get_instance()
                for inst_id, inst_data in manager.get_all_instances().items():
                    if inst_id == instance_id or inst_data.get("wxid") == instance_id:
                        m = re.search(r"\d+", inst_id)
                        if m:
                            instance_index = int(m.group(0))
                            break
            except Exception as e_inst:
                log(f"解析实例索引异常: {e_inst}")

        # ── 维护窗口：屏蔽心跳守护的假性风控告警 ──────────────────────────────────────────────
        # 密钥提取会主动 kill 微信进程再重启，心跳守护会将此误判为「意外掉线」，
        # 60 秒后触发「致命风控！账号下线阻断」误报。用 uia_maintenance 标记整个提取周期，
        # 使心跳守护的 _loop 在 is_uia_maintenance_active() 期间自动跳过连接检测。
        try:
            from src.utils.uia_task_runner import uia_maintenance as _uia_maint
            _maintenance_ctx = _uia_maint("密钥提取：主动重启微信")
            _maintenance_ctx.__enter__()
        except Exception:
            _maintenance_ctx = None

        pid = None
        _pid_claimed = False
        try:
            # 调用抽离出去的进程清理与启动 & UIA 登录流程
            # 关键：把 initialize_hook 作为「点击前回调」传入，确保 Hook 在 WeChat 打开数据库之前注入
            from .wechat_key_monitor import claim_pid_exclusive, release_pid_exclusive

            def _pre_click_inject(inject_pid: int):
                nonlocal _pid_claimed
                if not self.key_service.initialize_hook(inject_pid):
                    err = self.key_service.get_last_error()
                    if "Hook已经初始化" in err or "already initialized" in err:
                        log(f"Hook 已经针对 PID {inject_pid} 初始化（后台监控已提前注入），直接进入轮询提取状态。")
                        # Hook 已由 monitor 装好，立即 claim 防止 wcdb_key_extractor 卸掉它
                        claim_pid_exclusive(inject_pid)
                        _pid_claimed = True
                    else:
                        log(f"[Hook预注入] 注入失败: {err}，将在点击登录后继续尝试。")
                else:
                    log(f"[Hook预注入] ✅ 成功在点击登录前完成 Hook 注入 (PID={inject_pid})")
                    # ❶ Hook 注入成功，立即宣告独占，封死 wcdb_key_extractor 的竞争窗口
                    # 时序关键：必须在点击"进入微信"之前完成，否则 wcdb_key_extractor
                    # 会在点击后 2s 超时并在 finally 里调 _x_term_session() 卸掉 Hook
                    claim_pid_exclusive(inject_pid)
                    _pid_claimed = True

            pid = clean_and_launch_wechat(wechat_path, instance_index, instance_id, log,
                                          pre_click_hook_cb=_pre_click_inject)
            if not pid:
                return None

            # 兜底：若 _pre_click_inject 因异常未能 claim，此处补充 claim
            if not _pid_claimed:
                claim_pid_exclusive(pid)
                _pid_claimed = True

            log(f"检测到微信窗口 (PID: {pid})，Hook 已预注入，进入轮询提取状态...")
            log("提示：如果微信处于未登录界面，请在弹出的微信窗口中完成登录/扫码确认。")
            start_time = time.time()
            final_key = None
            _last_qrcode_log_time = 0.0  # 避免扫码等待日志刷屏

            try:
                while time.time() - start_time < timeout:
                    key = self.key_service.poll_key_data()
                    if key and len(key) == 64:
                        log(f"成功截获微信数据库 AES 密钥: {key}")
                        final_key = key
                        break

                    # ✅ [修复] 扫码等待时动态重置超时计时器，防止用户扫码期间 Hook 被提前卸载。
                    # 原因：如果用户扫码耗时超过 90s，主循环超时触发 cleanup_hook() 卸载 Hook，
                    # 扫码登录瞬间的 sqlite3_key 调用就无法被截获。
                    # 修复：每轮检测当前 pid 对应的 WeChatLoginWndForPC 是否仍可见，
                    # 若是说明用户还在扫码/登录中，重置 start_time 使计时器归零。
                    try:
                        import win32gui as _w32g
                        import win32process as _w32p
                        _login_wnd_visible = False
                        _hwnd_scan = 0
                        while True:
                            _hwnd_scan = _w32g.FindWindowEx(0, _hwnd_scan, "WeChatLoginWndForPC", None)
                            if not _hwnd_scan:
                                break
                            if _w32g.IsWindowVisible(_hwnd_scan):
                                _, _wnd_pid = _w32p.GetWindowThreadProcessId(_hwnd_scan)
                                if _wnd_pid == pid:
                                    _login_wnd_visible = True
                                    break
                        if _login_wnd_visible:
                            start_time = time.time()  # 重置超时，用户仍在扫码中
                            now = time.time()
                            if now - _last_qrcode_log_time >= 5.0:
                                log(f"[扫码等待] 检测到登录窗口仍可见 (PID={pid})，超时计时器已重置，等待用户扫码...")
                                _last_qrcode_log_time = now
                    except Exception:
                        pass

                    # 💡 轮询兜底：wechat_key_monitor 后台线程可能先调用 poll_key_data() 消费掉 DLL buffer，
                    # 把密钥写入 last_key（无 wxid 绑定），导致前台 poll_key_data() 永远读不到数据。
                    # 修复：从 last_key 兜底读取，但必须先通过 verify_wechat_key(key, instance_id) 校验，
                    # 确保密钥确实能解密目标账号的数据库，防止跨账号密钥（如 nudef 的 92b1cf...）污染。
                    # ① 目标账号密钥通过校验 → 用它，精确绑定到 instance_id
                    # ② 其他账号密钥校验失败 → 跳过，继续等 poll_key_data() 或下次兜底
                    try:
                        from src.utils.wechat_key_store import get_persisted_wechat_key, KEYS_FILE_PATH, verify_wechat_key, persist_wechat_key
                        import json as _json
                        persisted_key = get_persisted_wechat_key(instance_id)
                        if persisted_key and len(persisted_key) == 64:
                            log(f"通过本地 KeyStore(instance_id={instance_id})检测到已截获微信密钥: {persisted_key[:6]}******")
                            final_key = persisted_key
                            break
                        # last_key 兜底（含跨账号隔离校验）
                        if os.path.exists(KEYS_FILE_PATH):
                            try:
                                with open(KEYS_FILE_PATH, "r", encoding="utf-8") as _f:
                                    _kdata = _json.load(_f)
                                _last_key = _kdata.get("last_key") if isinstance(_kdata, dict) else None
                                if _last_key and len(_last_key) == 64 and instance_id:
                                    if verify_wechat_key(_last_key, instance_id):
                                        # 校验通过：这个 last_key 确实能解密目标账号数据库，是正确的密钥
                                        log(f"通过 last_key 兜底(已校验可解密 {instance_id} 数据库)检测到密钥: {_last_key[:6]}******")
                                        persist_wechat_key(_last_key, instance_id)
                                        final_key = _last_key
                                        break
                                    # 校验失败：这是其他账号的密钥（如 nudef），忽略，不污染当前账号
                            except Exception:
                                pass
                    except Exception:
                        pass

                    # 打印 DLL 底层日志（用于监控内部扫码、就绪等状态）
                    msg, _ = self.key_service.get_status_message()
                    while msg:
                        log(f"[底层状态] {msg}")
                        msg, _ = self.key_service.get_status_message()

                    time.sleep(0.2)
            finally:
                log("正在清理并卸载挂钩...")
                self.key_service.cleanup_hook()
                # ❶ 释放对该 PID 的独占权，允许 monitor 恢复正常监控
                if _pid_claimed and pid:
                    release_pid_exclusive(pid)

            if not final_key:
                log("错误：等待密钥提取超时（90秒）。")
            return final_key

        finally:
            # 解除维护窗口：恢复心跳守护的正常连接检测
            if _maintenance_ctx is not None:
                try:
                    _maintenance_ctx.__exit__(None, None, None)
                except Exception:
                    pass

    def start_auto_key_monitor(self):
        """启动后台守护线程，在微信首次启动或登录时静默截获密钥，避免重启微信"""
        from .wechat_key_monitor import run_auto_key_monitor_loop
        t = threading.Thread(target=run_auto_key_monitor_loop, args=(self.key_service,), daemon=True, name="WeChatAutoKeyMonitorThread")
        t.start()

if __name__ == "__main__":
    controller = WeChatHookController()
    controller.auto_get_key()
