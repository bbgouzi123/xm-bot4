"""会话列表读取与滚动。"""
import logging
import time
from typing import List

from src.uia.session import parse_session_name, is_group_msg_format
from src.utils.safe_uia import safe_walk_control, safe_get_children, safe_control_type, safe_get_name

logger = logging.getLogger("WeChatDriver")


class WeChatCoreSessionsMixin:
    # ==================== 会话操作 ====================

    def get_latest_sessions(self, limit: int = 20, prepare: bool = False) -> List[dict]:
        """获取当前可见的最新会话列表。

        - ``prepare=False``（默认）：非侵入式，不抢焦点、不点侧栏；用于后台监控等场景。
        - ``prepare=True``：仅当当前读不到会话列表、且 ``user_activity`` 判定用户已空闲（默认 3s 无键鼠）时，
          才 ``SwitchToThisWindow`` + ``_ensure_chat_page``；已能读到列表时不反复置前，减少抢焦点。
        """
        if not self.is_connected():
            return []

        # 🌟 优化：把局部 imports 和 contacts_cache 的提取提到大循环外部，防止在处理数十个会话项时发生严重的重复运算与卡死
        from src.uia.session import session_type_cache, SYSTEM_ACCOUNTS
        from src.utils.contacts_cache import contacts_cache

        bot_wxid = getattr(self, 'bot_wxid', '') or getattr(self, '_wxid', '')
        friend_names = set()
        group_names = set()
        if bot_wxid:
            try:
                friends_list = contacts_cache.get_friends(bot_wxid) or []
                groups_list = contacts_cache.get_groups(bot_wxid) or []
                for f in friends_list:
                    n = (f.get('name') or '').strip()
                    r = (f.get('remark') or '').strip()
                    if n:
                        friend_names.add(n)
                    if r:
                        friend_names.add(r)
                group_names = {(g.get('name') or '').strip() for g in groups_list} - {''}
            except Exception as cache_err:
                logger.debug(f"[UIA] 读取好友/群聊缓存异常: {cache_err}")

        try:
            session_list = self._find_session_list()
            if prepare and not session_list:
                from src.utils.user_activity import is_user_active

                if not is_user_active():
                    self.SwitchToThisWindow()
                    self._ensure_chat_page()
                    time.sleep(0.15)
                    session_list = self._find_session_list()

            if not session_list:
                logger.debug("未找到会话列表控件")
                return []

            sessions = []
            items = safe_get_children(session_list)
            for item in items:
                if len(sessions) >= limit:
                    break
                try:
                    raw_name = safe_get_name(item)
                    if not raw_name.strip():
                        continue

                    parsed = None
                    clean_name = None
                    # 1. 尝试使用竞品的多端高精度控件解析法
                    try:
                        import uiautomation as uia
                        import re
                        import hashlib
                        
                        # 优先使用 ButtonControl 提取会话的纯净名字
                        clean_name = None
                        btn = None
                        try:
                            for sub_ctrl in safe_get_children(item):
                                if safe_control_type(sub_ctrl) == "ButtonControl":
                                    btn = sub_ctrl
                                    break
                        except Exception:
                            pass
                        if btn:
                            candidate_name = safe_get_name(btn).strip()
                            if candidate_name and candidate_name in raw_name:
                                clean_name = candidate_name
                        
                        if not clean_name:
                            clean_name = re.sub(r'(\d+条新消息|\d+条未读|已置顶)$', '', raw_name).strip()

                        # 优化：通过扁平遍历代替深层 DFS walk，避免大量 COM 属性 RPC 带来的累积卡死
                        text_controls = []
                        for child in safe_get_children(item):
                            c_type = safe_control_type(child)
                            if c_type == "TextControl":
                                text_controls.append(child)
                            # 深度限制为 2，对直接子节点展开一层
                            for sub_child in safe_get_children(child):
                                if safe_control_type(sub_child) == "TextControl":
                                    text_controls.append(sub_child)

                        if len(text_controls) > 1:
                            second_text = safe_get_name(text_controls[1]).strip()
                            time_str = ""
                            last_message = ""
                            is_at = False

                            if second_text.startswith('@') or "[有人@我]" in raw_name:
                                is_at = True
                                if len(text_controls) >= 3:
                                    time_str = safe_get_name(text_controls[2]).strip()
                                if len(text_controls) >= 4:
                                    last_message = safe_get_name(text_controls[3]).strip()
                            else:
                                time_str = second_text
                                if len(text_controls) >= 3:
                                    last_message = safe_get_name(text_controls[2]).strip()

                            # 安全过滤：缺少内容或时间的会话直接进入兜底
                            if not time_str or not last_message:
                                raise ValueError("Missing time or last message in sub-controls")

                            # 过滤撤回的消息
                            if last_message.endswith("撤回了一条消息"):
                                raise ValueError("Recalled message bypassed in sub-controls")

                            # 处理 [有人@我] 文本前缀
                            if last_message.startswith("[有人@我]"):
                                last_message = re.sub(r'^\[有人@我\]', '', last_message).strip()
                                is_at = True

                            # 提取未读数
                            unread = 0
                            unread_match = re.search(r'(\d+)条新消息$', raw_name)
                            if not unread_match:
                                unread_match = re.search(r'(\d+)条未读$', raw_name)
                            if unread_match:
                                unread = int(unread_match.group(1))
                            else:
                                # 子控件兜底修正
                                for child in safe_get_children(item):
                                    if safe_control_type(child) == "TextControl" and safe_get_name(child):
                                        c_name = safe_get_name(child).strip()
                                        if c_name.isdigit() and 1 <= len(c_name) <= 3:
                                            unread = int(c_name)
                                            break
                                        elif c_name in ("...", "99+"):
                                            unread = 99
                                            break

                            session_id = int(hashlib.md5(clean_name.encode()).hexdigest()[:8], 16)
                            
                            # 判断群聊与公众号
                            is_in_friends = False
                            is_in_groups = False
                            if bot_wxid:
                                if clean_name in friend_names:
                                    is_in_friends = True
                                elif clean_name in group_names:
                                    is_in_groups = True

                            cached_type = session_type_cache.get_type(clean_name)
                            if is_in_groups:
                                is_group = True
                                is_official = False
                                session_type_cache.set_type(clean_name, "group")
                            elif is_in_friends:
                                is_group = False
                                is_official = False
                                session_type_cache.set_type(clean_name, "friend")
                            elif cached_type:
                                is_group = (cached_type == "group")
                                is_official = (cached_type == "official_account")
                            else:
                                is_group = (
                                    ('群' in clean_name and len(clean_name) > 2) or
                                    clean_name in ('公众号', '服务号') or
                                    is_group_msg_format(last_message) or
                                    '、' in clean_name
                                )
                                is_official = clean_name in ('公众号', '服务号') or clean_name in SYSTEM_ACCOUNTS

                            parsed = {
                                "id": session_id,
                                "name": clean_name,
                                "lastTime": time_str,
                                "lastMessage": last_message,
                                "unread": unread,
                                "isGroup": is_group,
                                "isPinned": "已置顶" in raw_name,
                                "isMuted": "消息免打扰" in raw_name,
                                "isAt": is_at,
                                "isOfficial": is_official,
                                "avatar": "",
                            }
                    except Exception as sub_ex:
                        logger.debug(f"[UIA] 子控件 WalkControl 提取会话发生异常，转为正则解析兜底: {sub_ex}")
                        parsed = None

                    # 2. 如果子控件提取失败，使用我们原先的 parse_session_name 正则法进行兜底
                    if not parsed:
                        parsed = parse_session_name(raw_name, real_name=clean_name)
                        if parsed:
                            if parsed.get("unread", 0) == 0:
                                for child in safe_get_children(item):
                                    try:
                                        if safe_control_type(child) == "TextControl" and safe_get_name(child):
                                            c_name = safe_get_name(child).strip()
                                            if c_name.isdigit() and 1 <= len(c_name) <= 3:
                                                parsed["unread"] = int(c_name)
                                                logger.info(f"[UIA未读数修正] 会话 '{parsed['name']}' 识别到子控件未读数 Name='{c_name}'，修正未读数为 {c_name}")
                                                break
                                            elif c_name in ("...", "99+"):
                                                parsed["unread"] = 99
                                                logger.info(f"[UIA未读数修正] 会话 '{parsed['name']}' 识别到子控件未读标记 '{c_name}'，修正未读数为 99")
                                                break
                                    except Exception:
                                        continue

                    if parsed:
                        sessions.append(parsed)
                except Exception as item_ex:
                    logger.warning(f"处理单条会话 UIA 项目发生异常: {item_ex}")
                    continue

            return sessions

        except Exception as e:
            logger.error(f"获取会话列表失败: {e}")
            return []

    def scroll_sessions(self, direction: str = 'down', times: int = 5) -> bool:
        """滚动会话列表，用于实现前端无限加载（须先置前微信并让列表获得焦点，滚轮才生效）。"""
        if not self.is_connected():
            return False

        try:
            self.SwitchToThisWindow()
            time.sleep(0.12)
            self._ensure_chat_page()
            time.sleep(0.12)
            session_list = self._find_session_list()
            if not session_list:
                return False
            try:
                session_list.SetFocus()
            except Exception:
                pass
            time.sleep(0.08)

            if direction == 'down':
                session_list.WheelDown(wheelTimes=times, waitTime=0.12)
            else:
                session_list.WheelUp(wheelTimes=times, waitTime=0.12)

            return True
        except Exception as e:
            logger.error(f"滚动会话列表失败: {e}")
            return False

    def jump_to_next_unread(self, force: bool = False) -> bool:
        """双击左侧 TabBar 的'微信'按钮，利用微信原生机制自动跳转到下一个未读会话。"""
        import time
        import logging
        import uiautomation as uia
        import win32api
        import win32con
        import ctypes

        logger = logging.getLogger(__name__)
        if not getattr(self, "hwnd", None):
            return False

        # 强制设置当前进程 DPI 感知，防止打包后在用户高 DPI 缩放电脑下的坐标被系统虚拟化偏离
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

        try:
            wechat_win = uia.ControlFromHandle(self.hwnd)
            if not wechat_win.Exists(0.5):
                return False

            tabbar = wechat_win.ToolBarControl(ClassName="mmui::MainTabBar")
            if not tabbar.Exists(0.5):
                # 兼容部分微信高版本 ClassName 改变或无 ClassName 的情况
                tabbar = wechat_win.Control(searchDepth=3, ControlTypeName="ToolBarControl")
                if not tabbar.Exists(0.5):
                    return False

            chat_btn = None
            # 获取所有按钮，包括 ButtonControl 和 Custom 类型的按钮控件（以应对不同微信版本）
            buttons = [c for c in tabbar.GetChildren() if c.ControlTypeName in ("ButtonControl", "CustomControl", "Control")]
            for btn in buttons:
                c_name = btn.Name or ""
                # 兼容 聊天, 微信, Chat, 消息, Chats 等所有消息图标名称
                if any(k in c_name for k in ["微信", "Chat", "聊天", "消息", "Chats"]):
                    chat_btn = btn
                    break
            if not chat_btn and buttons:
                chat_btn = buttons[0]

            if not chat_btn:
                logger.debug("[未读跳转] 未找到侧边栏'聊天/微信'按钮")
                return False

            # 获取按钮的绝对物理坐标中心点
            rect = chat_btn.BoundingRectangle
            x = (rect.left + rect.right) // 2
            y = (rect.top + rect.bottom) // 2

            # 优先使用底层的 physical_double_click 进行物理双击以在屏幕上显示天蓝色/红色/黄色波纹反馈并防封拟人
            try:
                from src.uia.retry.clicks import physical_double_click
                # 确保微信处于前台再执行物理双击，防止被其他窗口捕获
                import win32gui
                if win32gui.GetForegroundWindow() != self.hwnd:
                    from src.uia.retry.window_ops import ensure_wechat_foreground
                    ensure_wechat_foreground(self.hwnd)
                    time.sleep(0.15)
                physical_double_click(x, y, restore_cursor=True, force=force)
            except Exception as e_double:
                logger.warning(f"[未读跳转] 物理双击失败，退回到 UIA 逻辑双击: {e_double}")
                try:
                    chat_btn.DoubleClick(simulateMove=False)
                except Exception:
                    pass
            
            logger.info(f"[未读跳转] 已成功双击侧边栏消息按钮 (坐标: {x}, {y}) 触发原生跳转到下一个未读会话")
            return True
        except Exception as e:
            logger.error(f"[未读跳转] 双击侧边栏'微信'按钮发生异常: {e}")
            return False
