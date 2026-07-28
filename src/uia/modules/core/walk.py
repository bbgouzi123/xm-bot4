"""控件树 WalkControl / BFS 搜索（供导航、消息等 Mixin 复用）。"""
from typing import Any
import uiautomation as uia



class WeChatCoreWalkMixin:
    def _walk_find(self, control_type: str = None, name: str = "",
                   class_name: str = "", max_depth: int = 15, name_contains: str = ""):
        """用 safe_walk_control 深度搜索控件，防止 COM 崩溃（对齐 xm-bot4）
        
        支持精确匹配 name 或包含匹配 name_contains。
        """
        from src.utils.safe_uia import safe_walk_control, safe_control_type, safe_get_name, safe_class_name
        try:
            for ctrl, _ in safe_walk_control(self.root, max_depth=max_depth):
                try:
                    ctrl_type = safe_control_type(ctrl)
                    if control_type and ctrl_type != control_type:
                        continue
                    ctrl_name = safe_get_name(ctrl)
                    if name and ctrl_name != name:
                        continue
                    if name_contains and name_contains not in ctrl_name:
                        continue
                    ctrl_cls = safe_class_name(ctrl)
                    if class_name and ctrl_cls != class_name:
                        continue
                    return ctrl
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def _walk_find_edit(self, name: str = "", class_name: str = "",
                        max_depth: int = 16):
        """用 safe_walk_control 查找 EditControl，支持 Name 包含匹配"""
        from src.utils.safe_uia import safe_walk_control, safe_control_type, safe_get_name, safe_class_name
        try:
            for ctrl, _ in safe_walk_control(self.root, max_depth=max_depth):
                try:
                    if safe_control_type(ctrl) != 'EditControl':
                        continue
                    ctrl_name = safe_get_name(ctrl)
                    ctrl_cls = safe_class_name(ctrl)
                    if name and ctrl_name != name:
                        continue
                    if class_name and ctrl_cls != class_name:
                        continue
                    return ctrl
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def _find_child(self, parent, name: str = "", control_type: str = "",
                    class_name: str = "", depth: int = 8):
        """在控件树中查找指定子控件（BFS）"""
        try:
            queue = [(parent, 0)]
            while queue:
                ctrl, d = queue.pop(0)
                if d > depth:
                    continue

                match = True
                if name and (getattr(ctrl, "Name", "") or "") != name:
                    match = False
                if control_type and (
                        getattr(ctrl, "ControlTypeName", "") or
                        "") != control_type + "Control":
                    match = False
                if class_name and (
                        getattr(ctrl, "ClassName", "") or "") != class_name:
                    match = False

                if match and (name or control_type or class_name):
                    return ctrl

                try:
                    for child in ctrl.GetChildren():
                        queue.append((child, d + 1))
                except Exception:
                    pass

        except Exception:
            pass
        return None

    def _walk_controls(self, parent, depth: int = 5):
        """遍历控件树（生成器）"""
        queue = [(parent, 0)]
        while queue:
            ctrl, d = queue.pop(0)
            yield ctrl
            if d < depth:
                try:
                    for child in ctrl.GetChildren():
                        queue.append((child, d + 1))
                except Exception:
                    pass

    def _silent_inspect_tab(self, target_tab_name: str, inspect_fn, rollback_to_chat: bool = True) -> Any:
        """全局通用：安全静默地在后台切换到指定 Tab 页面执行检测
        
        - target_tab_name: 目标导航栏按钮名称 (例如 '微信' 或 '通讯录')
        - inspect_fn: 传入执行具体检测的回调函数, 签名为 inspect_fn(wechat_win: uia.WindowControl) -> Any
        - rollback_to_chat: 检测结束后是否默默切换回'微信'聊天主页面
        """
        import time
        import logging
        import uiautomation as uia
        import win32gui
        from src.uia.elements import WxName

        logger = logging.getLogger(__name__)
        if not getattr(self, "hwnd", None):
            return 0

        # 智能防干扰：如果微信窗口当前处于最前台，且用户正在活跃操作，才暂不自动切换以防干扰打字；若用户空闲（围观挂机）则放行
        if win32gui.GetForegroundWindow() == self.hwnd:
            try:
                from src.utils.user_activity import is_user_active
                if is_user_active():
                    return 0
            except Exception:
                return 0

        try:
            # 建立当前线程专用的微信窗口，防止 cross-thread COM 报错
            wechat_win = uia.ControlFromHandle(self.hwnd)
            if not wechat_win.Exists(0.5):
                return 0

            # 定位 TabBar
            tabbar = wechat_win.ToolBarControl(ClassName="mmui::MainTabBar")
            if not tabbar.Exists(0.5):
                return 0

            # 找到“通讯录”和“微信”按钮
            contact_btn = None
            chat_btn = None
            for child in tabbar.GetChildren():
                if child.ControlTypeName == "ButtonControl":
                    c_name = child.Name or ""
                    if WxName.CONTACTS_NAV in c_name or "ͨѶ¼" in c_name:
                        contact_btn = child
                    elif WxName.CHAT_NAV in c_name or "微信" in c_name:
                        chat_btn = child

            # 定位目标按钮
            target_btn = None
            if target_tab_name == "通讯录":
                target_btn = contact_btn
            elif target_tab_name == "微信":
                target_btn = chat_btn
            else:
                for child in tabbar.GetChildren():
                    if child.ControlTypeName == "ButtonControl" and target_tab_name in (child.Name or ""):
                        target_btn = child
                        break

            if not target_btn:
                return 0

            def silent_click(element) -> bool:
                try:
                    legacy = element.GetLegacyIAccessiblePattern()
                    if legacy:
                        legacy.DoDefaultAction()
                        return True
                    invoke = element.GetInvokePattern()
                    if invoke:
                        invoke.Invoke()
                        return True
                except Exception:
                    pass
                try:
                    element.Click(simulateMove=False)
                    return True
                except Exception:
                    return False

            # 1. 悄悄切换到目标页面
            if not silent_click(target_btn):
                return 0
            time.sleep(0.5)

            # 2. 执行传入的检测逻辑
            result = inspect_fn(wechat_win)

            # 3. 如果需要，默默切回“微信”主会话界面
            if rollback_to_chat and chat_btn and target_tab_name != "微信":
                silent_click(chat_btn)

            return result

        except Exception as e:
            logger.debug(f"[静默巡检] 检测 Tab '{target_tab_name}' 发生异常: {e}")
            return 0

    def _check_sidebar_unread(self) -> bool:
        """检查侧边栏是否有未读聊天会话"""
        # 首先检查左侧导航栏的“微信”按钮是否显示有未读角标，这可以在不切换Tab的非侵入状态下快速判定！
        if self.get_tabbar_chat_unread_count() > 0:
            return True

        import uiautomation as uia

        def inspect_chat_unread(wechat_win) -> bool:
            # 寻找会话列表 ListControl
            session_list = None
            for cls in ["StickyHeaderRecyclerListView", "SessionRecyclerListView"]:
                lst = wechat_win.ListControl(ClassName=cls)
                if lst.Exists(0.2):
                    session_list = lst
                    break
            if not session_list:
                session_list = wechat_win.ListControl(Name="会话")
                
            if not session_list or not session_list.Exists(0.2):
                return False
                
            # 遍历会话项，检查是否有未读数
            for item in session_list.GetChildren():
                try:
                    raw_name = item.Name or ""
                    if not raw_name.strip():
                        continue
                    # 1. 尝试直接从 raw_name 解析未读
                    from src.uia.session import parse_session_name
                    parsed = parse_session_name(raw_name)
                    if parsed and parsed.get("unread", 0) > 0:
                        return True
                    # 2. 扫描子控件中的数字角标（如 TextControl 包含纯数字）
                    for child in item.GetChildren():
                        if child.ControlTypeName == "TextControl" and child.Name:
                            c_name = child.Name.strip()
                            if c_name.isdigit() and 1 <= len(c_name) <= 3:
                                return True
                except Exception:
                    continue
            return False

        return bool(self._silent_inspect_tab(target_tab_name="微信", inspect_fn=inspect_chat_unread, rollback_to_chat=False))
