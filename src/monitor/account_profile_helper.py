import os
import logging
from src.monitor.account_instance import AccountInstance

logger = logging.getLogger(__name__)

def restore_account_profile(inst: AccountInstance, hwnd: int, allow_uia_click: bool = False) -> bool:
    """尝试通过各种路径恢复账号信息"""
    cache_ok = False
    try:
        cache_ok = inst.driver._try_restore_from_cache()
    except Exception as e:
        logger.debug(f"[多开] hwnd={hwnd} 缓存恢复异常: {e}")

    if not cache_ok:
        try:
            from src.wechat_4x.db_profile_extractor import extract_profile_from_db
            profile = extract_profile_from_db(hwnd, inst.driver._wxid)
            if profile:
                wxid, nickname = profile
                inst.driver._wxid = wxid
                inst.driver._nickname = nickname
                inst.driver._connected = True
                cache_ok = True
                logger.info(f"[多开-双引擎] 成功通过本地数据库提取微信号信息: nickname={nickname!r}, wxid={wxid!r}")
                
                # 🌟 核心同步：立刻持久化并同步共享内存
                try:
                    from src.crm.account_data import _save_account_meta
                    aid = getattr(inst, "account_id", None) or getattr(inst.driver, "account_id", None) or wxid
                    _save_account_meta(aid, nickname, wxid)
                    if aid != wxid:
                        _save_account_meta(wxid, nickname, wxid)
                    
                    from src.utils.instance_manager import InstanceManagerV2
                    manager = InstanceManagerV2.get_instance()
                    for inst_id, inst_data in manager.get_all_instances().items():
                        if inst_id == aid or inst_data.get("wxid") == wxid or inst_data.get("window_handle") == hwnd:
                            manager.update_instance(inst_id, {
                                "nickname": nickname,
                                "wxid": wxid,
                                "has_key": True
                            })
                            logger.info(f"[多开-双引擎] 成功同步账号元数据与共享内存: {inst_id} -> {nickname}({wxid})")
                except Exception as e_sync:
                    logger.warning(f"[多开-双引擎] 同步账号元数据与共享内存异常: {e_sync}")
        except Exception as db_init_err:
            logger.warning(f"[多开-双引擎] 尝试通过解密本地数据库提取实例资料异常: {db_init_err}")

    if not cache_ok:
        # ── 过滤登录窗口：如果当前还是登录确认/扫码窗口，绝对禁止执行 UIA 物理提取，防锁超时 ──
        import win32gui
        try:
            cls_name = win32gui.GetClassName(hwnd)
            if "LoginWnd" in cls_name or "Qt51514QWindowIcon" in cls_name:
                logger.info(f"[多开] hwnd={hwnd} 当前为登录窗口，跳过 UIA 物理提取")
                
                # ── 💡 极速静默扫描登录界面上的用户昵称与对应头像 ──
                try:
                    import uiautomation as auto
                    # 避免阻塞，限定在此窗口内进行浅度极速查找
                    wnd_el = auto.WindowControl(searchDepth=1, hwnd=hwnd)
                    if wnd_el.Exists(0.1):
                        nick_el = wnd_el.TextControl(searchDepth=5, AutomationId="current_login_nick_name")
                        if nick_el.Exists(0.1):
                            # 二次防线：获取所属顶层 Window 句柄，验证与当前 hwnd 严格一致，拦截跨窗口搜索溢出 (串号)
                            top_wnd = nick_el.GetTopLevelWindow()
                            top_hwnd = top_wnd.NativeWindowHandle if top_wnd else 0
                            if top_hwnd != hwnd:
                                logger.debug(f"[多开-UIA静默] 拦截搜索溢出：控件所属顶层句柄 {top_hwnd} 与当前窗口句柄 {hwnd} 不一致")
                                return False

                            raw_name = nick_el.Name
                            if raw_name:
                                nick = raw_name
                                prefix = "当前登录用户"
                                if nick.startswith(prefix):
                                    nick = nick[len(prefix):]
                                if nick:
                                    inst.driver._nickname = nick
                                    logger.info(f"[多开-UIA静默] 成功从登录窗口提取待登录昵称: {nick}")
                                    
                                    # 从历史已登录账号中检索是否匹配此昵称，以提前关联 wxid 并渲染头像
                                    from src.crm.account_ops import list_accounts
                                    for acct in list_accounts():
                                        if acct.get("nickname") == nick:
                                            matched_wxid = acct.get("wxid")
                                            if matched_wxid:
                                                inst.driver._wxid = matched_wxid
                                                logger.info(f"[多开-UIA静默] 匹配到历史微信号 {matched_wxid}，已提前同步头像与绑定关系")
                                                break
                except Exception as e_scan:
                    logger.warning(f"[多开-UIA静默] 提取待登录账号资料异常: {e_scan}")

                return False
        except Exception:
            pass

        if allow_uia_click:
            logger.info(f"[多开] hwnd={hwnd} 缓存与数据库匹配均未命中，启动 UIA 头像点击提取...")
            try:
                inst.driver.extract_user_info_with_isolation(skip_avatar_if_exists=True)
            except Exception as e:
                logger.error(f"[多开] hwnd={hwnd} UIA 提取异常: {e}")
        else:
            logger.info(f"[多开] hwnd={hwnd} 缓存与数据库匹配未命中，且 allow_uia_click=False，跳过物理点击提取")
            
    return cache_ok
