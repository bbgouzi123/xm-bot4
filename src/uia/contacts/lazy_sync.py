import time
import logging
import uiautomation as uia
from src.uia.retry import try_click, random_delay, physical_click
from src.uia.contacts.sync_contacts.detail_extractor import safe_exists, safe_close_pop_win

logger = logging.getLogger("WeChatDriver.LazySync")

class ContactLazySyncMixin:
    """联系人懒加载详情同步 Mixin"""

    def try_lazy_sync_current_chat(self, session_name: str) -> bool:
        """
        在当前活跃单聊会话中，静默/按需同步提取当前聊天好友的详情并补全通讯录。
        """
        if not self.driver or not self.driver.is_connected():
            return False

        # 0. 过滤系统账号/公众号
        if session_name in ["文件传输助手", "微信团队", "服务通知", "订阅号消息", "公众号"]:
            return False

        # 1. 验证是否处于聊天视图
        chat_container = self.driver.root.GroupControl(ClassName='mmui::ChatDetailView')
        if not chat_container.Exists(0.5):
            return False

        # 验证输入框是否属于该 session
        edit_msg = self.driver._get_edit_control(session_name)
        if not edit_msg or not edit_msg.Exists(0.2):
            return False

        # 2. 验证是否为普通单聊会话 (排除群聊)
        chat_info_btn = chat_container.ButtonControl(Name='聊天信息')
        if not chat_info_btn.Exists(0.2):
            return False

        from src.uia.modules.session_type_helper import get_chat_window_type_impl
        if get_chat_window_type_impl(self.driver, session_name) != "chat":
            return False

        # 3. 检查缓存中是否已有该好友真实的 wxid 且有头像
        from src.utils.contacts_cache import contacts_cache
        user_info = self.driver.get_current_user()
        account_id = user_info.get("wxid") or user_info.get("nickname") or "default_user"

        friends = contacts_cache.get_friends(account_id) or []
        contact_in_db = None
        for f in friends:
            if (f.get("name") or "").strip() == session_name.strip() or (f.get("remark") or "").strip() == session_name.strip():
                contact_in_db = f
                break

        # 判断是否具有真实的 wxid 以及头像 (如果已经有非哈希的真实 wxid 并且有 avatar_url，则无需再次抓取)
        if contact_in_db and contact_in_db.get("wxid") and not contact_in_db.get("wxid").isalnum():
            if contact_in_db.get("avatar_url"):
                return True

        logger.info(f"[LazySync] 正在对当前活跃聊天 '{session_name}' 进行无感详情补全...")

        # 4. 点击“聊天信息”按钮展开侧边栏
        try:
            from src.uia.input_guard import uia_lock
            with uia_lock("正在提取聊天好友详情", hwnd=self.driver.hwnd):
                # 确保微信在前台
                import ctypes
                if ctypes.windll.user32.GetForegroundWindow() != self.driver.hwnd:
                    from src.uia.retry.window_ops import ensure_wechat_foreground
                    ensure_wechat_foreground(self.driver.hwnd)
                    time.sleep(0.1)

                # 点击“聊天信息”侧边栏按钮 (方案 B 用，方案 A 不需要点击)
                pop_win = uia.WindowControl(ClassName="mmui::ProfileUniquePop")
                if pop_win.Exists(0.1):
                    safe_close_pop_win(pop_win)
                    time.sleep(0.15)

                clicked = False

                # === [方案 A (优先)] 从当前聊天记录气泡直接点击好友头像 ===
                try:
                    list_ctrl = self.driver.root.ListControl(ClassName="RecyclerListView")
                    if not list_ctrl.Exists(0.1):
                        list_ctrl = self.driver.root.ListControl(ClassName="mmui::RecyclerListView")

                    if list_ctrl.Exists(0.5):
                        children = list_ctrl.GetChildren()
                        for child in reversed(children):
                            cls_name = getattr(child, "ClassName", "") or ""
                            if any(msg_cls in cls_name for msg_cls in ["ChatTextItemView", "ChatBubbleItemView", "ChatBubbleReferItemView", "ChatVoiceItemView", "ChatFileItemView", "ChatImageItemView", "ChatVideoItemView"]):
                                from src.uia.message import _detect_is_self
                                driver_nickname = getattr(self.driver, "_nickname", "") or "我"
                                if not _detect_is_self(child, driver_nickname):
                                    rect = child.BoundingRectangle
                                    if rect and rect.left > 0 and rect.top > 0:
                                        # 🌟 4.x 扁平气泡物理直接定位法：由于微信 4.x 聊天记录 ListItem 没有暴露子控件，
                                        # 我们直接基于 ListItem 的 BoundingRectangle 与系统 DPI 缩放比例，
                                        # 物理计算出左侧好友头像的物理中心坐标，省去查找子控件的耗时，稳定且高效。
                                        import ctypes
                                        def get_lazy_dpi() -> float:
                                            try:
                                                hdc = ctypes.windll.user32.GetDC(0)
                                                log_x = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)
                                                ctypes.windll.user32.ReleaseDC(0, hdc)
                                                return log_x / 96.0
                                            except Exception:
                                                return 1.0
                                        scale = get_lazy_dpi()
                                        
                                        x = int(rect.left + 35 * scale)
                                        y = int(rect.top + 30 * scale)
                                        
                                        logger.info(f"[LazySync] 优先通过聊天气泡物理绝对坐标点击好友头像, 坐标: ({x}, {y}) [DPI={scale}]")
                                        physical_click(x, y)
                                        if safe_exists(pop_win, 2.0):
                                            clicked = True
                                            break
                except Exception as avatar_ex:
                    logger.debug(f"[LazySync] 尝试聊天区气泡头像点击异常: {avatar_ex}")

                # === [方案 B (兜底)] 原侧边栏头像同步方案 ===
                if not clicked:
                    logger.info("[LazySync] 聊天气泡头像点击未成功唤起，回退执行侧边栏头像提取方案...")
                    try_click(chat_info_btn)
                    time.sleep(0.4)

                    # 5. 在 root 或聊天区域的右侧寻找侧边栏内的第一个头像控件 (兼容 ContactHeadView 和 ContactHead)
                    head_view = None
                    win_rect = self.driver.root.BoundingRectangle
                    min_left = win_rect.left + int((win_rect.right - win_rect.left) * 0.5) if win_rect else 400

                    # 仅在右半侧检索头像，确保是侧边栏里的好友头像，而非聊天记录里的
                    for ctrl, _ in uia.WalkControl(self.driver.root, maxDepth=12):
                        cls_name = ctrl.ClassName or ""
                        if "ContactHead" in cls_name or "ContactHeadView" in cls_name:
                            r = ctrl.BoundingRectangle
                            if r and r.left > min_left:
                                head_view = ctrl
                                break

                    # 兜底：如果依然没找到，采用大小和坐标位置在右半侧定位头像控件
                    if not head_view:
                        for ctrl, _ in uia.WalkControl(self.driver.root, maxDepth=12):
                            r = ctrl.BoundingRectangle
                            if r and r.left > min_left:
                                w, h = r.width(), r.height()
                                # 头像通常是 30px-90px 左右，且长宽比接近 1
                                if 25 <= w <= 95 and 25 <= h <= 95 and 0.8 <= (w / h) <= 1.25:
                                    ctype = ctrl.ControlTypeName or ""
                                    cls_name = ctrl.ClassName or ""
                                    if "CheckBox" not in ctype and "CheckBox" not in cls_name and "Edit" not in ctype:
                                        head_view = ctrl
                                        break

                    if not head_view:
                        logger.warning("[LazySync] 未在聊天信息侧边栏中定位到好友头像控件")
                        try_click(chat_info_btn)
                        return False

                    try_click(head_view)
                    if not safe_exists(pop_win, 1.0):
                        r = head_view.BoundingRectangle
                        if r:
                            physical_click((r.left + r.right) // 2, (r.top + r.bottom) // 2)

                if safe_exists(pop_win, 2.0):
                    # 7. 调用已有的提取方法解析详情
                    details = self._extract_details_from_profile_pop(pop_win, session_name)

                    # 8. 安全收尾：关闭个人资料气泡，并收起侧边栏
                    safe_close_pop_win(pop_win)
                    time.sleep(0.15)

                    if not clicked:
                        try_click(chat_info_btn)
                        time.sleep(0.15)

                    if details and details.get("wxid"):
                        logger.info(f"[LazySync] 成功补全 '{session_name}' 的详情数据: wxid={details['wxid']}")

                        # 组装最终联系人数据
                        contact = {
                            "name": session_name,
                            "display_name": details.get("nickname") or session_name,
                            "category": "联系人",
                            "syncTime": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        }
                        for k, v in details.items():
                            if v:
                                contact[k] = v

                        # 保存并触发 contacts_cache 内存及 SQLite 级数据同步
                        self._save_contacts([contact], is_incremental=True)
                        return True
                else:
                    logger.warning("[LazySync] 气泡卡片 Window mmui::ProfileUniquePop 未弹出")
                    try_click(chat_info_btn)
        except Exception as e:
            logger.error(f"[LazySync] 补全执行异常: {e}")
            try:
                try_click(chat_info_btn)
            except Exception:
                pass

        return False
