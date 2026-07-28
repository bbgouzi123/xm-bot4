import os
import time
import uuid
import traceback

def do_wechat_connect() -> dict:
    """执行微信连接全流程（移自 src/api/system.py）"""
    from app.state import account_manager, driver, monitor

    try:
        # === 1. 微信 UIA 基础环境就绪（先确保 Qt Accessibility 激活）===
        from src.uia.startup_flow import ensure_wechat_ready, ensure_all_wechat_ready
        print("[微信连接] 开始执行微信 UIA 启动流程...")
        ensure_wechat_ready()

        # === 1b. 多开识别：仅在所有微信均已完成登录后才执行 ===
        # 设计原则：若仍有登录界面（用户尚未扫码/点击进入），绝不提前识别。
        # 未完成登录的实例由 multi_open_helper 后台线程在登录成功后逐一接管绑定。
        try:
            from src.uia.startup_flow.state import detect_wechat_state as _ds
            _state_check = _ds()
            _pending_login = _state_check.get("login_windows", [])
            if _pending_login:
                print(
                    f"[微信连接] ⏸ 发现 {len(_pending_login)} 个登录界面尚未完成登录，"
                    f"批量识别跳过，将由后台线程在登录完成后逐一接管"
                )
            else:
                # 所有窗口均已是主界面 → 批量激活 UIA 树
                ready_hwnds = ensure_all_wechat_ready()
                if len(ready_hwnds) > 1:
                    print(f"[微信连接] 🎉 检测到 {len(ready_hwnds)} 个已登录微信，全部 UIA 激活完成")
                elif len(ready_hwnds) == 1:
                    print(f"[微信连接] 单账号模式 hwnd={ready_hwnds[0]}")
        except Exception as _me:
            print(f"[微信连接] 多开识别警告: {_me}")

        # === 2. 实例探测与连接 ===
        t0 = time.time()
        connect_results = account_manager.discover_and_connect()
        elapsed = time.time() - t0
        connected_count = sum(1 for r in connect_results if r["success"])
        print(f"[微信连接] 实例扫描完成: {connected_count} 个成功, 耗时 {elapsed:.1f}s")

        if connected_count > 0:
            primary = account_manager.primary_instance
            if primary:
                driver.__dict__.update(primary.driver.__dict__)
                driver.hwnd = primary.driver.hwnd
                try:
                    driver.root = primary.driver.root
                except Exception as root_err:
                    print(f"[微信连接] [WARN] 获取 primary.driver.root 异常: {root_err}")
                driver._nickname = primary.driver._nickname
                driver._wxid = primary.driver._wxid
                driver._connected = primary.driver._connected
                monitor.driver = driver

                nickname = primary.nickname
                wxid = primary.wxid

                if primary.driver.hwnd:
                    try:
                        from src.uia.privacy_shield import get_privacy_shield
                        avatar_path = ""
                        if wxid:
                            from src.crm.account_data import ACCOUNTS_DIR
                            avatar_path = os.path.join(ACCOUNTS_DIR, f"{wxid}.png")
                        get_privacy_shield().auto_start(
                            primary.driver.hwnd, "", nickname=nickname or "", avatar_path=avatar_path
                        )
                    except Exception:
                        pass

                from src.crm.account_data import set_active_account, migrate_legacy_data
                migrate_legacy_data()
                set_active_account(wxid or nickname, nickname)

                try:
                    from src.utils.instance_manager import InstanceManagerV2
                    inst_mgr = InstanceManagerV2.get_instance()
                    for r in [x for x in connect_results if x["success"] and x.get("action") in ("connected", "connected_pending_info")]:
                        hwnd = r["hwnd"]
                        wxid = r.get("wxid")
                        nickname = r.get("nickname", "")
                        
                        target_id = None
                        all_instances = inst_mgr.get_all_instances()
                        if wxid:
                            target_id = wxid
                        else:
                            for inst_id, inst_data in all_instances.items():
                                if inst_data.get("window_handle") == hwnd:
                                    target_id = inst_id
                                    break
                        if not target_id:
                            target_id = f"wx_{uuid.uuid4().hex[:8]}"
                            
                        inst_mgr.register_instance(target_id, hwnd, nickname=nickname)
                        up = {'status': 'online'}
                        if wxid:
                            up['wxid'] = wxid
                            from src.crm.account_data import make_avatar_url
                            up['avatar'] = make_avatar_url(wxid)
                        inst_mgr.update_instance(target_id, up)
                        try:
                            from src.uia.privacy_shield import get_privacy_shield
                            if get_privacy_shield().enabled:
                                get_privacy_shield().enable(hwnd)
                        except Exception:
                            pass
                except Exception:
                    pass

        # === 3. 调度器初始化 ===
        try:
            from src.scheduler.automation_scheduler import AutomationScheduler
            scheduler = AutomationScheduler.get_instance()
            for r in [x for x in connect_results if x["success"] and x.get("action") == "connected"]:
                inst = account_manager._instances.get(r["hwnd"])
                if inst:
                    scheduler.register_instance(
                        instance_id=r.get("wxid") or f"wx_{r['hwnd']}",
                        driver=inst.driver,
                        hwnd=r["hwnd"],
                        nickname=r.get("nickname", ""),
                        wxid=r.get("wxid", ""),
                    )
        except Exception:
            pass

        # === 4. 默认开启右上角悬浮看板看板 ===
        if connected_count > 0:
            try:
                from src.utils.status_overlay import status_overlay
                status_overlay.start()
                status_overlay.update("就绪", "等待系统指令...")
            except Exception as e:
                print(f"[微信连接] 默认开启状态看板异常: {e}")

        print("[微信连接] ✅ 微信连接流程全部完成")
        return {"success": True, "connected": connected_count}

    except Exception as e:
        traceback.print_exc()
        print(f"[微信连接] ❌ 连接失败: {e}")
        return {"success": False, "error": str(e)}
