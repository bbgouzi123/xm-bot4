import time
import random
import re
import ctypes
import logging
from typing import Optional
import uiautomation as uia

from src.uia.elements import WxClass
from src.uia.session import clean_session_name, session_type_cache, parse_session_name
from src.uia.input_guard import uia_lock
from src.uia.retry import random_delay, exists_with_timeout
from src.uia.modules.session_type_helper import detect_and_cache_session_type_impl

logger = logging.getLogger("WeChatDriver.NavigationHelper")

def chat_with_impl(self, session_name: str, lock_input: bool = False, foreground: bool = False, msg_hint: str = "", wxid: str = None) -> bool:
    """切换到指定会话的实际实现（委托自 WeChatNavigationMixin）"""
    if not self.is_connected():
        return False
    
    session_name = clean_session_name(session_name)
    if not session_name:
        return False
    
    # 0. 过滤官方账号与系统非聊天会话，防止 UIA 检索卡死/乱操作
    non_chat_sessions = {
        "公众号", "订阅号消息", "服务号", "服务通知", "微信支付", 
        "腾讯新闻", "微信游戏", "微信支付商家助手", "小程序助手"
    }
    is_official = (session_name in non_chat_sessions) or (session_type_cache.get_type(session_name) == "official_account")
    if is_official:
        logger.info(f"[UIA] 目标会话 '{session_name}' 是公众号/系统非聊天会话，拒绝执行物理切换操作。")
        return False
        
    def _switch_impl():
        try:
            # 1. 检查当前是否已经是该聊天会话，避免不必要的动作
            edit_msg = self._get_edit_control(session_name)
            
            # 🚀【加载延迟容错】若未找到输入框，可能由于页面刚切换或处于高负载高延迟渲染状态，尝试短重试
            if not edit_msg:
                for attempt in range(4):
                    time.sleep(0.15)
                    edit_msg = self._get_edit_control(session_name)
                    if edit_msg:
                        break
            
            # 🚀【终极标题与任意输入框兜底】即使因为草稿或其他遮挡导致输入框 Name 匹配失败，
            # 但如果聊天详情容器已存在，顶部标题与目标匹配，且有任意输入框在，则直接判定已在目标会话
            if not edit_msg:
                try:
                    chat_container = self.root.GroupControl(ClassName='mmui::ChatDetailView', searchDepth=12)
                    if chat_container.Exists(0.15):
                        from src.uia.modules.edit_helper import _get_header_title_safely
                        header_title = _get_header_title_safely(chat_container)
                        if header_title:
                            clean_header = clean_session_name(header_title)
                            import re
                            clean_header_pure = re.sub(r'[（(]\d+[）)]$', '', clean_header).strip()
                            
                            def normalize_spaces(s: str) -> str:
                                return re.sub(r'\s+', ' ', s).strip()
                            
                            norm_header = normalize_spaces(clean_header_pure)
                            norm_search = normalize_spaces(session_name)
                            
                            if norm_search in norm_header or norm_header in norm_search:
                                any_edit = chat_container.EditControl(ClassName="mmui::ChatInputField", searchDepth=16)
                                if any_edit.Exists(0.15):
                                    logger.info(f"[UIA] 切换前终极标题兜底匹配通过：顶部标题为 '{header_title}' 且有输入框，判定已在会话 '{session_name}'")
                                    edit_msg = any_edit
                except Exception as check_ex:
                    logger.debug(f"[UIA] 切换前终极标题判定异常: {check_ex}")
            
            if edit_msg and edit_msg.Exists(0.5): 
                # 💡 【防同名会话串台】
                # 首先预检：即便当前窗口名相符且输入框存在，但如果左侧会话列表中该同名好友（或群）仍有未读红点（unread > 0），
                # 说明真正有新消息的那个同名好友窗口尚未被点开（当前聚焦在了同名的另一个已读会话上）。此时必须强行执行重新定位！
                has_unread_duplicate = False
                try:
                    session_list = self._find_session_list()
                    if session_list:
                        for item in session_list.GetChildren():
                            raw_name = (item.Name or "").strip()
                            if raw_name:
                                parsed = parse_session_name(raw_name, real_name=session_name)
                                if parsed and clean_session_name(parsed.get("name", "")) == clean_session_name(session_name):
                                    if parsed.get("unread", 0) > 0:
                                        has_unread_duplicate = True
                                        break
                except Exception as e_dup:
                    logger.debug(f"[UIA] 预检同名未读红点异常: {e_dup}")

                is_correct_session = True
                if wxid:
                    try:
                        import app.state as app_state
                        # 优先使用带 wxid 前缀的复合 Key 做精准隔离，如果没有则退回 session_name 兼容单开旧数据
                        active_wxid = app_state.name_to_active_wxid.get(f"{getattr(self, '_wxid', '')}:{session_name}") or app_state.name_to_active_wxid.get(session_name)
                        if active_wxid != wxid:
                            logger.info(f"[UIA] 当前虽处于会话 '{session_name}'，但期望 wxid '{wxid}' 与活跃缓存中的 wxid '{active_wxid}' 不一致，强制搜索切换")
                            is_correct_session = False
                    except Exception as e_cache:
                        logger.warning(f"[UIA] 校验活跃缓存 wxid 异常: {e_cache}")

                if is_correct_session and has_unread_duplicate:
                    logger.info(f"[UIA] 检测到会话列表中 '{session_name}' 仍有未读消息角标，判定为同名混淆会话，强制执行点击切换。")
                    is_correct_session = False
                
                if wxid and is_correct_session:
                    try:
                        from src.uia.modules.edit_helper import verify_chat_by_history
                        if not verify_chat_by_history(self, wxid):
                            logger.info(f"[UIA] 当前虽处于会话 '{session_name}'，但历史消息比对失败，判定为同名混淆会话，强制搜索切换")
                            is_correct_session = False
                    except Exception as e_hist:
                        logger.warning(f"[UIA] 校验当前页面历史消息异常: {e_hist}")
                # 💡 如果提示信息是多媒体占位符，不进行内容强行校验，因为多媒体不以文本形式存留在最近消息列表，会引发误判
                elif msg_hint and not any(p in msg_hint for p in ("[图片]", "[语音]", "[文件]", "[视频]", "[表情]", "[红包]", "[转账]", "[链接]")):
                    try:
                        recent_msgs = self.get_all_messages(parse_file=False, context_count=5, session_name=session_name)
                        if recent_msgs:
                            match_found = False
                            clean_hint = msg_hint.strip()
                            for sender, content in recent_msgs:
                                if content and clean_hint in content:
                                    match_found = True
                                    break
                            if not match_found:
                                logger.info(f"[UIA] 当前虽在名为 '{session_name}' 的页面，但最近消息中没有匹配到提示内容 '{clean_hint}'，判定为同名混淆会话，强行重新定位切换。")
                                is_correct_session = False
                    except Exception as e_check:
                        logger.warning(f"[UIA] 校验当前页面消息相似度异常: {e_check}")
                
                if is_correct_session:
                    # 💡 【防双击加固】只要当前聊天输入框已存在，说明已经聚焦，无论红点是否残留，微信原生都会处理。
                    # 绝对不要再碰任何 UI 列表项，直接跳过所有点击动作以绝对防御微信“意外双击弹出独立窗口”的顽疾！
                    import ctypes
                    if foreground and ctypes.windll.user32.GetForegroundWindow() != self.hwnd:
                        logger.info(f"[UIA] 当前已在会话 '{session_name}' 页面，仅执行微信前置置顶，跳过所有点击动作以绝对防御双击")
                        try:
                            from src.uia.retry import ensure_wechat_foreground
                            ensure_wechat_foreground(self.hwnd)
                        except Exception as w_ex:
                            logger.warning(f"[UIA] 切换前置顶微信窗口异常: {w_ex}")
                    else:
                        logger.info(f"[UIA] 当前已在会话 '{session_name}' 页面且微信已在前台，直接安全跳过点击")
                    return True
            
            # 2. 如果微信当前未激活，且用户正在操作鼠标键盘，非显式锁定时，优先避让以防抢鼠标焦点
            from src.utils.user_activity import is_user_active
            import ctypes
            is_fg = ctypes.windll.user32.GetForegroundWindow() == self.hwnd
            if not is_fg and not lock_input and is_user_active(cooldown_ms=2500):
                logger.info(f"[UIA] 切换会话 '{session_name}' 避让：用户正在操作，且当前微信处于后台（避让干扰）")
                return False
 
            # 3. 如果强制前台，才在前置处理置顶
            if foreground:
                try:
                    from src.uia.retry import ensure_wechat_foreground
                    ensure_wechat_foreground(self.hwnd)
                except Exception as e:
                    logger.warning(f"[UIA] ChatWith 前置顶激活窗口异常: {e}")
 
            random_delay(0.08, 0.15)
            # 🌟 [切换前扫尾防御]
            try:
                from src.monitor.chat_monitor.active_chat_helper import scan_and_enqueue_active_chat_before_switch
                scan_and_enqueue_active_chat_before_switch(self, session_name)
            except Exception as e_sweep:
                logger.debug(f"[UIA] 切换前活跃窗口扫尾异常: {e_sweep}")

            self._ensure_chat_page()
            random_delay(0.1, 0.2)
            
            def _find_and_click_in_visible_list(name: str) -> bool:
                session_list = self._find_session_list()
                if not session_list:
                    return False

                # 🌟 0. 基于抓取的 UIA 数据优化：微信 4.1.7+ 支持 AutomationId="session_item_会话名" 直连定位会话行
                # 优先采用 O(1) 精准查找，彻底省去遍历所有子节点的计算开销，极致防挂起
                try:
                    clean_n = clean_session_name(name)
                    for autoid in [f"session_item_{clean_n}", f"session_item_{name}", f"session_item_{wxid}" if wxid else ""]:
                        if not autoid: continue
                        item = session_list.ListItemControl(AutomationId=autoid)
                        if item.Exists(0.05):
                            logger.info(f"[UIA] 列表匹配成功！通过 AutomationId '{autoid}' 极速定位会话项")
                            from src.uia.retry.clicks import try_click
                            try:
                                item.ScrollIntoView()
                                random_delay(0.05, 0.1)
                            except Exception:
                                pass
                            try_click(item, max_retries=2, delay=0.15)
                            random_delay(0.3, 0.4)
                            if self._verify_chat_switched(name, wxid=wxid):
                                return True
                except Exception as e_fast:
                    logger.debug(f"[UIA] 极速 AutomationId 匹配列表项目异常: {e_fast}")

                candidates = []
                for item in session_list.GetChildren():
                    raw_name = (item.Name or "").strip()
                    if raw_name:
                        parsed = parse_session_name(raw_name, real_name=name)
                        if parsed and clean_session_name(parsed.get("name", "")) == clean_session_name(name):
                            score = 0
                            if parsed.get("unread", 0) > 0:
                                score += 50
                            last_msg = parsed.get("lastMessage", "")
                            if msg_hint and last_msg:
                                if msg_hint.strip() in last_msg or last_msg in msg_hint.strip():
                                    score += 100
                            
                            # 🌟 精准匹配防同名冲突：如果左侧列表项的 AutomationId 匹配/包含 wxid，赋予超级高分
                            item_autoid = getattr(item, "AutomationId", "") or ""
                            if wxid and (wxid in item_autoid or item_autoid == f"session_item_{wxid}"):
                                score += 500
                                logger.info(f"[UIA] 左侧列表匹配到目标 wxid: '{wxid}'，AutomationId: '{item_autoid}'，评分加 500")

                            candidates.append((score, item))
                
                if candidates:
                    candidates.sort(key=lambda x: x[0], reverse=True)
                    best_score, best_item = candidates[0]
                    logger.info(f"[UIA] 切换定位会话 '{name}'，发现 {len(candidates)} 个同名候选。选择最高得分 ({best_score}) 项执行切换。")
                    
                    from src.uia.retry.clicks import try_click
                    try:
                        best_item.ScrollIntoView()
                        random_delay(0.05, 0.1)
                    except Exception:
                        pass
                    try_click(best_item, max_retries=2, delay=0.15)
                    
                    random_delay(0.4, 0.5)
                    if self._verify_chat_switched(name, wxid=wxid): 
                        return True
                    
                    import ctypes
                    if ctypes.windll.user32.GetForegroundWindow() != self.hwnd:
                        from src.uia.retry.window_ops import ensure_wechat_foreground
                        ensure_wechat_foreground(self.hwnd)
                        random_delay(0.4, 0.5)
                        if self._verify_chat_switched(name, wxid=wxid):
                            return True
                    
                    if ctypes.windll.user32.GetForegroundWindow() == self.hwnd:
                        logger.info(f"[UIA] 静默切换会话 '{name}' 未生效，执行物理点击兜底")
                        self._click_session_physically(best_item)
                        random_delay(0.25, 0.45)
                        if self._verify_chat_switched(name, wxid=wxid): 
                            return True
                return False

            # 3. 尝试在当前可见会话列表中查找并静默切换
            if _find_and_click_in_visible_list(session_name):
                return True
                        
            # 4. 前面都未成功切换，说明列表里没有，需要使用全局搜索切换。
            # 搜索切换需要使用模拟键盘输入，因此微信窗口必须处于最前台获取焦点。
            # 如果当前不在前台，则在这里强行置顶微信，然后再调用搜索。
            import ctypes
            if ctypes.windll.user32.GetForegroundWindow() != self.hwnd:
                try:
                    from src.uia.retry import ensure_wechat_foreground
                    ensure_wechat_foreground(self.hwnd)
                except Exception as e:
                    logger.warning(f"[UIA] 搜索前置顶微信窗口异常: {e}")
            
            # 3.5 优先尝试通过双击侧边栏“微信”Tab做未读跳转定位（未读消息不多时，如 <= 5 条）
            # 💡 【避让用户操作】若用户当前活跃，跳过双击侧边栏 Tab 动作，防止物理双击与用户抢夺鼠标及产生 10s 长超时挂起
            try:
                from src.utils.user_activity import is_user_active
                if not is_user_active(cooldown_ms=3000):
                    tabbar_unread = self.get_tabbar_chat_unread_count()
                    if 0 < tabbar_unread <= 5:
                        logger.info(f"[UIA] 当前左侧导航栏未读数较少 ({tabbar_unread} 条)，优先尝试通过双击未读跳转定位: '{session_name}'...")
                        for attempt in range(tabbar_unread):
                            if self.jump_to_next_unread():
                                random_delay(0.4, 0.6)
                                # 双击跳转后，优先识别可见列表中的元素并执行物理/静默点击
                                if _find_and_click_in_visible_list(session_name):
                                    logger.info(f"[UIA] 通过双击消息图标未读跳转并在列表中成功识别点击: '{session_name}'")
                                    return True
                                if self._verify_chat_switched(session_name, wxid=wxid):
                                    logger.info(f"[UIA] 通过双击消息图标未读跳转，成功定位并切换至会话: '{session_name}'")
                                    return True
                            else:
                                break
            except Exception as e_jump:
                logger.debug(f"[UIA] 尝试双击未读跳转定位异常: {e_jump}")

            return self._search_and_click(session_name, wxid=wxid)

        except Exception as e:
            logger.error(f"[UIA] 切换会话异常: {e}")
            return False
 
    # 如果调用方显式指定了加锁，并且当前没有被外层锁包裹，则以加锁方式运行，否则无锁运行
    if lock_input and not uia_lock.is_locked:
        with uia_lock(f"正在切换到会话: {session_name}", hwnd=self.hwnd):
            res = _switch_impl()
    else:
        res = _switch_impl()
 
    if res:
        detect_and_cache_session_type_impl(self, session_name)
        try:
            import app.state as app_state
            app_state.active_chat_name = session_name
            if wxid:
                app_state.name_to_active_wxid[session_name] = wxid
                app_state.name_to_active_wxid[f"{getattr(self, '_wxid', '')}:{session_name}"] = wxid
                app_state.active_chat_wxid = wxid
            else:
                app_state.active_chat_wxid = app_state.name_to_active_wxid.get(session_name) or app_state.name_to_active_wxid.get(f"{getattr(self, '_wxid', '')}:{session_name}")
        except Exception:
            pass
    return res

