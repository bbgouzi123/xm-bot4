import uuid
import logging
import os
import win32gui
import win32process
from src.utils.response import ok, err
from src.uia.driver import WeChatDriver
from src.utils.instance_manager import InstanceManagerV2

logger = logging.getLogger(__name__)
manager = InstanceManagerV2.get_instance()

def do_scan_sync() -> int:
    """同步执行的微信实例扫描逻辑"""
    from src.crm.account_data import ACCOUNTS_DIR, make_avatar_url
    
    # ── 新增：清理 account_manager 中已失效句柄的僵尸实例 ──
    try:
        from app.state import account_manager as am
        dead_hwnds = []
        for hwnd, inst in list(am._instances.items()):
            if not inst.driver.is_connected():
                dead_hwnds.append(hwnd)
        for hwnd in dead_hwnds:
            inst = am._instances.pop(hwnd, None)
            if inst:
                logger.info(f"[scan] 从 account_manager 清理已掉线僵尸实例: {inst.nickname} (hwnd={hwnd})")
    except Exception as e_clean:
        logger.debug(f"[scan] 清理 account_manager 僵尸实例异常: {e_clean}")

    found = 0
    blocked_count = 0
    all_inst = manager.get_all_instances()
    
    # 使用 WeChatDriver 的标准窗口发现逻辑
    windows = WeChatDriver.find_all_wechat_windows()
    
    for win_info in windows:
        hwnd = win_info["hwnd"]
        
        # 跳过已注册的窗口。对于已注册但还没有成功登录（未成功获取到具体真实 wxid）的窗口，我们不跳过以供重新尝试提取
        existing_inst = next((inst for inst in all_inst.values() if inst.get('window_handle') == hwnd), None)
        if existing_inst and existing_inst.get('wxid') and not existing_inst.get('wxid').startswith('wx_'):
            continue
        
        # 检查多开限制
        from src.utils.license_validator import LicenseValidator
        try:
            features = LicenseValidator.check_features()
            max_wechat = int(features.get("max_wechat", 1) or 1)
        except Exception:
            max_wechat = 1
            
        _ov = os.environ.get("XM_BOT4_MAX_WECHAT_OVERRIDE", "").strip()
        if _ov:
            try:
                max_wechat = max(1, int(_ov))
            except ValueError:
                pass

        if max_wechat != -1 and not existing_inst:
            online_count = sum(
                1
                for inst in manager.get_all_instances().values()
                if inst.get("status") != "offline" or bool(inst.get("window_handle"))
            )
            if online_count >= max_wechat:
                blocked_count += 1
                continue

        new_id = f"wx_{uuid.uuid4().hex[:8]}"
        fallback_nickname = f"微信分身_{new_id[-4:]}"
        
        # ── 已登录主窗口过滤：只自动绑定已经完全登录微信并进入主界面的窗口，避免抢占登录扫码流程 ────────
        from src.uia.startup_flow.utils import is_wechat_main_window
        is_main = is_wechat_main_window(hwnd)
        
        success = False
        if is_main:
            temp_driver = WeChatDriver()
            try:
                cls_name = win32gui.GetClassName(hwnd)
                # 💡 在自动扫描检测中，绝对禁止执行自动 UIA 物理点击提取，以解耦一键提取密钥业务
                # 💡 传入 escalate=False 避免扫描非活跃主窗口时被重型白色幽灵恢复动作卡住
                success = temp_driver.connect_by_hwnd(hwnd, extract_info=False, escalate=False)
            except Exception as e:
                logger.warning(f"[scan] hwnd={hwnd} 连接失败: {e}")
                success = False
        else:
            # 如果是已注册的 login_pending 窗口，直接跳过以节省 CPU 消耗与无意义的循环绑定
            if existing_inst:
                continue
            # ── 新增：PID 级别去重 ──────────────────────────────────────────────────────────────
            # 场景：密钥提取完成后，旧的登录 hwnd 短暂残留，实例已换新主窗口 hwnd，
            # 导致 existing_inst=None。此时若不检查 PID，会误注册新的「微信分身」临时实例。
            try:
                import win32process
                _, hwnd_pid = win32process.GetWindowThreadProcessId(hwnd)
                if hwnd_pid:
                    pid_already_tracked = any(
                        _inst.get('window_handle') and
                        win32process.GetWindowThreadProcessId(_inst['window_handle'])[1] == hwnd_pid
                        for _inst in all_inst.values()
                        if _inst.get('window_handle')
                    )
                    if pid_already_tracked:
                        logger.debug(f"[scan] hwnd={hwnd} 所属 PID={hwnd_pid} 已被其他实例跟踪，跳过重复注册")
                        continue
            except Exception:
                pass
            logger.info(f"[scan] 发现未登录/非主界面微信窗口 hwnd={hwnd}，标记为 login_pending")
        
        nickname = fallback_nickname
        wxid = ""
        avatar_url = ""
        
        if success:
            real_nick = getattr(temp_driver, '_nickname', '') or ''
            real_wxid = getattr(temp_driver, '_wxid', '') or ''
            
            # 防死循环机制：如果之前已注册了该窗口，且本次仍未提取到真实 wxid（即未扫码登录），直接跳过
            if existing_inst and not real_wxid:
                continue
                
            if real_nick:
                nickname = real_nick
            if real_wxid:
                wxid = real_wxid
                if not real_nick:
                    try:
                        from src.crm.account_data import _load_account_meta
                        meta = _load_account_meta(wxid)
                        if meta and meta.get("nickname") and meta.get("nickname") != wxid:
                            nickname = meta["nickname"]
                            temp_driver._nickname = nickname
                            logger.info(f"[scan] 成功利用本地元数据为微信号 {wxid} 恢复昵称: {nickname}")
                    except Exception:
                        pass
                avatar_path = os.path.join(ACCOUNTS_DIR, f"{wxid}.png")
                if os.path.exists(avatar_path):
                    avatar_url = make_avatar_url(wxid)
            logger.info(f"[scan] 成功提取实例 {wxid if wxid else new_id}: {nickname} (wxid={wxid})")
        else:
            # 如果绑定失败（可能是扫码中），但之前已经有 existing_inst，也应该跳过，不应重复注册或报错
            if existing_inst:
                continue
            logger.warning(f"[scan] hwnd={hwnd} 绑定失败，使用默认名称")
        
        # 如果成功获取了真实的微信 wxid 且之前是使用临时 ID 注册的，我们需要移除旧的临时注册键并迁移其隔离目录
        if success and wxid and existing_inst:
            for k, v in all_inst.items():
                if v.get('window_handle') == hwnd and k != wxid:
                    try:
                        from src.crm.account_data import migrate_virtual_account_dir
                        migrate_virtual_account_dir(k, wxid)
                    except Exception as e_mig:
                        logger.error(f"[scan] 迁移临时分身目录数据失败: {e_mig}")
                    manager.remove_instance(k)
                    logger.info(f"[scan] 检测到临时分身窗口 hwnd={hwnd} 登录成功，清理临时注册实例键: {k}")
                    break
 
        # 注册到 InstanceManagerV2
        target_id = wxid if wxid else new_id
        manager.register_instance(target_id, hwnd, nickname=nickname)
        update_data = {'status': 'online'}
        if not wxid:
            update_data['status'] = 'login_pending'
        if wxid:
            update_data['wxid'] = wxid
        if avatar_url:
            update_data['avatar'] = avatar_url
        manager.update_instance(target_id, update_data)

        # 🌟 如果发现了真实微信号并且之前是临时注册，且当前系统无活跃实例时，才主动将该真实账号设为活跃。
        # ⚠️ 关键保护：若当前已有活跃实例（用户主动选中了某账号），绝对不覆盖用户的选择！
        # 根因：do_scan_sync 在密钥提取后被调用，若无保护会把 active_id 覆盖回最后扫到的账号（秋葵），
        # 而不是用户点击「数据通道」时所操作的目标账号（乐意至极）。
        if success and wxid and existing_inst:
            try:
                from src.crm.account_data import set_active_account
                _current_active_id = manager.get_active_instance_id()
                if not _current_active_id:
                    set_active_account(wxid, nickname)
                    manager.set_active_instance(wxid)
                    logger.info(f"[scan] 当前无活跃实例，自动升级新实例 {wxid} 为活跃")
                else:
                    logger.debug(f"[scan] 当前活跃实例为 {_current_active_id}，跳过自动覆盖为 {wxid}，保护用户选择")
            except Exception as e_act:
                logger.error(f"[scan] 更新活跃实例账号失败: {e_act}")
        

        
        # 同步到 account_manager
        try:
            from app.state import account_manager as am
            if success and hwnd not in am._instances:
                from src.monitor.chat_monitor import ChatMonitor
                from src.monitor.multi_account_manager import AccountInstance
                from src.monitor.friend_request_monitor import FriendRequestMonitor
                mon = ChatMonitor(temp_driver, am.ai_service)
                frm = FriendRequestMonitor(temp_driver, am.ai_service)
                inst = AccountInstance(
                    hwnd=hwnd, driver=temp_driver, monitor=mon, friend_request_monitor=frm,
                    nickname=nickname, wxid=wxid,
                )
                am._instances[hwnd] = inst
                logger.info(f"[scan] 已同步到 account_manager: {nickname}")
                
                # ── 新增：在 scan 发现新实例并就绪后，立即异步拉起 WCDB 引擎与监控（对齐 discover_and_connect） ──
                if wxid:
                    try:
                        from app import state as app_state
                        import asyncio
                        if hasattr(app_state, "main_loop") and app_state.main_loop and app_state.main_loop.is_running():
                            asyncio.run_coroutine_threadsafe(inst.monitor._start_wcdb_engine(), app_state.main_loop)
                            asyncio.run_coroutine_threadsafe(inst.monitor.start(), app_state.main_loop)
                        else:
                            loop = asyncio.get_event_loop()
                            if loop.is_running():
                                loop.create_task(inst.monitor._start_wcdb_engine())
                                loop.create_task(inst.monitor.start())
                    except Exception as wcdb_err:
                        logger.warning(f"[scan] 异步拉起新实例监控/WCDB引擎异常: {wcdb_err}")
        except Exception as e:
            logger.debug(f"[scan] 同步 account_manager 异常: {e}")
        
        found += 1
    if blocked_count > 0:
        logger.info(f"[scan] 本次扫描中因达到订阅多开席位限制而拦截了 {blocked_count} 个微信窗口绑定")
    return found


def perform_tile_instances(is_tiled_state: bool, saved_positions: dict):
    """一键平铺布局与还原切换"""
    wechat_hwnds = []
    try:
        windows = WeChatDriver.find_all_wechat_windows()
        for w in windows:
            hwnd = w["hwnd"]
            if win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd):
                wechat_hwnds.append(hwnd)
    except Exception as e:
        logger.error(f"获取微信窗口失败: {e}")

    main_hwnd = 0
    try:
        pid = os.getpid()
        def enum_cb(hwnd, _):
            nonlocal main_hwnd
            if win32gui.IsWindowVisible(hwnd):
                _, win_pid = win32process.GetWindowThreadProcessId(hwnd)
                if win_pid == pid:
                    # 主程序窗口可能是 Electron/Tauri/Pywebview，不一定包含 "xm-bot4" 的标题
                    # 我们可以看它是否有合规的尺寸
                    rect = win32gui.GetWindowRect(hwnd)
                    w = rect[2] - rect[0]
                    h = rect[3] - rect[1]
                    if w >= 250 and h >= 250:
                        # 只要是当前进程的可见窗口，且尺寸合适，就认为是主程序窗口
                        main_hwnd = hwnd
                        return False
            return True
        win32gui.EnumWindows(enum_cb, None)
    except Exception:
        pass

    all_hwnds = wechat_hwnds + ([main_hwnd] if main_hwnd and win32gui.IsWindow(main_hwnd) else [])
    
    # 🌟 特殊保护：如果正处于平铺状态（要还原），即使没获取到任何可见微信，也可以直接根据保存的历史句柄还原
    if is_tiled_state:
        if not all_hwnds and saved_positions:
            all_hwnds = [h for h in saved_positions.keys() if win32gui.IsWindow(h)]

    if not all_hwnds:
        return err("没有找到任何可见的窗口进行平铺或还原")

    from src.utils.window_utils import handle_tile_and_restore
    try:
        action, count = handle_tile_and_restore(all_hwnds, is_tiled_state, saved_positions)
        return ok({"action": action, "window_count": count, "include_main": bool(main_hwnd)})
    except Exception as e:
        return err(f"一键平铺/还原执行异常: {e}")
