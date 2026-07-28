"""微信登录界面处理（多开智能决策版）。

决策规则：已有主界面微信 → 点「切换账号」；否则 → 点「进入微信」；
按钮均不可用 → 等待扫码。扫码/快捷登录完成后自动平铺所有窗口消除重叠。
"""

import time
import ctypes
import threading
import win32api
import win32gui

from .utils import _log, _random_sleep
from .window_ops import force_focus_window, _activate_taskbar_window
from .state import _enum_wechat_windows
from .login_tile import get_existing_main_hwnds, tile_windows_after_login
from .login_wait import _notify_qrcode

try:
    import uiautomation as uia
except ImportError:
    uia = None


# ──────────────────────────────────────────────────────────────
# 内部工具
# ──────────────────────────────────────────────────────────────

def _click_at_absolute(x: int, y: int):
    """绝对坐标单击（反风控：SetCursorPos + mouse_event，恢复鼠标位置）"""
    old = win32api.GetCursorPos()
    win32api.SetCursorPos((x, y))
    _random_sleep(0.05, 0.1)
    ctypes.windll.user32.mouse_event(2, 0, 0, 0, 0)   # LEFTDOWN
    time.sleep(0.05)
    ctypes.windll.user32.mouse_event(4, 0, 0, 0, 0)   # LEFTUP
    _random_sleep(0.05, 0.1)
    win32api.SetCursorPos(old)


from .login_wait import (
    _scan_login_window_buttons,
    _has_active_main_wechat,
    _decide_login_action
)


# ──────────────────────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────────────────────


def perform_smart_login_click(hwnd: int, buttons: dict, action: str, instance_id: str = None) -> bool:
    """集中处理 '进入微信' vs '切换账号' 物理点击逻辑，防止时序竞态与坐标漂移"""
    success = False
    
    # 物理置顶并显示窗口，确保坐标点击准确
    try:
        ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 3) # HWND_TOPMOST
    except Exception:
        pass
        
    if action == 'enter' and buttons.get("enter"):
        pos = buttons["enter"]
        
        # 🌟 [捕获时序抢救] 
        # ⚠️ [重要修复] 守护线程绝对不能用 WcdbKeyExtractor.get_key()！
        # 原因：get_key() 的 finally 块会调用 _cleanup_hook() 卸载 Hook，
        # 而前台 auto_get_key 主流程用的是同一个 DLL 单例，守护线程 30s 超时后一卸载，
        # 前台就永远轮询不到密钥，导致 90s 后报超时。
        # 修复：守护线程只做轻量 poll + persist，不持有任何 cleanup 权。
        # cleanup 权完全交给前台 auto_get_key 的 finally 块统一执行。
        try:
            import win32process
            import threading
            from src.wechat_4x.wcdb_key_extractor import get_wcdb_key_extractor
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            _enter_nick = buttons.get("nickname") or ""
            if pid:
                # 清除冷却记录，防止 WcdbKeyExtractor 的冷却机制拦截前台轮询
                extractor = get_wcdb_key_extractor()
                if hasattr(extractor, "_failed_pids") and pid in extractor._failed_pids:
                    del extractor._failed_pids[pid]
                
                _guard_instance_id = instance_id
                def _guard_worker(_guard_pid=pid, _nick=_enter_nick, _inst_id=_guard_instance_id):
                    """只读观察者：监听 wechat_keys.json[instance_id] 专属条目，判断 auto_get_key 是否写入成功。
                    
                    ⚠️ 架构原则（三禁）：
                    ① 严禁调用 poll_key_data()：DLL buffer 是一次性消费，守护线程抢读会导致
                       主流程 auto_get_key 读不到，造成 90s 超时失败。
                    ② 严禁监听 last_key：last_key 是全局共享字段，nudef 等其他账号的
                       wechat_key_monitor 每隔 0.5s 就会写入自己的密钥，守护线程无法区分
                       "哪个 last_key 变化属于目标账号"，必然导致跨账号密钥污染。
                    ③ 严禁写入任何密钥：守护线程是纯只读观察者，写入权完全归 auto_get_key
                       的主流程所有（auto_get_key 拿到密钥后会调用 persist_wechat_key(key, instance_id)）。
                    
                    正确做法：只监听 wechat_keys.json[instance_id] 这个专属字段，
                    初始化时快照当前值（可能是空或脏数据），仅当出现"与初始值不同的新值"时才视为提取成功。
                    """
                    try:
                        import os as _os
                        import time as _t
                        import json as _json
                        from src.utils.wechat_key_store import KEYS_FILE_PATH
                        
                        def _read_inst_key():
                            """只读取目标 wxid 的专属密钥条目，忽略 last_key"""
                            if not _inst_id:
                                return None
                            try:
                                if _os.path.exists(KEYS_FILE_PATH):
                                    with open(KEYS_FILE_PATH, "r", encoding="utf-8") as _f:
                                        _kdata = _json.load(_f)
                                    k = _kdata.get(_inst_id) if isinstance(_kdata, dict) else None
                                    return k if (k and len(k) == 64) else None
                            except Exception:
                                return None
                        
                        # ✅ 关键：启动时快照当前值，确保只响应"新变化"，不触发历史遗留数据
                        # 场景：wechat_keys.json 里可能已有 wxid_4fmddgv0yhee22 的脏密钥
                        # 若不快照直接比较 None，任何已存在的值都会被当作"新密钥"误触发
                        _initial_key = _read_inst_key()
                        _log("登录", f"[密钥守护] PID={_guard_pid}(wxid={_inst_id}) 启动只读监听，"
                             f"初始快照: {'已有(等待被auto_get_key刷新)' if _initial_key else '空(等待写入)'}")
                        
                        deadline = _t.time() + 90.0
                        while _t.time() < deadline:
                            current_key = _read_inst_key()
                            
                            if current_key and current_key != _initial_key:
                                # auto_get_key 已成功写入新密钥，提取完成
                                _log("登录", f"[密钥守护] ✅ 检测到 wxid={_inst_id} 专属密钥已由 auto_get_key 成功写入")
                                return
                            
                            if not current_key and _initial_key:
                                # 脏数据被 KeyStore 自动清除（校验失败触发清理），重置快照，等待真实新值
                                _log("登录", f"[密钥守护] 初始脏密钥已被清除，重置快照，继续等待 auto_get_key 写入正确密钥...")
                                _initial_key = None
                            
                            _t.sleep(0.5)
                        
                        _log("登录", f"[密钥守护] ⚠️ 90s 内未检测到 wxid={_inst_id} 密钥写入，守护超时退出")
                    except Exception as e_g:
                        _log("登录", f"[密钥守护] 守护线程异常: {e_g}")
                        
                threading.Thread(target=_guard_worker, name=f"wcdb-login-guard-{pid}", daemon=True).start()
        except Exception as e_pre:
            _log("登录", f"[密钥守护] 初始化捕获守护失败: {e_pre}")
            
        _log("登录", f"▶ 物理点击：点击「进入微信」，位置: {pos}")
        _random_sleep(0.3, 0.6)
        _click_at_absolute(pos[0], pos[1])
        success = True
    elif action == 'switch' and buttons.get("switch"):
        pos = buttons["switch"]
        _log("登录", f"▶ 物理点击：点击「切换账号」，位置: {pos}")
        _random_sleep(0.3, 0.6)
        _click_at_absolute(pos[0], pos[1])
        success = True

    try:
        ctypes.windll.user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 3) # HWND_NOTOPMOST
    except Exception:
        pass

    return success


def click_login_enter_button(hwnd: int) -> bool:
    """自动检测微信登录窗口中的“进入微信/一键登录/登录”按钮并模拟点击（带置顶与反风控）"""
    from .window_ops import force_focus_window, _activate_taskbar_window
    
    _activate_taskbar_window(hwnd)
    force_focus_window(hwnd)

    _log("登录", f"开始为窗口 hwnd={hwnd} 扫描「进入微信」按钮...")
    buttons = _scan_login_window_buttons(hwnd, timeout=5.0)
    
    if buttons.get("enter"):
        return perform_smart_login_click(hwnd, buttons, 'enter')
    
    _log("登录", "未在登录窗口上发现可点击的进入按钮（可能需要扫码或已登录）")
    return False


def smart_click_login_or_switch(hwnd: int, instance_id: str = None) -> bool:
    """智能判断当前微信是否已登录/重复。如果重复或昵称未知冲突，则点击「切换账号」；否则点击「进入微信」"""
    from .window_ops import force_focus_window, _activate_taskbar_window
    
    _activate_taskbar_window(hwnd)
    force_focus_window(hwnd)

    _log("登录", f"开始为窗口 hwnd={hwnd} 执行智能登录决策与点击...")
    buttons = _scan_login_window_buttons(hwnd, timeout=5.0)
    
    # 智能决定是点击 enter 还是 switch 还是扫码
    action = _decide_login_action(hwnd, buttons, target_instance_id=instance_id)
    
    success = perform_smart_login_click(hwnd, buttons, action, instance_id=instance_id)
    if not success:
        _log("登录", f"▶ 智能决策：无需点击或没有对应按钮（决策 action: {action})")
        if action == 'qrcode':
            _log("登录", "📱 无快捷登录按钮，等待用户扫码")

            # ✅ [修复] 扫码场景同样需要预注入 Hook + 启动密钥守护线程。
            # 原因：perform_smart_login_click 内的守护逻辑只在 action=='enter' 时触发，
            # 扫码场景下 Hook 完全未注入，用户扫码登录的瞬间根本截获不到 sqlite3_key 调用。
            # 修复：此处为扫码场景单独注入 Hook，并启动 120s 守护线程（给用户足够的扫码时间）。
            try:
                import win32process as _w32p
                from src.wechat_4x.wcdb_key_extractor import get_wcdb_key_extractor
                _, _qr_pid = _w32p.GetWindowThreadProcessId(hwnd)
                if _qr_pid:
                    _extractor = get_wcdb_key_extractor()
                    # 清除该 PID 的冷却记录，防止被 5s/300s 冷却拦截
                    _extractor._failed_pids.pop(_qr_pid, None)

                    def _qrcode_guard_worker(_pid=_qr_pid):
                        """扫码场景：只 poll 不 cleanup，由前台 auto_get_key 统一清理"""
                        _log("登录", f"[密钥守护-扫码] 已为扫码等待进程 PID={_pid} 启动 120s 只读轮询守护线程")
                        try:
                            from src.wechat_4x.key_service import KeyService
                            from src.utils.wechat_key_store import persist_wechat_key
                            import time as _t
                            _ks = KeyService()
                            deadline = _t.time() + 120.0
                            while _t.time() < deadline:
                                key = _ks.poll_key_data()
                                if key and len(key) == 64:
                                    _log("登录", f"[密钥守护-扫码] 🎉 守护线程截获扫码密钥，写入持久化存储")
                                    persist_wechat_key(key)
                                    return
                                _t.sleep(0.15)
                            _log("登录", f"[密钥守护-扫码] 120s 超时退出（不 cleanup，由主流程统一清理）")
                        except Exception as _e:
                            _log("登录", f"[密钥守护-扫码] 守护提取密钥异常: {_e}")

                    threading.Thread(
                        target=_qrcode_guard_worker,
                        name=f"wcdb-qrcode-guard-{_qr_pid}",
                        daemon=True
                    ).start()
            except Exception as _e_qr:
                _log("登录", f"[密钥守护-扫码] 启动守护失败: {_e_qr}")

            _notify_qrcode(hwnd, buttons.get("nickname") or "微信")
            
    return success


# ──────────────────────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────────────────────

def handle_login_window(hwnd: int) -> int | None:
    """处理微信登录界面（多开智能决策版）。

    流程：
    1. 置顶登录窗口，确保用户可见
    2. 扫描所有关键按钮（进入微信 / 切换账号）
    3. 根据「系统中是否已有主界面微信」决定点哪个按钮
    4. 等待新主界面出现后立即重新平铺所有窗口，消除重叠

    Returns:
    ---
        新登录 of 微信主界面 hwnd，或 None（超时）
    """
    _log("登录", f"检测登录界面 hwnd={hwnd}")

    # ── 置顶登录窗口，确保可见 ──────────────────────────────────────────
    _activate_taskbar_window(hwnd)
    force_focus_window(hwnd)

    # ── 扫描登录窗口上的所有关键按钮 ────────────────────────────────────
    _log("登录", "正在扫描登录界面按钮...")
    buttons = _scan_login_window_buttons(hwnd, timeout=10.0)
    _log("登录",
         f"扫描结果: 昵称='{buttons['nickname']}' "
         f"进入微信={buttons['enter']} 切换账号={buttons['switch']}")

    # ── 多开智能决策 ─────────────────────────────────────────────────────
    action = _decide_login_action(hwnd, buttons, target_instance_id=None)

    # ── 记录当前已有的主界面 hwnd 集合（用于后续等待时排除，只等新出现的窗口）──
    existing_main_hwnds = get_existing_main_hwnds(exclude_login_hwnd=hwnd)
    _log("登录", f"现有主界面微信: {existing_main_hwnds}")

    # ── 执行决策 ─────────────────────────────────────────────────────────
    success = perform_smart_login_click(hwnd, buttons, action)

    if action == 'enter':
        if success:
            _log("登录", "已点击「进入微信」，等待主界面加载...")
            _random_sleep(2.0, 3.0)
        else:
            _log("登录", "一键登录点击失败或按钮不可用，退回扫码状态")
            _notify_qrcode(hwnd, buttons["nickname"])

    elif action == 'switch':
        if success:
            _log("登录", "已点击「切换账号」，等待扫码登录新账号...")
            _random_sleep(1.0, 1.5)
            _notify_qrcode(hwnd, buttons["nickname"])

            # ✅ [补充] 切换账号场景同样需要启动密钥守护线程。
            # 用户扫码后新账号数据库打开的瞬间必须截获 sqlite3_key 调用；
            # 截获后精确按 wxid 持久化，使 WcdbSessionMonitor 重启时可直接恢复。
            try:
                import win32process as _w32p
                _, _sw_pid = _w32p.GetWindowThreadProcessId(hwnd)
                _sw_nick = buttons.get("nickname") or ""
                if _sw_pid:
                    # ✅ 关键修复：在守护线程启动前声明 PID 独占，阻止 wcdb_key_extractor 的
                    # finally 块调用 _x_term_session() 卸载 Hook。
                    # 守护线程结束（成功截获或120s超时）后再 release，完整覆盖扫码等待期。
                    try:
                        from src.wechat_4x.wechat_key_monitor import claim_pid_exclusive, release_pid_exclusive
                        claim_pid_exclusive(_sw_pid)
                        _sw_claimed = True
                    except Exception:
                        _sw_claimed = False

                    def _switch_guard_worker(_pid=_sw_pid, _nick=_sw_nick, _claimed=_sw_claimed):
                        """切换账号场景：只 poll 不 cleanup，由后续主流程统一清理"""
                        _log("登录", f"[密钥守护-切换] 已为切换账号进程 PID={_pid} 启动 120s 只读轮询守护线程")
                        try:
                            from src.wechat_4x.key_service import KeyService
                            from src.utils.wechat_key_store import persist_wechat_key
                            import time as _t
                            _ks = KeyService()
                            deadline = _t.time() + 120.0
                            while _t.time() < deadline:
                                key = _ks.poll_key_data()
                                if key and len(key) == 64:
                                    _log("登录", f"[密钥守护-切换] 🎉 守护线程截获切换账号密钥")
                                    # 尝试匹配历史账号获取 wxid，实现按账号精确绑定
                                    _bind_wxid = None
                                    try:
                                        from src.crm.account_ops import list_accounts
                                        for _acct in list_accounts():
                                            if _acct.get("nickname") == _nick:
                                                _bind_wxid = _acct.get("wxid")
                                                break
                                    except Exception:
                                        pass
                                    persist_wechat_key(key, _bind_wxid)
                                    if _bind_wxid:
                                        _log("登录", f"[密钥守护-切换] 密钥已绑定到 wxid={_bind_wxid}")
                                    else:
                                        _log("登录", "[密钥守护-切换] 未能匹配历史 wxid，密钥已写入 last_key")
                                    return
                                _t.sleep(0.15)
                            _log("登录", f"[密钥守护-切换] 120s 超时退出（扫码未完成或密钥未截获）")
                        except Exception as _e:
                            _log("登录", f"[密钥守护-切换] 守护提取密钥异常: {_e}")
                        finally:
                            if _claimed:
                                try:
                                    from src.wechat_4x.wechat_key_monitor import release_pid_exclusive
                                    release_pid_exclusive(_pid)
                                except Exception:
                                    pass

                    threading.Thread(
                        target=_switch_guard_worker,
                        name=f"wcdb-switch-guard-{_sw_pid}",
                        daemon=True
                    ).start()
            except Exception as _e_sw:
                _log("登录", f"[密钥守护-切换] 启动守护失败: {_e_sw}")
        else:
            _log("登录", "切换账号按钮点击失败，等待用户扫码")
            _notify_qrcode(hwnd, buttons["nickname"])

    else:  # action == 'qrcode'
        _log("登录", "📱 等待用户手机扫码登录")
        _notify_qrcode(hwnd, buttons["nickname"])

    # ── 等待新主界面出现，并立即平铺所有窗口 ─────────────────────────────
    # 切换账号/扫码场景必须排除已有的主界面，才能检测到「新出现」的主界面 hwnd
    exclude_set = existing_main_hwnds if action in ('switch', 'qrcode') else set()
    from .login_wait import _wait_for_main_window
    return _wait_for_main_window(timeout=120, exclude_hwnds=exclude_set, login_hwnd=hwnd)
