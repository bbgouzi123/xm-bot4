"""
多账号管理器 — 支持同时管理多个微信号的聊天监控

核心能力：
1. 自动发现系统中所有微信窗口
2. 每个窗口创建独立的 WeChatDriver + ChatMonitor
3. AI 侧通过 user_id 哈希沙盒实现数据隔离
4. 本地记忆（SQLite）按 bot_wxid 分库

使用方式（在 main.py 中）：
    manager = MultiAccountManager(ai_service)
    await manager.discover_and_connect()
    await manager.start_all()
"""
import asyncio
import logging
from typing import Dict, List, Optional

from src.uia.driver import WeChatDriver
from src.ai.base import AIServiceBase
from src.monitor.chat_monitor import ChatMonitor

logger = logging.getLogger(__name__)

from src.monitor.account_instance import AccountInstance

class MultiAccountManager:
    """多账号管理器（一个进程管理 N 个微信号）"""

    def __init__(self, ai_service: AIServiceBase):
        self.ai_service = ai_service
        self._instances: Dict[int, AccountInstance] = {}  # key=hwnd
        self._primary_hwnd: Optional[int] = None  # 主实例（第一个连接的）

    @property
    def primary_instance(self) -> Optional[AccountInstance]:
        """获取主实例（兼容单开场景）"""
        if self._primary_hwnd and self._primary_hwnd in self._instances:
            return self._instances[self._primary_hwnd]
        # 回退到第一个可用实例
        for inst in self._instances.values():
            if inst.driver.is_connected():
                return inst
        return None

    @property
    def primary_driver(self) -> Optional[WeChatDriver]:
        """获取主实例的 driver（兼容现有代码）"""
        inst = self.primary_instance
        return inst.driver if inst else None

    @property
    def primary_monitor(self) -> Optional[ChatMonitor]:
        """获取主实例的 monitor（兼容现有代码）"""
        inst = self.primary_instance
        return inst.monitor if inst else None

    def discover_and_connect(self) -> List[dict]:
        """发现所有微信窗口并逐一连接

        两遍扫描策略（多开安全版）：
          第一遍：只做 UIA 绑定（connect_by_hwnd(extract_info=False)），不操作窗口 UI
          平铺：多个窗口时自动排列，避免堆叠导致物理点击误触
          第二遍：逐一提取信息，每次提取时临时最小化其他微信窗口以隔离焦点

        微信 4.x 多进程架构下，同一个实例可能有多个 Qt 窗口，
        只有能提取到昵称或微信号的才是真实主窗口。

        Returns:
            连接结果列表 [{"hwnd": int, "nickname": str, "success": bool}, ...]
        """
        windows = WeChatDriver.find_all_wechat_windows()

        results = []
        already_bound = set(self._instances.keys())
        # 记录已连接的微信号，用于去重（防止同一个号的多个窗口都连上）
        connected_wxids = {inst.wxid for inst in self._instances.values() if inst.wxid}

        # ===== 第一遍：只做 UIA 绑定（不提取用户信息，不操作窗口 UI） =====
        newly_bound_hwnds = []  # 本轮新绑定的 hwnd，需要后续提取信息

        for win_info in windows:
            hwnd = win_info["hwnd"]

            # 跳过已绑定的窗口
            if hwnd in already_bound:
                inst = self._instances[hwnd]
                results.append({
                    "hwnd": hwnd,
                    "nickname": inst.nickname,
                    "success": True,
                    "action": "already_bound",
                })
                continue

            # ── 已登录主窗口过滤：只自动绑定已经完全登录微信并进入主界面的窗口，避免抢占登录扫码流程 ────────
            from src.uia.startup_flow.utils import is_wechat_main_window
            if not is_wechat_main_window(hwnd):
                print(f"[多开] hwnd={hwnd} 不是已登录的微信主界面窗口，暂不进行 UIA 绑定")
                results.append({
                    "hwnd": hwnd, "nickname": "", "success": False,
                    "action": "login_pending",
                })
                continue

            # 创建新的 driver + 尝试连接（extract_info=False：只绑定，不提取信息，不初始化遮罩）
            drv = WeChatDriver()
            success = drv.connect_by_hwnd(hwnd, extract_info=False, init_shield=False, escalate=False)

            if not success:
                print(f"[多开] ❌ UIA 绑定失败: hwnd={hwnd}")
                results.append({
                    "hwnd": hwnd, "nickname": "", "success": False,
                    "action": "bind_failed",
                })
                continue

            # 绑定成功，注册实例（信息待后续提取）
            import win32gui as _wg
            try:
                _r = _wg.GetWindowRect(hwnd)
                _w, _h = _r[2] - _r[0], _r[3] - _r[1]
            except Exception:
                _w, _h = 0, 0

            # 幽灵窗口过滤：只有小尺寸窗口才隐藏
            # 【关键】如果窗口是微信的登录/扫码窗口 (LoginWnd)，绝不能当作幽灵窗口过滤并隐藏，否则用户无法扫码/点击确认
            is_login_wnd = False
            try:
                cls_name = _wg.GetClassName(hwnd)
                if "LoginWnd" in cls_name or "Qt51514QWindowIcon" in cls_name:
                    is_login_wnd = True
            except Exception:
                pass

            if not is_login_wnd and (_w < 500 or _h < 400):
                print(f"[多开] 👻 hwnd={hwnd} ({_w}x{_h}) 尺寸过小，"
                      f"可能是幽灵窗口，隐藏并跳过")
                drv._connected = False
                try:
                    import ctypes
                    ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
                except Exception:
                    pass
                results.append({
                    "hwnd": hwnd, "nickname": "", "success": False,
                    "action": "ghost_window",
                })
                continue

            mon = ChatMonitor(drv, self.ai_service)
            from src.monitor.friend_request_monitor import FriendRequestMonitor
            frm = FriendRequestMonitor(drv, self.ai_service)
            inst = AccountInstance(
                hwnd=hwnd, driver=drv, monitor=mon, friend_request_monitor=frm,
                nickname="", wxid="",
            )
            self._instances[hwnd] = inst
            newly_bound_hwnds.append(hwnd)

            if self._primary_hwnd is None:
                self._primary_hwnd = hwnd

            results.append({
                "hwnd": hwnd, "nickname": "", "success": True,
                "action": "connected_pending_info",
            })

        # ===== 多个窗口时先平铺，避免堆叠导致物理点击穿透到错误窗口 =====
        if len(self._instances) >= 1:
            self._tile_all_windows()

        # ===== 第二遍：逐一恢复账号信息 =====
        # 策略（大厂标准双轨制）：
        #   快速路径 —— 先查 instance_snapshot 缓存（hwnd 精确匹配），命中则 0 UIA 操作
        #   降级路径 —— 缓存未命中时才走 UIA 点击头像提取（需要 uia_lock 串行隔离）
        for hwnd in newly_bound_hwnds:
            inst = self._instances.get(hwnd)
            if not inst:
                continue

            from src.monitor.account_profile_helper import restore_account_profile
            restore_account_profile(inst, hwnd)

            inst.wxid = wxid = inst.driver._wxid or ""
            inst.nickname = nickname = inst.driver._nickname or ""
            if not nickname and wxid:
                try:
                    from src.crm.account_data import _load_account_meta
                    meta = _load_account_meta(wxid)
                    if meta and meta.get("nickname") and meta.get("nickname") != wxid:
                        inst.nickname = nickname = inst.driver._nickname = meta["nickname"]
                        print(f"[多开-全局兜底] 成功利用本地元数据为微信号 {wxid} 恢复昵称: {nickname}")
                except Exception as e_meta:
                    print(f"[多开-全局兜底] 读取本地元数据异常: {e_meta}")

            # ─ 去重检查 ───────────────────────────────────────────────────
            if wxid and wxid in connected_wxids:
                print(f"[多开] ⚠️ hwnd={hwnd} 的微信号 {wxid} 已连接，移除重复实例")
                inst.driver._connected = False
                del self._instances[hwnd]
                for r in results:
                    if r.get("hwnd") == hwnd:
                        r["action"] = "duplicate_wxid"
                        r["success"] = False
                        break
                continue

            # ─ 注册到共享内存 ─────────────────────────────────────────────
            if wxid:
                connected_wxids.add(wxid)
                try:
                    from src.utils.instance_manager import InstanceManagerV2
                    from src.crm.account_data import make_avatar_url
                    m_inst = InstanceManagerV2.get_instance()
                    m_inst.register_instance(wxid, hwnd, nickname)
                    m_inst.update_instance(wxid, {
                        "status": "online",
                        "wxid": wxid,
                        "avatar": make_avatar_url(wxid)
                    })
                except Exception as e:
                    print(f"[多开] 注册共享内存异常: {e}")

                # 无论自动聊天是否开启，接管微信后立即异步拉起 WCDB 引擎，提取密钥并建立只读连接
                # 🌟 [强力门控] 严格限制：只有当平台用户已成功登录时，才拉起微信数据库。
                from src.utils.auth_session import has_active_platform_session
                if has_active_platform_session():
                    try:
                        from app import state as app_state
                        if hasattr(app_state, "main_loop") and app_state.main_loop and app_state.main_loop.is_running():
                            asyncio.run_coroutine_threadsafe(inst.monitor._start_wcdb_engine(), app_state.main_loop)
                        else:
                            loop = asyncio.get_event_loop()
                            if loop.is_running():
                                loop.create_task(inst.monitor._start_wcdb_engine())
                    except Exception as e:
                        print(f"[多开] 异步拉起 WCDB 引擎异常: {e}")
                else:
                    print(f"[多开] 🔒 平台尚未成功登录，已跳过 WCDB 数据库解密连接 (hwnd={hwnd})。将在登录后由 sso_api 补触发。")

            # ─ 更新结果列表 ───────────────────────────────────────────────
            for r in results:
                if r.get("hwnd") == hwnd:
                    r["nickname"] = nickname
                    r["wxid"] = wxid
                    if nickname or wxid:
                        r["action"] = "connected"
                    break

            # ─ 在基础信息获取并就绪后，安全地延迟初始化该窗口的隐私遮罩 ────────────
            import win32gui
            if inst and inst.driver.is_connected() and win32gui.IsWindowVisible(hwnd):
                try: inst.driver._init_privacy_shield_with_local_avatar(hwnd)
                except Exception as e: print(f"[多开] 🛡 hwnd={hwnd} 初始化隐私遮罩异常: {e}")

        # 如果开启了全局好友自动通过，自动启动新实例的监控
        try:
            from src.api.config_api import _load_configs
            configs = _load_configs()
            auto_accept = configs.get("friend_request_settings", {}).get("auto_accept", False)
            if auto_accept:
                for hwnd in newly_bound_hwnds:
                    inst = self._instances.get(hwnd)
                    if inst and inst.friend_request_monitor and not inst.friend_request_monitor.is_running():
                        inst.friend_request_monitor.start()
        except Exception as e:
            print(f"[多开] 自动启动新朋友监控异常: {e}")

        return results


    # ==================== 多开窗口管理 ====================
    def _tile_all_windows(self):
        import win32gui
        from src.utils.window_utils import tile_all_wechat_windows
        tile_all_wechat_windows([h for h, i in self._instances.items() if i.driver.is_connected() and win32gui.IsWindowVisible(h)])


    async def start_all(self):
        """启动所有实例的聊天监控"""
        from src.api.config_api import _load_configs
        configs = _load_configs()
        auto_accept = configs.get("friend_request_settings", {}).get("auto_accept", False)

        for hwnd, inst in self._instances.items():
            if not inst.monitor._running:
                try:
                    await inst.monitor.start()
                    print(f"[多开] 🚀 {inst.nickname} 监控已启动")
                except Exception as e:
                    print(f"[多开] ❌ {inst.nickname} 启动失败: {e}")
            if auto_accept and inst.friend_request_monitor and not inst.friend_request_monitor.is_running():
                try:
                    inst.friend_request_monitor.start()
                    print(f"[多开] 🚀 {inst.nickname} 好友监控已启动")
                except Exception as e:
                    print(f"[多开] ❌ {inst.nickname} 好友监控启动失败: {e}")

    async def stop_all(self):
        """停止所有实例的聊天监控"""
        for hwnd, inst in self._instances.items():
            if inst.monitor._running:
                try:
                    await inst.monitor.stop()
                    print(f"[多开] ⏹  {inst.nickname} 监控已停止")
                except Exception as e:
                    print(f"[多开] ❌ {inst.nickname} 停止失败: {e}")
            if inst.friend_request_monitor and inst.friend_request_monitor.is_running():
                try:
                    inst.friend_request_monitor.stop()
                    print(f"[多开] ⏹  {inst.nickname} 好友监控已停止")
                except Exception as e:
                    print(f"[多开] ❌ {inst.nickname} 好友监控停止失败: {e}")

    async def start_instance(self, hwnd: int) -> bool:
        inst = self._instances.get(hwnd)
        if not inst: return False
        if not inst.monitor._running: await inst.monitor.start()
        from src.api.config_api import _load_configs
        if _load_configs().get("friend_request_settings", {}).get("auto_accept", False):
            if inst.friend_request_monitor and not inst.friend_request_monitor.is_running():
                inst.friend_request_monitor.start()
        return True





    def get_status(self) -> dict:
        return {
            "total": len(self._instances),
            "active": sum(1 for i in self._instances.values() if i.monitor._running),
            "instances": [{**i.to_dict(), "is_primary": (h == self._primary_hwnd)} for h, i in self._instances.items()],
        }

    def get_instance_by_wxid(self, wxid: str) -> Optional[AccountInstance]:
        return next((i for i in self._instances.values() if i.wxid == wxid), None)

    def refresh(self) -> List[dict]:
        """刷新：清理断开的连接 + 发现新窗口

        适合放在定时器里周期调用。
        注意：不再自动调用 _extract_user_info()，
        信息提取改为由前端登录后通过 /api/user/extract-info 显式触发，
        避免后台静默操作微信窗口影响用户体验。
        """

        # 1. 清理已断开的实例
        dead_hwnds = []
        for hwnd, inst in self._instances.items():
            if not inst.driver.is_connected():
                dead_hwnds.append(hwnd)

        for hwnd in dead_hwnds:
            inst = self._instances.pop(hwnd)
            print(f"[多开] 🔌 {inst.nickname} 已断开，移除实例")
            if inst.wxid:
                try:
                    from src.utils.instance_manager import InstanceManagerV2
                    InstanceManagerV2.get_instance().remove_instance(inst.wxid)
                except Exception as e:
                    print(f"[多开] 共享内存移除异常: {e}")
            if hwnd == self._primary_hwnd:
                self._primary_hwnd = None

        # 2. 发现新窗口
        return self.discover_and_connect()
