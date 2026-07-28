import asyncio, logging, re, time, os
from typing import Any
from src.utils.license_validator import LicenseValidator
from src.crm.account_data import get_active_account
from src.utils.websocket_manager import ws_manager
from src.utils.uia_task_runner import is_uia_maintenance_active, run_uia_with_timeout
from .base import _chat_daily_counter
from .message_scanner import check_friend_in_list
from .reply_workflow_media import VoiceTranscribedMessage, process_incoming_multimedia
from .reply_preconditions import check_reply_preconditions

logger = logging.getLogger(__name__)

# check_reply_preconditions已解耦移出至reply_preconditions.py中以遵守300行有效代码限制


_syncing_names = set()

def trigger_lazy_sync_contact(name: str, account_id: str):
    """
    非阻塞异步触发活跃联系人的 lazy-sync，补全 wxid 并记录到 contacts_cache 内存及 snapshot
    """
    from src.crm.account_data import get_account_settings
    if not get_account_settings(account_id).get("reply", {}).get("fetch_profile_enabled", True):
        logger.info(f"[LazySync] 根据全局配置，已禁用在聊天过程中获取用户详细信息与头像: {name}")
        return

    if name in _syncing_names:
        return
    _syncing_names.add(name)

    async def _do_lazy_sync():
        try:
            from src.uia.contacts import ContactSync
            from app.state import driver
            if driver and driver.is_connected():
                syncer = ContactSync(driver)
                logger.info(f"[LazySync] 触发自动回复活跃好友 '{name}' 的增量详情同步...")
                loop = asyncio.get_running_loop()
                
                # 1. 优先尝试在当前会话的聊天界面下进行“无感/静默”侧边栏补全
                lazy_ok = await loop.run_in_executor(
                    None,
                    lambda: syncer.try_lazy_sync_current_chat(name)
                )
                if lazy_ok:
                    logger.info(f"[LazySync] 成功使用当前会话侧边栏对好友 '{name}' 进行了无感同步！")
                    return
                # 2. 后台静默增量同步场景下，为避免强抢前台焦点干扰用户及引发死锁，不执行通讯录管理弹窗同步
                logger.warning(f"[LazySync] 侧边栏无感同步未生效，已放弃通讯录管理弹窗同步兜底。好友: '{name}'")
        except Exception as lazy_ex:
            logger.warning(f"[LazySync] 补全好友 '{name}' 详情异常 (非致命): {lazy_ex}")
        finally:
            _syncing_names.discard(name)

    asyncio.create_task(_do_lazy_sync())


async def run_uia_history_recovery(engine, name, is_group, account_id, history_mgr, context_msgs, msg_hint: str = "", wxid: str = None):
    """
    通过微信窗口 UIA 物理回溯补全并实时对齐增量聊天记录历史，保持本地记忆同微信界面 100% 一致。
    """
    try:
        # 🚀 优先判定数据库同步引擎状态，若数据库实时通道正常，直接利用数据库记录同步记忆库，免去物理 UIA 扫描
        is_db_online = False
        session_monitor = getattr(engine, "_wcdb_session_monitor", None)
        try:
            if session_monitor and session_monitor.is_active():
                is_db_online = True
            else:
                from src.wechat_4x.wcdb_monitor import get_wcdb_monitor
                monitor = get_wcdb_monitor(account_id or 'default')
                if monitor and monitor.is_active():
                    is_db_online = True
        except Exception:
            pass

        # 如果是群聊，不执行高频窗口切换和物理回溯，以防干扰群监控
        # 🌟 但如果是数据库同步引擎在线，我们依旧要安全、无物理切换地同步群消息历史！
        if is_group and not is_db_online:
            return context_msgs

        if is_db_online:
            logger.info(f"[工作流] 检测到微信数据库在线，正在从数据库拉取最新聊天历史对齐本地记忆库。会话: {name}")
            target_wxid = wxid
            if not target_wxid or target_wxid == name:
                target_wxid = name
                try:
                    from src.utils.contacts_cache import contacts_cache
                    friends = contacts_cache.get_friends(account_id or 'default')
                    for f in friends:
                        if f.get("name") == name or f.get("remark") == name:
                            target_wxid = f.get("wxid")
                            break
                    if target_wxid == name:
                        groups = contacts_cache.get_groups(account_id or 'default')
                        for g in groups:
                            if g.get("name") == name:
                                target_wxid = g.get("wxid")
                                break
                except Exception as e_cache:
                    logger.debug(f"[工作流] 查找 wxid 异常: {e_cache}")

            try:
                db_msgs = []
                if session_monitor and session_monitor.is_active():
                    glm = getattr(session_monitor, "get_latest_messages", None)
                    if glm:
                        db_msgs = glm(target_wxid, limit=20 if is_group else 15)
                    elif not db_msgs:  # DLL 降级：session_monitor 无此方法，直接用 _msg_fallback_monitor
                        fb = getattr(session_monitor, "_msg_fallback_monitor", None)
                        glm_fb = getattr(fb, "get_latest_messages", None)
                        if glm_fb:
                            db_msgs = glm_fb(target_wxid, 20 if is_group else 15)
                if not db_msgs:
                    from src.wechat_4x.wcdb_monitor import get_wcdb_monitor
                    monitor = get_wcdb_monitor(account_id or 'default')
                    db_msgs = monitor.get_latest_messages(target_wxid, limit=20 if is_group else 15)
                
                if db_msgs:
                    import hashlib
                    db_history = []
                    driver_nickname = getattr(engine.driver, "_nickname", "") or "我"
                    
                    def parse_db_time(m_item: dict) -> str:
                        cat = m_item.get("created_at")
                        if cat:
                            return cat
                        ts = m_item.get("timestamp")
                        if ts:
                            try:
                                return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))
                            except Exception:
                                pass
                        return time.strftime("%Y-%m-%d %H:%M:%S")
                    
                    if is_group:
                        import os
                        import json
                        from src.utils.contacts_cache import contacts_cache
                        
                        # 提前加载群成员列表以提高解析性能
                        members = contacts_cache.get_group_members(account_id or 'default', name) or []
                        friends = contacts_cache.get_friends(account_id or 'default') or []
                        
                        def resolve_member_name(wxid_val: str) -> str:
                            if not wxid_val:
                                return ""
                            if wxid_val == account_id:
                                return driver_nickname
                            
                            # 1. 优先在群成员缓存中寻找
                            for m in members:
                                if m.get("wxid") == wxid_val:
                                    return m.get("display_name") or m.get("nickname") or m.get("name") or ""
                            
                            # 2. 其次在好友缓存中寻找
                            for f in friends:
                                if f.get("wxid") == wxid_val:
                                    return f.get("name") or f.get("remark") or f.get("nickname") or ""
                                    
                            return wxid_val
                            
                        for m in reversed(db_msgs):
                            role = "assistant" if m["is_self"] else "user"
                            content_val = m["content"].strip()
                            sender_name = driver_nickname
                            
                            if not m["is_self"]:
                                # 从 content 中解析真正的群消息发送人 wxid
                                # 格式如 "wxid_xxxx:\n实际内容"
                                m_sender = re.match(r"^([a-zA-Z0-9_\-]+):\s*\n(.*)$", content_val, re.DOTALL)
                                if m_sender:
                                    sender_wxid = m_sender.group(1)
                                    content_val = m_sender.group(2).strip()
                                    sender_name = resolve_member_name(sender_wxid)
                                else:
                                    sender_name = name
                            
                            db_history.append({
                                "role": role,
                                "content": content_val,
                                "sender": sender_name,
                                "time": parse_db_time(m)
                            })
                    else:
                        for m in reversed(db_msgs):
                            role = "assistant" if m["is_self"] else "user"
                            sender_name = driver_nickname if m["is_self"] else name
                            db_history.append({
                                "role": role,
                                "content": m["content"].strip(),
                                "sender": sender_name,
                                "time": parse_db_time(m)
                            })
                            
                    session_key = target_wxid or name
                    
                    # 🌟 强力去重机制：对比内存现有消息的 md5 指纹，只保存新消息，消除高频触发时的内存与数据库膨胀
                    existing_fps = set()
                    with history_mgr._lock:
                        if session_key in history_mgr._sessions:
                            for m in history_mgr._sessions[session_key]:
                                existing_fps.add(hashlib.md5(m["content"].strip().encode("utf-8")).hexdigest())
                                
                    filtered_db_history = []
                    for m in db_history:
                        fp = hashlib.md5(m["content"].strip().encode("utf-8")).hexdigest()
                        if fp not in existing_fps:
                            filtered_db_history.append(m)
                            
                    if filtered_db_history:
                        history_mgr.save_messages(session_key, name, filtered_db_history, is_group=is_group)
                        logger.info(f"[工作流] 🚀 成功从数据库同步最新 {len(filtered_db_history)} 条新聊天历史到记忆库。会话: {name}")
                    else:
                        logger.debug(f"[工作流] 数据库同步历史完成，无新消息需要同步。会话: {name}")
                        
                    return history_mgr.get_context(session_key, window_size=20)
            except Exception as db_sync_err:
                logger.warning(f"[工作流] 数据库同步历史失败: {db_sync_err}")
            return context_msgs

        # 🚀 【前后台防干扰与死锁拦截】
        # 如果微信句柄不存在，或者微信主窗口当前不处于最前台（例如被遮挡、最小化或用户在使用其他程序），
        # 此时强行通过 UIA 物理切换会话对齐聊天记录极易引发 UIA 锁死超时（挂起 15 秒）。
        # 既然我们已经有后台数据库同步引擎（WCDB / 影子拷贝）实时向本地记忆库同步增量消息，
        # 在微信处于后台或最小化时，可以直接安全跳过物理 UIA 切换，直接读取本地历史返回。
        import win32gui
        hwnd = getattr(engine.driver, 'hwnd', None)
        if not hwnd or not win32gui.IsWindow(hwnd):
            logger.debug(f"[工作流] 微信窗口无效，跳过物理 UIA 历史回溯，使用本地历史")
            return context_msgs
            
        # 检查是否最小化
        if win32gui.IsIconic(hwnd):
            logger.debug(f"[工作流] 微信当前处于最小化状态，跳过物理 UIA 历史回溯，使用本地历史")
            return context_msgs
            
        # 检查是否处于最前台
        fg_hwnd = win32gui.GetForegroundWindow()
        if fg_hwnd != hwnd:
            logger.debug(f"[工作流] 微信不处于前台活动窗口 (fg_hwnd={fg_hwnd}, WeChat={hwnd})，跳过物理 UIA 回溯")
            return context_msgs

        # 🛡️ UIA 并发保护：历史回溯是可选操作，若 UIA 锁已被其他会话占用（如正在发送消息），
        # 直接跳过物理回溯，避免多人并发时形成排队死锁并触发 120s 安全阀熔断
        try:
            from src.utils.uia_lock import uia_lock as _uia_lock
            if _uia_lock.is_busy:
                logger.debug(f"[工作流] UIA 当前忙于 '{_uia_lock.current_task}'，跳过物理历史回溯，使用本地历史")
                return context_msgs
        except Exception:
            pass

        from src.utils.uia_task_runner import run_uia_with_timeout

        chat_switched = [True]

        def _switch_and_fetch():
            # 🚀 [防干扰与免重复切换] 前置已成功 ChatWith，若当前输入框仍存在，直接读取历史以绝防重复搜索挂起
            edit = engine.driver._get_edit_control(name)
            if edit and edit.Exists(0.15):
                return engine.driver.get_all_messages(parse_file=False, context_count=15, session_name=name)
            if engine.driver.ChatWith(name, lock_input=True, msg_hint=msg_hint, wxid=wxid):
                return engine.driver.get_all_messages(parse_file=False, context_count=15, session_name=name)
            chat_switched[0] = False
            return []

        raw_bubbles = await run_uia_with_timeout(_switch_and_fetch, 15.0)
        if not chat_switched[0]:
            logger.warning(f"[工作流] ChatWith 切换会话 '{name}' 返回失败，终止对齐")
            return None
        if raw_bubbles:
            scanned_history = []
            for sender, content in raw_bubbles:
                if not content or not content.strip():
                    continue
                if sender in ("GREET", "SYS", "Recall", "Time"):
                    continue
                
                role = "user"
                if sender == (engine.driver._nickname or "我"):
                    role = "assistant"
                
                scanned_history.append({
                    "role": role,
                    "content": content.strip(),
                    "sender": sender
                })

            if scanned_history:
                # 执行最大前缀-后缀对齐算法，将未同步的增量消息写入本地历史库
                session_key = wxid or name
                local_context = history_mgr.get_context(session_key, window_size=20)
                if not local_context:
                    # 本地为空，直接全量保存
                    history_mgr.save_messages(session_key, name, scanned_history, is_group=False)
                    logger.info(f"[工作流] 成功为 '{name}' 初始化微信可见消息历史 (共 {len(scanned_history)} 条)")
                else:
                    local_pairs = [(m["role"], m["content"]) for m in local_context]
                    scanned_pairs = [(m["role"], m["content"]) for m in scanned_history]
                    
                    overlap_len = 0
                    max_possible_overlap = min(len(local_pairs), len(scanned_pairs))
                    for l in range(max_possible_overlap, 0, -1):
                        if local_pairs[-l:] == scanned_pairs[:l]:
                            overlap_len = l
                            break
                            
                    incremental_history = scanned_history[overlap_len:]
                    if incremental_history:
                        # 增量消息逐条写入本地记忆库
                        for item in incremental_history:
                            history_mgr.add_message(
                                session_key, name, item["role"], item["content"], is_group=False
                            )
                        logger.info(f"[工作流] 成功同步微信窗口增量消息到本地记忆库 '{name}' (共 {len(incremental_history)} 条)")
                
                # 重新拉取最新的 context_msgs
                return history_mgr.get_context(session_key, window_size=20)
    except Exception as e:
        err_msg = str(e) or e.__class__.__name__
        logger.warning(f"[工作流] 微信界面历史增量对齐同步异常: {err_msg}")
        
    return context_msgs



