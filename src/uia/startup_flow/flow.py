import os
import threading
from .utils import _log, _random_sleep
from .version import detect_wechat_version, is_wechat_version_compatible, format_version, _WECHAT_MAX_COMPATIBLE
from .env import check_qt_accessibility_injected, inject_qt_accessibility
from .state import detect_wechat_state
from .window_ops import force_focus_window, _ensure_window_on_screen
from .refresh import force_accessibility_refresh
from .toolbar import find_nav_toolbar
from .narrator import start_narrator, stop_narrator
from .process import exit_wechat_via_tray, kill_wechat, wait_wechat_exit
from .launch import launch_wechat
from .login import handle_login_window
from .hook_helper import inject_hook_for_login, check_and_restart_wechat_if_needed

_flow_lock = threading.Lock()

def _with_flow_lock(func):
    def wrapper(*args, **kwargs):
        with _flow_lock:
            return func(*args, **kwargs)
    return wrapper

@_with_flow_lock
def ensure_wechat_ready() -> dict:
    """
    智能启动流程主入口 — 确保微信就绪。
    """
    result = {
        "success": False,
        "hwnd": None,
        "root": None,
        "nav_toolbar": None,
        "env_was_injected": False,
        "wechat_restarted": False,
        "wechat_version": (0, 0, 0, 0),
        "version_compatible": True,
    }

    # ===== 0 WeChat version check =====
    version = detect_wechat_version()
    result["wechat_version"] = version
    if version != (0, 0, 0, 0):
        compatible = is_wechat_version_compatible(version)
        result["version_compatible"] = compatible
        if compatible:
            _log("version", f"WeChat {format_version(version)} (UIA compatible)")
        else:
            _log("version", f"WeChat {format_version(version)} — Qt Accessibility blocked in this version")
            _log("version", f"UIA automation will NOT work! Downgrade to {format_version(_WECHAT_MAX_COMPATIBLE[:3] + (0,))} or lower")
            _log("version", "Affected: nav bar, user info, session scan, message read/send")

    # ===== 1 env check =====
    env_already = check_qt_accessibility_injected()
    if env_already:
        _log("env", "QT_ACCESSIBILITY=1 present in registry")
        os.environ["QT_ACCESSIBILITY"] = "1"
        try:
            import ctypes
            ctypes.windll.kernel32.SetEnvironmentVariableW("QT_ACCESSIBILITY", "1")
        except Exception:
            pass
    else:
        _log("env", "First deploy, injecting QT_ACCESSIBILITY=1...")
        result["env_was_injected"] = True
        inject_qt_accessibility()

    # 强制让系统托盘显示所有图标，杜绝因折叠导致自动化工具找不到图标的问题
    from .env import ensure_tray_always_show_all_icons
    ensure_tray_always_show_all_icons()

    # ===== 2 detect wechat =====
    state = detect_wechat_state()
    state = check_and_restart_wechat_if_needed(state, result)
    _log("检测", f"微信状态 — 运行: {state['running']}, "
         f"主窗口: {len(state['main_windows'])}, "
         f"登录窗口: {len(state['login_windows'])}, "
         f"隐藏窗口: {len(state['hidden_windows'])}")

    has_multiple_instances = (len(state["main_windows"]) + len(state["login_windows"]) + len(state["hidden_windows"])) > 1

    # ---------- 分支处理 ----------

    wechat_usable = False
    hwnd = None
    root = None
    nav_toolbar = None

    if state["running"]:
        _log("检测", "微信已在运行，尝试直接识别...")
        
        if state["main_windows"]:
            hwnd = max(state["main_windows"], key=lambda x: x[1] * x[2])[0]
        elif state["hidden_windows"]:
            hwnd_hidden = state["hidden_windows"][0][0]
            _log("窗口", f"微信窗口不可见，优先通过托盘点击唤醒 hwnd={hwnd_hidden}")
            
            tray_activated = False
            try:
                from src.uia.retry import click_wechat_tray_icon
                tray_activated = click_wechat_tray_icon()
                if tray_activated:
                    _log("窗口", "✓ 已通过托盘图标唤醒微信")
                    _random_sleep(0.8, 1.2)
            except Exception:
                pass
            
            if not tray_activated:
                # 策略 2：使用微信官方快捷键 Ctrl+Alt+W 唤醒（最安全，无幽灵白屏副作用）
                _log("窗口", "托盘点击未成功，尝试微信快捷键 Ctrl+Alt+W 唤醒...")
                from .window_ops import simulate_wechat_show_hotkey
                simulate_wechat_show_hotkey(hwnd_hidden)
                _random_sleep(1.0, 1.5)
                
                # 检查快捷键是否生效
                import win32gui
                hotkey_ok = win32gui.IsWindowVisible(hwnd_hidden)
                if hotkey_ok:
                    _log("窗口", "✓ 已通过 Ctrl+Alt+W 快捷键唤醒微信")
                else:
                    # 策略 3（最终兜底）：ShowWindow 强制激活 + 白屏修复组合拳
                    _log("窗口", "快捷键唤醒未成功，最终降级使用 ShowWindow 激活（可能产生白屏）")
                    force_focus_window(hwnd_hidden)
                    _random_sleep(0.5, 1.0)
                    try:
                        from src.uia.retry import fix_white_screen_after_show
                        fix_white_screen_after_show(hwnd_hidden)
                    except Exception:
                        pass
                
            state2 = detect_wechat_state()
            if state2["main_windows"]:
                hwnd = max(state2["main_windows"], key=lambda x: x[1] * x[2])[0]
            else:
                state = state2

        if hwnd:
            # 优先通过托盘图标点击确保微信可见（安全：不会最小化、可清除白屏覆盖）
            # force_focus_window 的 ShowWindow/AttachThreadInput 组合拳可能在特定场景下
            # 导致微信窗口异常或被最小化，因此只作为托盘点击失败后的兜底
            import win32gui
            if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
                _log("置前", f"微信窗口不可见或已最小化，通过托盘图标唤醒 hwnd={hwnd}")
                try:
                    from src.uia.retry import click_wechat_tray_icon
                    tray_ok = click_wechat_tray_icon()
                    if tray_ok:
                        _random_sleep(0.5, 0.8)
                except Exception:
                    tray_ok = False
                if not tray_ok:
                    force_focus_window(hwnd)
            _ensure_window_on_screen(hwnd)
            
            # 先尝试静默查找导航栏。如果已经能找到，说明 UIA 树是活的，无需执行耗时且可能产生幽灵窗口的 refresh
            root, nav_toolbar = find_nav_toolbar(hwnd, max_retries=1)
            
            if not nav_toolbar:
                _log("UIA", "未检测到活跃 UI 树，执行强制刷新...")
                force_accessibility_refresh(hwnd, escalate=True)
                root, nav_toolbar = find_nav_toolbar(hwnd, max_retries=2)
            
            if not nav_toolbar:
                _log("UIA", "识别未命中，启动零感知讲述人兜底打击...")
                start_narrator()
                root, nav_toolbar = find_nav_toolbar(hwnd, max_retries=3)
                stop_narrator()

            if nav_toolbar:
                _log("UIA", "✓ 成功识别微信界面，状态正常！")
                wechat_usable = True

    if wechat_usable:
        result["success"] = True
        result["hwnd"] = hwnd
        result["root"] = root
        result["nav_toolbar"] = nav_toolbar
        return result

    if state["running"] and not state["login_windows"] and not has_multiple_instances:
        _log("重启", "⚠ 已有微信实例未能识别 UI 树，准备重启微信...")
    elif state["running"] and not state["login_windows"] and has_multiple_instances:
        _log("重启", "⚠ 已有微信实例未能识别 UI 树，但检测到有多个微信实例在运行，跳过自动重启以防影响其它实例。")
        # 在杀掉微信之前缓存 exe_path，否则进程退出后无法从 pid 反查路径
        _cached_exe_path = state.get("exe_path")
        if not exit_wechat_via_tray():
            kill_wechat()
        wait_wechat_exit()
        result["wechat_restarted"] = True
        state = detect_wechat_state()
        # 将缓存的路径注入新 state（进程已死，detect 拿不到）
        if not state.get("exe_path") and _cached_exe_path:
            state["exe_path"] = _cached_exe_path

    _log("启动", "进入安全启动/登录流程...")
    
    _narrator_running_for_launch = False
    if not state["running"]:
        _log("启动", "微信未运行，准备启动...")
        exe = state.get("exe_path")
        if not exe:
            # state.py 的 detect_wechat_state 已包含注册表 + 文件系统全量查找，
            # 若仍为空则做一次无进程的纯路径查找
            from .state import detect_wechat_state as _redetect
            exe = _redetect().get("exe_path")
        if not exe:
            _log("启动", "❌ 找不到 Weixin.exe 路径")
            return result
        
        _log("启动", "🔑 启动讲述人以100%激活 Qt Accessibility...")
        start_narrator()
        _narrator_running_for_launch = True
        
        new_hwnd = launch_wechat(exe)
        if not new_hwnd:
            stop_narrator()
            return result
        
        state = detect_wechat_state()

    hwnd = None
    if state["login_windows"]:
        login_hwnd = state["login_windows"][0][0]
        inject_hook_for_login(login_hwnd)
        hwnd = handle_login_window(login_hwnd)
        if not hwnd:
            _log("窗口", "❌ 登录超时，请手动完成微信登录后重启程序")
            if _narrator_running_for_launch: stop_narrator()
            return result
    elif state["main_windows"]:
        hwnd = max(state["main_windows"], key=lambda x: x[1] * x[2])[0]

    if not hwnd:
        _log("窗口", "❌ 未找到可用微信窗口")
        if _narrator_running_for_launch: stop_narrator()
        return result

    _log("置前", f"确保微信窗口在最前面 hwnd={hwnd}")
    # 登录后：优先托盘点击唤醒（安全、不最小化、清除白屏）
    import win32gui as _wg2
    if not _wg2.IsWindowVisible(hwnd) or _wg2.IsIconic(hwnd):
        try:
            from src.uia.retry import click_wechat_tray_icon
            if click_wechat_tray_icon():
                _random_sleep(0.5, 0.8)
            else:
                force_focus_window(hwnd)
        except Exception:
            force_focus_window(hwnd)
    _ensure_window_on_screen(hwnd)

    _random_sleep(1.0, 2.0)  # 等待 Qt 树渲染
    root, nav_toolbar = find_nav_toolbar(hwnd, max_retries=15)

    if _narrator_running_for_launch:
        _log("UIA", "关闭讲述人（已完成初次 UIA 探测）...")
        stop_narrator()
        _narrator_running_for_launch = False

    if nav_toolbar:
        _log("UIA", "✓ 导航栏就绪，微信启动流程完成！")
        result["success"] = True
        result["hwnd"] = hwnd
        result["root"] = root
        result["nav_toolbar"] = nav_toolbar
    elif not result.get("wechat_restarted"):
        if has_multiple_instances:
            _log("UIA", "⚠ 未找到导航栏，但检测到有多个微信实例在运行，跳过自动重启以防影响其它实例。")
            result["hwnd"] = hwnd
            result["root"] = root
        else:
            _log("UIA", "⚠ 未找到导航栏，Qt Accessibility 未激活")
            _log("UIA", "自动重启微信以激活 Accessibility...")
            result["wechat_restarted"] = True

            wx_state = detect_wechat_state()
        exe = wx_state["exe_path"]
        if not exe:
            _log("UIA", "❌ 找不到 Weixin.exe 路径，无法重启")
            result["hwnd"] = hwnd
            result["root"] = root
            return result

        if not exit_wechat_via_tray():
            kill_wechat()
        wait_wechat_exit()

        _log("启动", "🔑 重启时开启讲述人修复 Qt 辅助功能...")
        start_narrator()
        new_hwnd = launch_wechat(exe)
        if not new_hwnd:
            stop_narrator()
            return result

        new_state = detect_wechat_state()
        if new_state["login_windows"] and not new_state["main_windows"]:
            login_hwnd = new_state["login_windows"][0][0]
            inject_hook_for_login(login_hwnd)
            new_hwnd = handle_login_window(login_hwnd)
            if not new_hwnd:
                stop_narrator()
                return result
        elif new_state["main_windows"]:
            best = max(new_state["main_windows"], key=lambda x: x[1] * x[2])
            new_hwnd = best[0]

        force_focus_window(new_hwnd)
        _ensure_window_on_screen(new_hwnd)
        _random_sleep(1.0, 2.0)
        root2, nav2 = find_nav_toolbar(new_hwnd, max_retries=15)

        stop_narrator()

        if nav2:
            _log("UIA", "✓ 重启后导航栏已就绪！")
            result["success"] = True
            result["hwnd"] = new_hwnd
            result["root"] = root2
            result["nav_toolbar"] = nav2
        else:
            _log("UIA", "⚠ 重启后仍未找到导航栏")
            _log("UIA", "💡 可能微信版本不支持 QT_ACCESSIBILITY")
            _log("UIA", "💡 请尝试安装微信 4.1.4.17 或更高版本")
            result["hwnd"] = new_hwnd
            result["root"] = root2
    else:
        _log("UIA", "⚠ 已重启过，仍未找到导航栏")
        result["hwnd"] = hwnd
        result["root"] = root

    return result
