import os
import time
import random
import re
import ctypes
import hashlib
import json
import logging
import threading
from typing import Optional, List, Dict
from pathlib import Path

import win32gui
import win32con
import uiautomation as uia
import pyperclip

from src.uia.elements import WxClass, WxName, RESOLUTION_PARAMS
from src.uia.session import parse_session_name, clean_session_name
from src.uia.message import parse_message
from src.uia.retry import random_delay, try_click, exists_with_timeout, smooth_click_at

logger = logging.getLogger("WeChatDriver")


class WeChatNavigationMixin:
    def _check_and_click_back_button(self) -> bool:
        """【4.1.9+ 适配】检查是否存在覆盖模式下的左上角“返回”按钮，若存在则点击返回会话列表"""
        try:
            if not self.hwnd:
                return False

            root = self.root
            if not root:
                return False

            # 宽屏模式直接跳过以提升性能
            session_list = self._find_session_list()
            if session_list:
                try:
                    sl_rect = session_list.BoundingRectangle
                    if sl_rect and (sl_rect.right - sl_rect.left) > 100 and (sl_rect.bottom - sl_rect.top) > 100:
                        return False
                except Exception:
                    pass

            win_left, win_top, _, _ = win32gui.GetWindowRect(self.hwnd)
            back_btn = None
            
            from src.utils.safe_uia import safe_walk_control, safe_control_type, safe_bounding_rect, safe_class_name, safe_get_name
            from src.uia.retry import get_dpi_scale
            _s = get_dpi_scale()

            # 🌟 [性能优化] 合并为单次遍历，最大深度设为 5，优先匹配物理范围进行高效初筛
            for ctrl, _ in safe_walk_control(root, max_depth=5):
                try:
                    if safe_control_type(ctrl) != 'ButtonControl':
                        continue
                    
                    rect = safe_bounding_rect(ctrl)
                    if not rect:
                        continue
                        
                    # 头部的返回按钮必须局限在窗口左上角的一定矩形区域内
                    is_in_header = (win_left <= rect.left <= win_left + 250) and (win_top <= rect.top <= win_top + 80)
                    if not is_in_header:
                        continue

                    ctrl_name = safe_get_name(ctrl)
                    # 1. 匹配 Name="返回"
                    if ctrl_name == '返回':
                        back_btn = ctrl
                        logger.info(f"[UIA] 快速匹配 Name 定位到返回按钮, 坐标: ({rect.left}, {rect.top})")
                        break

                    # 2. 匹配相对坐标与尺寸边界（避开侧栏 Tab 按钮区）
                    if (win_left + 45 <= rect.left <= win_left + 200) and 5 < (rect.right - rect.left) < 100 and 5 < (rect.bottom - rect.top) < 100:
                        back_btn = ctrl
                        logger.info(f"[UIA] 相对坐标匹配到返回按钮, 坐标: ({rect.left}, {rect.top}), 类名: {safe_class_name(ctrl)}")
                        break

                    # 3. 匹配 mmui::XImage 类名
                    ctrl_cls = safe_class_name(ctrl)
                    if ctrl_cls == 'mmui::XImage' and (rect.right - rect.left) > 5 and (rect.bottom - rect.top) > 5:
                        back_btn = ctrl
                        logger.info(f"[UIA] ClassName 匹配到返回按钮, 坐标: ({rect.left}, {rect.top})")
                        break
                except Exception:
                    continue

            if back_btn:
                logger.info("[UIA] 点击返回按钮退回会话列表")
                from src.uia.input_guard import uia_lock
                with uia_lock("检测到窄屏模式，正在返回会话列表...", hwnd=self.hwnd):
                    smooth_click_at(back_btn)
                    time.sleep(0.6)
                return True
            return False
        except Exception as e:
            logger.debug(f"[_check_and_click_back_button] 异常: {e}")
            return False

    def _find_session_list(self):
        """查找会话列表控件"""
        root = self.root
        if not root:
            return None

        # 🌟 0. 微信 4.1.7+ 标准：优先通过 AutomationId 与 Name 直连定位列表，秒级返回，绝不挂起
        try:
            lst = root.ListControl(AutomationId="session_list")
            if lst.Exists(0.05):
                return lst
        except Exception:
            pass

        try:
            lst = root.ListControl(Name=WxName.SESSION_LIST)
            if lst.Exists(0.05):
                return lst
        except Exception:
            pass

        # 1. 尝试按原先精准规则查找
        for cls in ['mmui::XRecyclerTableView', 'mmui::XTableView']:
            lst = self._walk_find('ListControl', class_name=cls, name=WxName.SESSION_LIST, max_depth=6)
            if lst: return lst
        lst = self._walk_find('ListControl', name=WxName.SESSION_LIST, max_depth=6)
        if lst: return lst
        
        # 2. 兜底一：按 Table 类名查找 ListControl
        for cls in ['mmui::XRecyclerTableView', 'mmui::XTableView']:
            lst = self._walk_find('ListControl', class_name=cls, max_depth=6)
            if lst: return lst

        # 3. 兜底二：前四层所有 ListControl 特征过滤
        try:
            candidate_lists = []
            stack = [(root, 0)]
            while stack:
                curr, depth = stack.pop()
                if curr.ControlTypeName == 'ListControl':
                    candidate_lists.append(curr)
                if depth < 4:
                    for child in curr.GetChildren():
                        stack.append((child, depth + 1))
            
            if candidate_lists:
                best_lst = None
                max_children = -1
                for l in candidate_lists:
                    try:
                        child_count = len(l.GetChildren())
                        rect = l.BoundingRectangle
                        width = rect.right - rect.left
                        if 150 < width < 500:
                            if child_count > max_children:
                                max_children = child_count
                                best_lst = l
                    except Exception:
                        pass
                if best_lst:
                    logger.info(f"[UIA] 定位到主会话列表 (宽度={best_lst.BoundingRectangle.right - best_lst.BoundingRectangle.left})")
                    return best_lst
        except Exception as e_list:
            logger.debug(f"[UIA] 兜底扫描异常: {e_list}")

        # 4. 执行刷新重试
        try:
            logger.info("[UIA] 未找到会话列表，刷新无障碍树...")
            from src.uia.startup_flow import force_accessibility_refresh
            force_accessibility_refresh(self.hwnd, self.root, escalate=False)
        except Exception as e:
            logger.debug(f"[UIA] 列表刷新异常: {e}")

        # 5. 刷新后再次尝试上述规则
        for cls in ['mmui::XRecyclerTableView', 'mmui::XTableView']:
            lst = self._walk_find('ListControl', class_name=cls, name=WxName.SESSION_LIST, max_depth=6)
            if lst: return lst
        lst = self._walk_find('ListControl', name=WxName.SESSION_LIST, max_depth=6)
        if lst: return lst
        for cls in ['mmui::XRecyclerTableView', 'mmui::XTableView']:
            lst = self._walk_find('ListControl', class_name=cls, max_depth=6)
            if lst: return lst
        return None

    def _ensure_chat_page(self, force: bool = False):
        """确保当前在聊天主页"""
        from src.uia.modules.navigation_page import ensure_chat_page_impl
        return ensure_chat_page_impl(self, force)

    def _get_edit_control(self, who: str) -> Optional[uia.EditControl]:
        """获取聊天文本输入框，委托至 helper 实现"""
        from src.uia.modules.edit_helper import get_edit_control_impl
        return get_edit_control_impl(self, who)

    def _verify_chat_switched(self, session_name: str, real_name: Optional[str] = None, wxid: Optional[str] = None) -> bool:
        """验证是否成功切换到目标聊天，委托至 helper 实现"""
        from src.uia.modules.edit_helper import verify_chat_switched_impl
        return verify_chat_switched_impl(self, session_name, real_name, wxid)

    def CloseActiveChat(self, check_last_msg: bool = True) -> bool:
        """安全释放当前活跃聊天焦点"""
        try:
            root = self.root
            if not root:
                logger.info("[UIA] 微信窗口 root 未就绪，终止 CloseActiveChat")
                return True
            edit_ctrl = None
            chat_container = root.GroupControl(ClassName='mmui::ChatDetailView')
            if not chat_container.Exists(0.15):
                logger.info("[UIA] 未检测到 ChatDetailView，当前无打开的聊天窗口，无需释放焦点")
                return True

            candidate = chat_container.EditControl(ClassName="mmui::ChatInputField", searchDepth=8)
            if candidate.Exists(0.1):
                edit_ctrl = candidate
            else:
                from src.utils.safe_uia import safe_get_children, safe_get_name
                stack = [(chat_container, 0)]
                while stack:
                    curr, depth = stack.pop()
                    if depth > 0:
                        try:
                            if curr.ControlTypeName == 'EditControl':
                                ctrl_name = safe_get_name(curr)
                                if ctrl_name and not any(k in ctrl_name for k in ("搜索", "Search", "微信号", "wxid")):
                                    edit_ctrl = curr
                                    break
                        except Exception:
                            pass
                    if depth < 8:
                        children = safe_get_children(curr)
                        if children:
                            for child in reversed(children):
                                stack.append((child, depth + 1))

            if not edit_ctrl or not edit_ctrl.Exists(0.15):
                logger.info("[UIA] 未检测到活跃的聊天输入框，无需释放焦点")
                return True

            session_name = edit_ctrl.Name or ""
            # 清理后缀
            import re
            session_name = re.sub(r'\s+按住.*$', '', session_name)
            session_name = re.sub(r'\(\d+\)$', '', session_name)
            session_name = session_name.strip()

            if not session_name:
                logger.info("[UIA] 无活跃会话名称，无需关闭")
                return True

            logger.info(f"[UIA] 准备释放活跃会话 '{session_name}' 的聊天焦点")

            # 2. 如果开启了最新消息校验
            if check_last_msg:
                last_msgs = self.get_all_messages(context_count=1, session_name=session_name)
                if last_msgs:
                    last_msg_item = last_msgs[-1]
                    if isinstance(last_msg_item, (list, tuple)) and len(last_msg_item) >= 2:
                        sender, content = last_msg_item[0], last_msg_item[1]
                    else:
                        sender, content = "未知", str(last_msg_item)
                    is_friend_sender = sender not in (self._nickname or "我", "我", "自己", "SYS", "Time", "Recall", "GREET")
                    if is_friend_sender:
                        logger.info(f"[UIA] 释放焦点校验：检测到最新一条消息来自好友 '{sender}'，为防新消息被吞，拒绝关闭当前聊天窗口！内容: '{content}'")
                        return False

            # 3. 避免再次物理点击列表中当前选中的联系人本身，以彻底防止触发微信双击弹出独立聊天窗口的特性。
            # 既然当前活跃聊天已经是目标会话，且最新消息非好友发送，已满足关闭/释放焦点的要求，无需重复物理点击，直接返回 True 即可。
            logger.info(f"[UIA] 释放焦点校验：最新消息非好友发送，且当前活跃聊天已是 '{session_name}'，无需重复物理点击自身，已安全释放焦点")
            return True
        except Exception as e:
            logger.error(f"[UIA] 关闭当前活跃聊天窗口异常: {e}")
            return False

    def ClearChatFocus(self, session_name: str, check_last_msg: bool = True) -> bool:
        """【xm-bot4核心】释放聊天焦点，退回列表或点击当前会话"""
        try:
            from src.uia.session import clean_session_name
            session_name = clean_session_name(session_name)
            if not session_name:
                return False

            if self._check_and_click_back_button(): return True

            # 确保微信窗口在前台，防止物理点击失效或误触其他窗口
            import ctypes
            if ctypes.windll.user32.GetForegroundWindow() != self.hwnd:
                from src.uia.retry.window_ops import ensure_wechat_foreground
                logger.info(f"[UIA] 释放焦点前，强制置顶微信窗口 hwnd={self.hwnd}")
                ensure_wechat_foreground(self.hwnd)
                time.sleep(0.2)

            return self.CloseActiveChat(check_last_msg=check_last_msg)
        except Exception as e:
            logger.error(f"[UIA] 释放焦点异常: {e}")
            return False

    def ChatWith(self, session_name: str, lock_input: bool = False, foreground: bool = False, msg_hint: str = "", wxid: str = None) -> bool:
        """切换到指定会话"""
        # 🛡️ 微信号入参智能自愈：如果 session_name 是 wxid_ 开头的原始微信号，通过 contacts_cache 实时反查其真实昵称/备注
        # 从而避免在微信搜索框输入微信号导致搜索失败的问题
        if session_name and (session_name.startswith("wxid_") or "@chatroom" in session_name):
            if not wxid:
                wxid = session_name
            try:
                from src.utils.contacts_cache import contacts_cache
                _bot_wxid = getattr(self, "bot_wxid", None) or getattr(self, "_wxid", None) or "main"
                is_group = "@chatroom" in session_name
                resolved_name = contacts_cache.find_name_with_db_sync(_bot_wxid, session_name, is_group=is_group)
                if resolved_name:
                    session_name = resolved_name
            except Exception as e_cache:
                logger.debug(f"[UIA] ChatWith 反查微信号昵称异常: {e_cache}")
        
        # 再次校验，如果 wxid 存在，但 session_name 依然是微信号，根据 wxid 再次反查
        if wxid and (wxid.startswith("wxid_") or "@chatroom" in wxid) and session_name == wxid:
            try:
                from src.utils.contacts_cache import contacts_cache
                _bot_wxid = getattr(self, "bot_wxid", None) or getattr(self, "_wxid", None) or "main"
                is_group = "@chatroom" in wxid
                resolved_name = contacts_cache.find_name_with_db_sync(_bot_wxid, wxid, is_group=is_group)
                if resolved_name:
                    session_name = resolved_name
            except Exception as e_cache2:
                logger.debug(f"[UIA] ChatWith 精确反查真实名称异常: {e_cache2}")

        from src.uia.modules.navigation_helper import chat_with_impl
        return chat_with_impl(self, session_name, lock_input, foreground, msg_hint=msg_hint, wxid=wxid)

    def get_chat_window_type(self, who: str = "") -> str:
        """获取当前聊天窗口类型"""
        from src.uia.modules.session_type_helper import get_chat_window_type_impl
        return get_chat_window_type_impl(self, who)

    def detect_and_cache_session_type(self, session_name: str):
        """自动检测并缓存当前会话的真实类型"""
        from src.uia.modules.session_type_helper import detect_and_cache_session_type_impl
        return detect_and_cache_session_type_impl(self, session_name)
    def _search_and_click(self, session_name: str, wxid: str = None) -> bool:
        """在微信搜索栏中搜索并点击进入指定会话的驱动代理"""
        from src.uia.modules.search_helper import search_and_click_impl
        return search_and_click_impl(self, session_name, wxid=wxid)
    def PinSession(self, session_name: str) -> bool:
        """【xm-bot4核心】置顶指定的会话（如果尚未置顶）"""
        from src.uia.modules.navigation_pin import pin_session_impl
        return pin_session_impl(self, session_name)
