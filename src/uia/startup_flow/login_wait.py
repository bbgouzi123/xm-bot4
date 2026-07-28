"""微信登录等待与通知子模块，用于将 login.py 的行数控制在 300 行以内。"""

import time
import ctypes
import threading
import logging

from .utils import _log, is_wechat_main_window, _random_sleep
from .state import _enum_wechat_windows
from .login_tile import tile_windows_after_login
import win32gui

try:
    import uiautomation as uia
except ImportError:
    uia = None

# 「直接进入」优先级按钮名（无需切换账号时使用）
_ENTER_BTN_NAMES = frozenset({
    '进入微信', '一键登录', '登录', '登 录', 'Log In', 'Login',
})

# 「切换账号」按钮名（多开时若已有主界面微信则优先点此）
_SWITCH_BTN_NAMES = frozenset({
    '切换账号', 'Switch Account',
})

def _scan_login_window_buttons(hwnd: int, timeout: float = 10.0) -> dict:
    """扫描登录窗口的所有关键按钮，返回名称→坐标映射。"""
    result = {"enter": None, "switch": None, "nickname": "微信"}

    def _search():
        if not uia:
            return
        try:
            import comtypes
            comtypes.CoInitialize()
            root = uia.ControlFromHandle(hwnd)
            if not root:
                return

            cnt = 0
            for ctrl, _ in uia.WalkControl(root, maxDepth=8):
                cnt += 1
                if cnt > 400:
                    break

                cn = getattr(ctrl, 'Name', '') or ''
                ct = getattr(ctrl, 'ControlTypeName', '') or ''

                if ct == 'ButtonControl' and cn in _ENTER_BTN_NAMES and result["enter"] is None:
                    try:
                        r = ctrl.BoundingRectangle
                        result["enter"] = ((r.left + r.right) // 2, (r.top + r.bottom) // 2)
                    except Exception:
                        pass

                if ct == 'ButtonControl' and cn in _SWITCH_BTN_NAMES and result["switch"] is None:
                    try:
                        r = ctrl.BoundingRectangle
                        result["switch"] = ((r.left + r.right) // 2, (r.top + r.bottom) // 2)
                    except Exception:
                        pass

                _SKIP_TEXTS = {
                    '登录', '登 录', 'Log In', 'Login', '切换账号', '仅传输文件',
                    '扫码登录', '微信', 'WeChat', '手机连接', '网络设置',
                    'Switch Account',
                }
                if (ct == 'TextControl' and cn
                        and cn not in _SKIP_TEXTS
                        and 1 < len(cn) < 40
                        and result["nickname"] == "微信"):
                    # 剥离微信登录界面可能携带的 '当前登录用户' 前缀，
                    # 使扫描到的昵称与 InstanceManager 中保存的格式严格一致，
                    # 避免多开决策时因字符串前缀导致误判为「未登录账号」
                    _LOGIN_NICK_PREFIX = "当前登录用户"
                    stripped = cn[len(_LOGIN_NICK_PREFIX):] if cn.startswith(_LOGIN_NICK_PREFIX) else cn
                    if 1 < len(stripped) < 20:
                        result["nickname"] = stripped

        except Exception:
            pass
        finally:
            try:
                import gc
                gc.collect()
            except Exception:
                pass

    t = threading.Thread(target=_search, daemon=True)
    t.start()
    t.join(timeout=timeout)
    return result

def _has_active_main_wechat(exclude_hwnd: int = 0) -> bool:
    """判断系统中当前是否已有处于主界面的微信实例（排除自身登录窗口）。"""
    from .utils import is_wechat_main_window
    try:
        from src.utils.instance_manager import InstanceManagerV2
        instances = InstanceManagerV2.get_instance().get_all_instances()
        for inst_id, info in instances.items():
            hwnd = info.get("window_handle", 0)
            if hwnd and hwnd != exclude_hwnd and win32gui.IsWindow(hwnd):
                if win32gui.IsWindowVisible(hwnd):
                    try:
                        if is_wechat_main_window(hwnd):
                            _log("登录决策", f"共享内存中发现已登录主界面实例: {info.get('nickname', '?')} hwnd={hwnd}")
                            return True
                    except Exception:
                        pass
    except Exception:
        pass

    wins = _enum_wechat_windows()
    for hwnd, w, h, vis in wins:
        if hwnd == exclude_hwnd:
            continue
        if vis and is_wechat_main_window(hwnd):
            _log("登录决策", f"枚举发现已登录主界面微信: hwnd={hwnd}")
            return True

    return False

def _decide_login_action(hwnd: int, buttons: dict, target_instance_id: str = None) -> str:
    """根据多开状态和账号冲突决定登录动作。

    Args:
        hwnd: 当前登录窗口句柄
        buttons: 扫描出的按钮信息 {"enter": pos, "switch": pos, "nickname": str}
        target_instance_id: 本次要登录的目标实例 wxid（由 auto_get_key / launcher 传入）
    """
    has_main = _has_active_main_wechat(exclude_hwnd=hwnd)

    if has_main:
        login_nickname = buttons.get("nickname") or "微信"

        # 收集所有已在线实例的昵称 + wxid（双重集合，防止昵称占位为 wxid 时误判）
        active_nicknames = []
        active_wxids = []
        try:
            from src.utils.instance_manager import InstanceManagerV2
            insts = InstanceManagerV2.get_instance().get_all_instances()
            for inst_id, inst_info in insts.items():
                nick = inst_info.get("nickname") or ""
                status = inst_info.get("status", "")
                inst_hwnd = inst_info.get("window_handle", 0)
                # 只统计已登录主界面的实例（login_pending 的不算）
                if inst_hwnd and inst_hwnd != hwnd:
                    try:
                        import win32gui as _w32g_ld
                        if _w32g_ld.IsWindow(inst_hwnd) and is_wechat_main_window(inst_hwnd):
                            if nick:
                                active_nicknames.append(nick)
                            # 同时收集 wxid，防止昵称尚未加载时的 wxid 占位
                            if inst_id and not inst_id.startswith("wx_"):
                                active_wxids.append(inst_id)
                    except Exception:
                        pass
        except Exception as ex:
            _log("登录决策", f"获取已登录账号昵称失败: {ex}")

        _log("登录决策", f"已登录账号昵称列表: {active_nicknames}，已登录 wxid 列表: {active_wxids}，当前登录窗口昵称: {login_nickname}")

        # 判断重复登录：
        # 1. 昵称精确匹配（最常见）
        # 2. 目标 wxid 已在已登录 wxid 列表中（解决昵称占位为 wxid 的问题）
        # 3. 昵称为"微信"（未知账号，保守切换）
        is_duplicate = (login_nickname == "微信") or (login_nickname in active_nicknames)

        # ✅ 精准 wxid 匹配（最高优先级）：若 target_instance_id 已在线，必须切换账号
        if not is_duplicate and target_instance_id:
            from src.utils.wechat_key_store import clean_wxid as _cw
            tid_clean = _cw(target_instance_id)
            if any(_cw(w) == tid_clean for w in active_wxids):
                _log("登录决策", f"⚠️ 精准 wxid 匹配：目标账号 {target_instance_id} 已在线（主界面），需切换账号")
                is_duplicate = True

        if is_duplicate:
            if buttons["switch"]:
                _log("登录决策", f"✅ 检测到已有相同账号或昵称未知，将点击「切换账号」以登录新账号")
                return 'switch'
            else:
                _log("登录决策", "⚠️ 相同账号但「切换账号」按钮未找到，退回扫码等待")
                return 'qrcode'
        else:
            if buttons["enter"]:
                _log("登录决策", f"✅ 该账号未登录，可以直接点击「进入微信」快捷一键登录")
                return 'enter'
            elif buttons["switch"]:
                _log("登录决策", "⚠️ 虽是不同账号但未找到一键登录按钮，尝试点击「切换账号」")
                return 'switch'
            else:
                return 'qrcode'
    else:
        if buttons["enter"]:
            _log("登录决策", "✅ 无其他登录实例，将点击「进入微信」快捷登录")
            return 'enter'
        else:
            _log("登录决策", "📱 无快捷登录按钮，等待用户扫码")
            return 'qrcode'

logger = logging.getLogger(__name__)

def _notify_qrcode(hwnd: int, nickname: str):
    """向前端推送扫码通知（WebSocket + 系统通知）。"""
    title = "📱 微信扫码登录提醒"
    body = f"实例 [{nickname}] 请求扫码登录"
    _log("登录", f"📱 {body}，请使用手机微信扫码")

    try:
        from src.utils.alert_notifier import alert_notifier
        import asyncio

        async def _send():
            await alert_notifier.send_user_notification(title=title, body=body, category="system")

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_send())
            else:
                loop.run_until_complete(_send())
        except Exception:
            threading.Thread(
                target=lambda: asyncio.run(_send()),
                daemon=True,
            ).start()
    except Exception:
        pass

    _log("登录", "程序将自动等待扫码（可按 ESC 键取消）...")


def _notify_login_success(hwnd: int):
    """向前端推送登录成功通知。"""
    try:
        from src.utils.alert_notifier import alert_notifier
        import asyncio

        async def _send():
            await alert_notifier.send_user_notification(
                title="✅ 微信登录成功",
                body=f"微信已成功登录并进入主界面 (句柄: {hwnd})",
                category="system",
            )

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_send())
            else:
                loop.run_until_complete(_send())
        except Exception:
            threading.Thread(
                target=lambda: asyncio.run(_send()),
                daemon=True,
            ).start()
    except Exception:
        pass


def _wait_for_main_window(timeout: int = 120,
                          exclude_hwnds: set | None = None,
                          login_hwnd: int | None = None) -> int | None:
    """轮询直到微信主界面出现（可见 + ≥500×400），支持 ESC 中断、窗口销毁检测及无限等待。"""
    _excluded = exclude_hwnds or set()
    from src.utils.stop_signal import stop_signal

    i = 0
    while True:
        # 实时检测待登录的微信窗口是否已被手动关闭
        if login_hwnd and not win32gui.IsWindow(login_hwnd):
            time.sleep(1.5)  # 给快捷登录的时序留出缓冲时间
            wins = _enum_wechat_windows()
            new_big = [
                (h, w, ht) for h, w, ht, vis in wins
                if vis and h not in _excluded and is_wechat_main_window(h)
            ]
            if not new_big:
                _log("登录", f"检测到微信登录窗口 (hwnd={login_hwnd}) 已被手动关闭，终止等待。")
                return None

        is_esc_pressed = False
        for _ in range(10):
            time.sleep(0.1)
            try:
                if ctypes.windll.user32.GetAsyncKeyState(0x1B) & 0x8000:
                    is_esc_pressed = True
                    break
            except Exception:
                pass

        if is_esc_pressed or stop_signal.is_stopped:
            _log("登录", "检测到用户按下 ESC 键，取消扫码等待。")
            stop_signal.request_stop("用户在扫码界面按下 ESC")
            return None

        i += 1
        wins = _enum_wechat_windows()

        new_big = [
            (h, w, ht) for h, w, ht, vis in wins
            if vis and h not in _excluded and is_wechat_main_window(h)
        ]
        any_big = [
            (h, w, ht) for h, w, ht, vis in wins
            if vis and is_wechat_main_window(h)
        ]

        target = new_big or (any_big if not _excluded else [])
        if target:
            target.sort(key=lambda x: x[1] * x[2], reverse=True)
            main_hwnd = target[0][0]
            _log("登录", f"✓ 微信主界面已出现 hwnd={main_hwnd}")
            _notify_login_success(main_hwnd)
            tile_windows_after_login(main_hwnd)

            # 🌟 [关键修复] 当主界面上线时，立刻触发一次 do_scan_sync()
            # 确保微信句柄及上线状态能立即在 InstanceManager 刷新上线。
            # 这彻底杜绝了心跳守护因为检测到被销毁的微信登录窗口 hwnd 而误判为意外掉线的 Bug。
            try:
                from src.api.instance_helpers import do_scan_sync
                do_scan_sync()
                _log("登录", f"[句柄自愈] 成功针对新主界面 hwnd={main_hwnd} 触发 do_scan_sync 同步更新")
            except Exception as e_scan:
                _log("登录", f"[句柄自愈] 警告：执行 do_scan_sync 失败: {e_scan}")

            return main_hwnd

        if i % 10 == 0:
            _log("登录", f"等待主界面中... ({i}秒)")
