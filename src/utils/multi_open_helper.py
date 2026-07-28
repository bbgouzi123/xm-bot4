import os
import time
import logging

logger = logging.getLogger(__name__)

from src.utils.window_utils import auto_tile_wechat_windows


async def auto_bind_new_instances(manager) -> int:
    """多开后自动扫描并绑定新出现的微信窗口实例。

    绑定前先做尺寸校验，过滤宽 <500 / 高 <400 的幽灵窗口，
    防止绑定无效句柄后 UIA 一直阻塞等待超时。
    """
    import uuid
    import win32gui
    from src.uia.driver import WeChatDriver
    from src.crm.account_data import make_avatar_url, ACCOUNTS_DIR

    found = 0
    all_inst = manager.get_all_instances()
    windows = WeChatDriver.find_all_wechat_windows()

    for win_info in windows:
        hwnd = win_info["hwnd"]

        # ── 已注册且微信号有效则跳过 ───────────────────────────────────────────
        is_reg = False
        reg_id = None
        for inst_id, inst in all_inst.items():
            if inst.get('window_handle') == hwnd:
                if inst.get('wxid'):
                    is_reg = True
                    break
                else:
                    reg_id = inst_id # 记录该没有微信号的临时卡片 ID (例如 wx_xxxxxx 或 login_pending 临时实例)
        
        if is_reg:
            continue

        # ── 已登录主窗口过滤：只自动绑定已经完全登录微信并进入主界面的窗口，避免抢占登录扫码流程 ────────
        from src.uia.startup_flow.utils import is_wechat_main_window
        if not is_wechat_main_window(hwnd):
            logger.debug(f"[自动绑定] hwnd={hwnd} 不是已登录的微信主界面窗口，跳过")
            continue

        new_id = f"wx_{uuid.uuid4().hex[:8]}"
        fallback_nickname = f"微信分身_{new_id[-4:]}"

        # ── 先走缓存，只有缓存未命中时才做 extract_info（节省时间） ─────────
        temp_driver = WeChatDriver()
        success = False
        try:
            # 第一步：只绑定 UIA，不提取
            success = temp_driver.connect_by_hwnd(hwnd, extract_info=False)
            if success:
                # 第二步：优先缓存恢复
                if not temp_driver._try_restore_from_cache():
                    # 降级：UIA 头像点击提取（skip_avatar_if_exists=True 节省下载耗时）
                    temp_driver.extract_user_info_with_isolation(skip_avatar_if_exists=True)
        except Exception as e:
            logger.warning(f"[自动绑定] hwnd={hwnd} 连接/提取失败: {e}")
            success = False

        nickname = fallback_nickname
        wxid = ""
        avatar_url = ""

        if success:
            real_nick = getattr(temp_driver, '_nickname', '') or ''
            real_wxid = getattr(temp_driver, '_wxid', '') or ''
            if real_nick:
                nickname = real_nick
            if real_wxid:
                wxid = real_wxid
                avatar_path = os.path.join(ACCOUNTS_DIR, f"{wxid}.png")
                if os.path.exists(avatar_path):
                    avatar_url = make_avatar_url(wxid)
        else:
            logger.warning(f"[自动绑定] hwnd={hwnd} 绑定失败，使用默认名称")

        # 注册到 InstanceManagerV2
        target_id = wxid if wxid else new_id
        
        # 💡 合并清理：如果之前绑定在临时的 wx_xxxx 句柄上，且现在确定了真实的微信 wxid
        if reg_id and wxid and reg_id != wxid:
            logger.info(f"[自动绑定] 成功探测到临时分身 {reg_id} 的真实微信号 {wxid}，正在执行卡片合并...")
            try:
                manager.remove_instance(reg_id)
            except Exception as e_rm:
                logger.debug(f"[自动绑定] 移除临时卡片异常: {e_rm}")

        manager.register_instance(target_id, hwnd, nickname=nickname)
        update_data = {'status': 'online'}
        if wxid:
            update_data['wxid'] = wxid
        if avatar_url:
            update_data['avatar'] = avatar_url
        manager.update_instance(target_id, update_data)

        try:
            from app.state import account_manager as am
            if success:
                # 💡 如果这个 hwnd 已经在实例列表中，同步更新它的 wxid 属性，使其获得微信号！
                if hwnd in am._instances:
                    inst = am._instances[hwnd]
                    inst.wxid = wxid
                    inst.nickname = nickname
                else:
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
        except Exception as e:
            logger.debug(f"[自动绑定] 同步 account_manager 异常: {e}")

        found += 1

    return found


def start_background_auto_login_and_bind(old_hwnds: set, count: int, manager):
    """
    后台线程监控新出现的微信窗口，自动点击进入微信并绑定注册。
    """
    import threading

    def worker():
        try:
            _run_auto_login_and_bind(old_hwnds, count, manager)
        except Exception as e:
            logger.error(f"[多开后台] 发生未捕获异常: {e}")

    t = threading.Thread(target=worker, name="WeChatAutoLoginBind", daemon=True)
    t.start()


def _run_auto_login_and_bind(old_hwnds: set, count: int, manager):
    import time
    import win32gui
    from src.uia.modules.core.connect import _is_wechat_title
    from src.uia.startup_flow.narrator import start_narrator, stop_narrator

    logger.info(f"[多开后台] 开始监听新微信实例，预期数量: {count}")

    # 在监听全程保持讲述人引用，确保拉起的微信能够成功激活 Qt 无障碍树
    start_narrator()
    try:
        processed_hwnds = set()
        start_time = time.time()

        # 持续检测 45 秒
        while len(processed_hwnds) < count and (time.time() - start_time) < 45:
            time.sleep(0.5)

            current_hwnds = set()

            def cb(hwnd, _):
                try:
                    cls = win32gui.GetClassName(hwnd)
                    title = win32gui.GetWindowText(hwnd)
                    if cls.endswith("Qt51514QWindowIcon") and _is_wechat_title(title):
                        current_hwnds.add(hwnd)
                except Exception:
                    pass

            win32gui.EnumWindows(cb, None)

            new_hwnds = current_hwnds - old_hwnds - processed_hwnds
            for hwnd in new_hwnds:
                if not win32gui.IsWindow(hwnd):
                    continue

                processed_hwnds.add(hwnd)
                logger.info(f"[多开后台] 发现新微信窗口 hwnd={hwnd}")

                import threading
                def process_single_hwnd(h):
                    try:
                        _handle_single_new_hwnd(h, manager)
                    except Exception as ex:
                        logger.error(f"[多开后台] 处理窗口 hwnd={h} 异常: {ex}")

                t_single = threading.Thread(target=process_single_hwnd, args=(hwnd,), daemon=True)
                t_single.start()

                time.sleep(1.0)
    finally:
        stop_narrator()


def _handle_single_new_hwnd(hwnd: int, manager):
    import time
    import win32gui
    import uuid
    import os
    from src.uia.startup_flow.login import handle_login_window
    from src.uia.driver import WeChatDriver
    from src.crm.account_data import get_account_data_dir, make_avatar_url, ACCOUNTS_DIR
    from src.uia.startup_flow.narrator import start_narrator, stop_narrator

    # 针对当前待登录/初始化的实例，启动讲述人上下文以防其他地方退订了讲述人
    start_narrator()
    try:
        # 等待窗口完全加载
        time.sleep(1.0)
        if not win32gui.IsWindow(hwnd):
            return

        r = win32gui.GetWindowRect(hwnd)
        w = r[2] - r[0]
        h = r[3] - r[1]

        main_hwnd = hwnd
        from src.uia.startup_flow.utils import is_wechat_main_window
        is_login_window = not is_wechat_main_window(hwnd)
        if is_login_window:
            logger.info(f"[多开后台] 窗口 hwnd={hwnd} ({w}x{h}) 确认为登录窗口，启动自动点击逻辑...")
            main_hwnd = handle_login_window(hwnd)
            if main_hwnd is None:
                logger.info(f"[多开后台] 微信登录窗口已手动关闭或扫码超时，取消本次实例绑定流程")
                return

        # ── 智能判断是否还在登录/扫码界面 ──────────────────────────────
        still_in_login = not is_wechat_main_window(main_hwnd)
        if still_in_login:
            main_hwnd = hwnd

        if still_in_login:
            logger.info(f"[多开后台] 窗口 hwnd={main_hwnd} 仍在登录或扫码界面，暂停提取微信账号数据动作")
            # 注册为等待扫码状态
            new_id = f"wx_{uuid.uuid4().hex[:8]}"
            manager.register_instance(new_id, main_hwnd, nickname="微信 (等待扫码...)")
            manager.update_instance(new_id, {'status': 'login_pending'})
            return

        logger.info(f"[多开后台] 微信窗口就绪 hwnd={main_hwnd}，开始提取信息并绑定...")

        # 等待 UIA 控件树完全渲染（经验值：Qt 多进程窗口需要约 2s）
        time.sleep(2.0)

        # 绑定新实例
        new_id = f"wx_{uuid.uuid4().hex[:8]}"
        fallback_nickname = f"微信分身_{new_id[-4:]}"

        temp_driver = WeChatDriver()
        success = False
        try:
            # ── 第一步：只做 UIA 绑定，不立即提取（避免此窗口和已有窗口争夺焦点）
            success = temp_driver.connect_by_hwnd(main_hwnd, extract_info=False)
            if success:
                # ── 第二步：优先缓存恢复（跨重启热附着场景最常见）
                if not temp_driver._try_restore_from_cache():
                    # ── 降级：串行 UIA 头像提取，skip_avatar_if_exists 避免重复下载
                    logger.info(f"[多开后台] 缓存未命中，走 UIA 提取: hwnd={main_hwnd}")
                    temp_driver.extract_user_info_with_isolation(skip_avatar_if_exists=True)
        except Exception as e:
            logger.warning(f"[多开后台] 窗口 hwnd={main_hwnd} 连接/提取信息失败: {e}")

        nickname = fallback_nickname
        wxid = ""
        avatar_url = ""

        if success:
            real_nick = getattr(temp_driver, '_nickname', '') or ''
            real_wxid = getattr(temp_driver, '_wxid', '') or ''
            if real_nick:
                nickname = real_nick
            if real_wxid:
                wxid = real_wxid
                avatar_path = os.path.join(ACCOUNTS_DIR, f"{wxid}.png")
                if os.path.exists(avatar_path):
                    avatar_url = make_avatar_url(wxid)
            logger.info(f"[多开后台] 成功提取实例信息: {nickname} (wxid={wxid})")
        else:
            logger.warning(f"[多开后台] 窗口 hwnd={main_hwnd} UIA 提取失败，使用默认名称")

        # 注册到 InstanceManagerV2
        target_id = wxid if wxid else new_id
        manager.register_instance(target_id, main_hwnd, nickname=nickname)
        update_data = {'status': 'online'}
        if wxid:
            update_data['wxid'] = wxid
        if avatar_url:
            update_data['avatar'] = avatar_url
        manager.update_instance(target_id, update_data)

        # 同步到 account_manager
        try:
            from app.state import account_manager as am
            if success and main_hwnd not in am._instances:
                from src.monitor.chat_monitor import ChatMonitor
                from src.monitor.multi_account_manager import AccountInstance
                from src.monitor.friend_request_monitor import FriendRequestMonitor
                mon = ChatMonitor(temp_driver, am.ai_service)
                frm = FriendRequestMonitor(temp_driver, am.ai_service)
                inst = AccountInstance(
                    hwnd=main_hwnd, driver=temp_driver, monitor=mon, friend_request_monitor=frm,
                    nickname=nickname, wxid=wxid,
                )
                am._instances[main_hwnd] = inst
                logger.info(f"[多开后台] 已同步实例到 account_manager: {nickname}")
        except Exception as e:
            logger.debug(f"[多开后台] 同步 account_manager 异常: {e}")

        # ── 登录完成后补调多开识别 ────────────────────────────────────────
        # 此时所有之前因"在登录界面"而被跳过的实例可能已经完成登录，
        # 统一执行一次 ensure_all_wechat_ready + discover_and_connect 补扫。
        try:
            from src.uia.startup_flow import ensure_all_wechat_ready
            from src.uia.startup_flow.state import detect_wechat_state as _ds2
            _s2 = _ds2()
            _still_login = _s2.get("login_windows", [])
            if not _still_login:
                # 所有窗口已是主界面，补一次全量识别
                logger.info("[多开后台] 所有微信均已登录，触发全量识别...")
                ensure_all_wechat_ready()
                from app.state import account_manager as am2
                am2.discover_and_connect()
            else:
                logger.info(f"[多开后台] 仍有 {len(_still_login)} 个登录界面，等待后续处理")
        except Exception as _e2:
            logger.debug(f"[多开后台] 补调多开识别异常: {_e2}")

        # 自动平铺：从 account_manager 获取真实已注册的所有 hwnds 后重新铺
        try:
            from app.state import account_manager as am
            import win32gui as _wg
            live_hwnds = [
                h for h, inst in am._instances.items()
                if inst.driver.is_connected() and _wg.IsWindowVisible(h)
            ]
            if live_hwnds:
                from src.utils.window_utils import tile_all_wechat_windows
                time.sleep(1.0)
                tile_all_wechat_windows(live_hwnds)
                logger.info(f"[多开后台] 自动平铺完成，共 {len(live_hwnds)} 个窗口")
        except Exception as e:
            logger.warning(f"[多开后台] 自动平铺排版失败: {e}")
    finally:
        stop_narrator()

