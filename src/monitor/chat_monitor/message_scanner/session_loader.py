import time
import hashlib
import asyncio
import logging
from src.utils.uia_task_runner import run_uia_with_timeout

logger = logging.getLogger(__name__)

class SessionLoaderMixin:
    """获取会话列表与视口外未读消息跳转巡检"""

    async def _fetch_and_navigate_sessions(self, active_name: str, active_last_msgs: list, user_active_now: bool) -> list:
        # 🌟 【UIA 排他锁避让】若当前其它前台交互动作（如模拟按键录音）已开启排他锁，
        # 立即跳过 UIA 会话加载，彻底杜绝在物理按键按下期间因并发调用 UIA 导致系统挂起超时的 Bug！
        try:
            from src.uia.input_guard import uia_lock
            if uia_lock.is_locked:
                logger.debug("[监控] 检测到 UIA 排他锁已开启，跳过 _fetch_and_navigate_sessions 物理会话分析")
                return []
        except Exception as e_lock:
            logger.debug(f"[监控] 避让检测 UIA 锁状态异常: {e_lock}")

        sessions = None
        is_wcdb_active = False
        if hasattr(self, "_wcdb_session_monitor") and self._wcdb_session_monitor:
            try:
                is_wcdb_active = self._wcdb_session_monitor.is_active()
            except Exception:
                pass

        # 1. 极速读取当前屏幕上可见的 UIA 会话列表（限制 20 条以保证极高性能）
        uia_sessions = []
        try:
            uia_sessions = await run_uia_with_timeout(self.driver.get_latest_sessions, 10.0, 20)
        except Exception as uia_err:
            logger.debug(f"[监控] 获取 UIA 可见会话超时或异常: {uia_err}")

        # 2. 如果 WCDB 双引擎处于活跃状态，拉取数据库最新会话，执行最大值属性融合
        if is_wcdb_active:
            try:
                db_sessions = self._wcdb_session_monitor.get_latest_sessions_from_db(limit=80)
                if db_sessions:
                    db_map = {s["name"]: s for s in db_sessions}
                    for uia_s in uia_sessions:
                        name = uia_s.get("name")
                        if name in db_map:
                            db_s = db_map[name]
                            # 取两者未读数和At状态的最大值/并集，以杜绝 SQLite WAL 刷盘滞后带来的状态漏感知
                            db_s["unread"] = max(db_s.get("unread", 0), uia_s.get("unread", 0))
                            db_s["isAt"] = db_s.get("isAt", False) or uia_s.get("isAt", False)
                            if uia_s.get("lastMessage"):
                                db_s["lastMessage"] = uia_s["lastMessage"]
                            if uia_s.get("lastTime"):
                                db_s["lastTime"] = uia_s["lastTime"]
                        else:
                            db_sessions.append(uia_s)
                    sessions = db_sessions
                    logger.debug(f"[监控] 双引擎融合成功，共 {len(sessions)} 条会话（UIA={len(uia_sessions)}, DB={len(db_sessions)}）")
            except Exception as wcdb_err:
                logger.error(f"[监控] 从 WCDB 数据库获取并融合会话异常: {wcdb_err}")
                sessions = None

        if sessions is None:
            # 说明未启用 WCDB，降级采用完整的 UIA 50 条遍历扫描
            if uia_sessions:
                sessions = uia_sessions
            else:
                try:
                    sessions = await run_uia_with_timeout(self.driver.get_latest_sessions, 15.0, 50)
                except Exception:
                    sessions = []
        
        now = time.time()
        # 初始化最后一次回正时间
        if not hasattr(self, "_last_tab_recovery"):
            self._last_tab_recovery = now

        # 如果当前读不到列表（可能在通讯录页）
        if not sessions:
            # 策略A：尝试红点扫描
            try:
                has_unread_sidebar = await run_uia_with_timeout(self.driver._check_sidebar_unread, 10.0)
            except (asyncio.TimeoutError, TimeoutError):
                has_unread_sidebar = 0
            
            # 策略B：心跳强力回正
            should_force_recovery = has_unread_sidebar
            
            if should_force_recovery:
                from src.utils.user_activity import is_user_active
                import win32gui as _win32gui_fc

                # 双重守卫：必须用户空闲且微信窗口是前台窗口，才允许自动切回聊天页
                _is_fg = (_win32gui_fc.GetForegroundWindow() == self.driver.hwnd)
                if _is_fg and not is_user_active(cooldown_ms=5000):
                    logger.info("[监控] 长时间未检测到会话且用户空闲，自动唤醒并切回聊天页...")
                    try:
                        await run_uia_with_timeout(self.driver._ensure_chat_page, 15.0, True)
                    except (asyncio.TimeoutError, TimeoutError):
                        pass
                    self._last_tab_recovery = now
                    # 切回后再读一次
                    try:
                        sessions = await run_uia_with_timeout(self.driver.get_latest_sessions, 15.0, 50)
                    except (asyncio.TimeoutError, TimeoutError):
                        sessions = []

        else:
            # 只要能读到列表，就重置回正计时器
            self._last_tab_recovery = now
            
            # ⚡️【活跃会话穿透】如果当前正停留在某个聊天会话，但该会话没在列表前 20 个里，追加进来以保证其实时监控
            if sessions and active_name and not user_active_now and not any(s.get('name') == active_name for s in sessions):
                from src.utils.contacts_cache import contacts_cache
                all_groups = contacts_cache.get_groups(self.account_id)
                is_group = any(g.get('name') == active_name for g in all_groups)
                
                sessions.append({
                    "id": int(hashlib.md5(active_name.encode()).hexdigest()[:8], 16),
                    "name": active_name,
                    "lastTime": "",
                    "lastMessage": active_last_msgs[-1][1] if active_last_msgs else "",
                    "unread": 0,
                    "isGroup": is_group,
                    "isPinned": False,
                    "isMuted": False,
                    "isAt": False,
                    "isOfficial": False,
                    "avatar": "",
                })

            # 初始化退让和挂起状态
            if not hasattr(self, "_pending_jump"):
                self._pending_jump = False
                self._jump_retry_count = 0
                self._original_check_interval = getattr(self, "_check_interval", 2.0)  # 默认回退到 2s

            # 探测视口外隐藏未读会话
            from src.utils.uia_task_runner import is_session_fused
            has_any_visible_unread = any(
                (s.get('unread', 0) > 0 or s.get('isAt', False)) 
                and not self.is_session_suspended(s.get('name', ''))
                and not is_session_fused(s.get('name', ''))
                for s in sessions
            )
            if not has_any_visible_unread:
                is_wcdb_active = False
                if hasattr(self, "_wcdb_session_monitor") and self._wcdb_session_monitor:
                    try:
                        is_wcdb_active = self._wcdb_session_monitor.is_active()
                    except Exception:
                        pass

                # 检查导航栏上的“微信”图标是否提示有未读消息/红点
                tabbar_unread = 0
                if not is_wcdb_active:
                    try:
                        tabbar_unread = await run_uia_with_timeout(self.driver.get_tabbar_chat_unread_count, 10.0)
                    except (asyncio.TimeoutError, TimeoutError):
                        logger.warning("[监控] 获取导航栏未读消息数超时，安全降级为 0")
                        tabbar_unread = 0
                    except Exception as e_unread:
                        logger.debug(f"[监控] 获取导航栏未读消息数异常: {e_unread}")
                        tabbar_unread = 0
                
                import win32gui
                from src.utils.user_activity import is_user_active
                
                # 如果当前有正在处理的自动回复任务，或者当前活跃的会话在队列或处理中，则不进行双击跳转以防御干扰
                has_active_reply_tasks = bool(self._processing) or bool(self._message_buffer)
                
                import app.state as app_state
                active_wxid = getattr(app_state, 'active_chat_wxid', None)
                is_active_chat_in_queue = False
                if active_name:
                    for k in [active_name, active_wxid]:
                        if k and (k in self._message_buffer or k in self._processing):
                            is_active_chat_in_queue = True
                            break
                
                should_jump = tabbar_unread > 0 and (now - getattr(self, "_last_tab_double_click_time", 0.0) > 1.5) and not has_active_reply_tasks and not is_active_chat_in_queue and not is_wcdb_active
                
                if should_jump:
                    self._pending_jump = True
                    is_wechat_foreground = (win32gui.GetForegroundWindow() == self.driver.hwnd)
                    
                    # 动态空闲冷却判定：退让重试期间，将空闲判定时间缩短为 1.2 秒（1200ms），更容易捕捉停顿
                    cooldown = 1200 if self._jump_retry_count > 0 else 3000
                    user_active = is_user_active(cooldown_ms=cooldown)
                    
                    allow_action = True
                    if user_active:
                        if self._jump_retry_count < 5:
                            # 避让期：暂不执行，增加重试次数，并将心跳缩短至 1.2 秒以实现高频检测
                            allow_action = False
                            self._jump_retry_count += 1
                            self._check_interval = 1.2
                            logger.info(f"[监控] 探测到隐藏未读，但检测到用户活跃。第 {self._jump_retry_count} 次退让，临时缩短心跳至 1.2s 以等待空闲空隙...")
                            from src.utils.status_overlay import status_overlay
                            status_overlay.update("客服避让", f"检测到 {tabbar_unread} 条隐藏未读消息，避让用户操作中... (第 {self._jump_retry_count} 次)", "新消息待回复", color=0x00A5FF)
                        else:
                            # 强行突破期：已连续避让多次，为了防止消息彻底漏回，强行执行跳转，但尽量减少干扰
                            allow_action = True
                            logger.warning(f"[监控] 已连续退让 {self._jump_retry_count} 次，用户持续活跃。为防新消息积压，执行强行跳转...")
                    
                    if allow_action:
                        # 执行跳转前，记录原前台窗口句柄以便跳转后恢复
                        fg_hwnd = win32gui.GetForegroundWindow()
                        logger.info(f"[监控] 探测到导航栏存在未读 ({tabbar_unread}条)，但可见列表中无未读。执行双击'微信'Tab跳转巡检视口外未读...")
                        self._last_tab_double_click_time = now
                        
                        from src.utils.status_overlay import status_overlay
                        status_msg = f"强行双击微信Tab巡检隐藏未读 (第 {self._jump_retry_count} 次)" if user_active else "双击微信Tab巡检隐藏未读..."
                        status_overlay.update("未读跳转", status_msg, "微信自动扫描", color=0xFF9900)
                        
                        force_action = bool(user_active and self._jump_retry_count >= 5)
                        from src.uia.input_guard import uia_lock as _uia_lock
                        with _uia_lock(f"发现 {tabbar_unread} 条隐藏未读，正在跳转巡检...", hwnd=getattr(self.driver, 'hwnd', None)):
                            jumped = await run_uia_with_timeout(self.driver.jump_to_next_unread, 10.0, force=force_action)
                        
                        if jumped:
                            await asyncio.sleep(0.6)
                            sessions = await run_uia_with_timeout(self.driver.get_latest_sessions, 15.0, 50)
                            
                        # 恢复状态与心跳间隔
                        self._pending_jump = False
                        self._jump_retry_count = 0
                        self._check_interval = self._original_check_interval
                        
                        # 尝试将焦点交还给原窗口
                        if fg_hwnd and fg_hwnd != self.driver.hwnd:
                            try:
                                import win32con
                                import win32api
                                # 物理按一下 Alt 以解除 SetForegroundWindow 的锁定限制
                                win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
                                win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
                                win32gui.SetForegroundWindow(fg_hwnd)
                                logger.info(f"[监控] 已成功将前台窗口还原至 hwnd={fg_hwnd}")
                            except Exception as e_restore:
                                logger.debug(f"[监控] 还原前台窗口异常 (忽略): {e_restore}")

                        # 滚动巡检双保险
                        has_unread_now = any(s.get('unread', 0) > 0 or s.get('isAt', False) for s in sessions) if sessions else False
                        if not has_unread_now and tabbar_unread > 0:
                            logger.info("[监控] 双击跳转后仍未在可见列表中检测到未读，尝试向下滚动会话列表巡检隐藏会话...")
                            from src.uia.input_guard import uia_lock as _uia_lock
                            with _uia_lock("正在滚动巡检隐藏未读会话...", hwnd=getattr(self.driver, 'hwnd', None)):
                                await run_uia_with_timeout(lambda: self.driver.scroll_sessions("down", times=3), 10.0)
                            await asyncio.sleep(0.5)
                            sessions = await run_uia_with_timeout(self.driver.get_latest_sessions, 15.0, 50)
                            self._did_scroll_down_this_turn = True
                else:
                    # 如果不需要跳转，且没有挂起的跳转任务，确保恢复原始的心跳间隔
                    if not self._pending_jump:
                        self._check_interval = self._original_check_interval

        # === 状态看板更新防护 ===
        # 只有在没有进行中的回复投递/处理任务时，才重置状态为“正在监控”
        has_unread_now = any(s.get('unread', 0) > 0 or s.get('isAt', False) for s in sessions) if sessions else False
        if not has_unread_now and not getattr(self, "_pending_jump", False):
            has_active_reply_tasks = bool(getattr(self, "_processing", [])) or bool(getattr(self, "_message_buffer", []))
            if not has_active_reply_tasks:
                from src.utils.status_overlay import status_overlay
                status_overlay.update("正在监控", "未检测到新消息，后台扫描中...", "守护中", color=0x00FF00)

        return sessions
