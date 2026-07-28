import time
import random
import logging
from typing import Optional, Dict, Any

import uiautomation as uia
import pyperclip

from .retry import try_click, exists_with_timeout, random_delay
from .elements import WxName
from .uia_utils import UiaUtils

logger = logging.getLogger(__name__)

class AddFriendHelper(UiaUtils):
    """加好友及批量加群好友底层的通用 UIA 交互辅助 Mixin\""""



    def _find_control_multi(self, root, name: str, max_depth: int = 15):
        """多策略查找控件"""
        for depth in range(1, min(max_depth + 1, 8)):
            try:
                c = root.ButtonControl(Name=name, searchDepth=depth)
                if c and exists_with_timeout(c, 0.4):
                    return c
            except Exception:
                pass

        try:
            for ctrl, _ in uia.WalkControl(root, maxDepth=max_depth):
                try:
                    n = ctrl.Name
                    if n and name in n:
                        return ctrl
                except Exception:
                    continue
        except Exception:
            pass

        return None



    def _open_add_friend_window(self) -> bool:
        """
        打开微信"添加朋友"窗口。
        新版微信 (4.x/MMUI) 推荐策略：
          1. 点击左侧导航栏 "微信" (消息/聊天) Tab
          2. 点击搜索栏旁边的 "+" 按钮 (ClassName="mmui::XImage")
          3. 在弹出的快捷操作菜单中点击 "添加朋友"
        若此新版策略失败，自动回退到 "通讯录" 查找策略。
        """
        main_win = uia.ControlFromHandle(self.driver.hwnd)

        # 获取微信窗口位置与侧边导航栏右边界
        import win32gui as _add_win32gui
        try:
            _wl, _wt, _wr, _wb = _add_win32gui.GetWindowRect(self.driver.hwnd)
        except Exception:
            _wl, _wt, _wr, _wb = 0, 0, 1000, 800

        # 💡 获取侧边导航栏右边界——作为唯一空间基准，彻底兼容任意DPI/分辨率/窗口位置
        # 加号按钮必须在导航栏右侧 & 窗口顶部 100px 内；收藏/通讯录都在导航栏左侧，永远被排除
        _tabbar_right = _wl + 80  # 默认保守估算：导航栏约 80px 宽
        try:
            from src.utils.safe_uia import safe_bounding_rect
            _tabbar_ctrl = main_win.ToolBarControl(ClassName="mmui::MainTabBar")
            if _tabbar_ctrl.Exists(0.3):
                _tb_rect = safe_bounding_rect(_tabbar_ctrl)
                if _tb_rect:
                    _tabbar_right = _tb_rect.right
                    logger.debug(f"[add_friend] 侧边导航栏右边界 tabbar_right={_tabbar_right}")
        except Exception:
            pass

        # 💡 关键防护：加号按钮只能位于微信窗口的左半部分（会话栏顶部），横坐标限制在窗口前 45% 的范围内。
        # 这样可以直接、彻底排除窗口右上角的控制按钮（最小化、最大化、关闭，它们都在窗口右侧边缘 80%~100% 区域）
        _right_limit = _wl + int((_wr - _wl) * 0.45)
        logger.debug(f"[add_friend] 限制搜索范围: tabbar_right={_tabbar_right}, right_limit={_right_limit}")

        # ────── 优先策略：微信聊天界面顶部的 "+" 加号菜单 ──────
        try:
            # A. 基于【侧边栏右边界】和【左半区限制】定位加号按钮，与 DPI/分辨率/窗口位置/缩放完全无关
            # 规则：① 在侧边栏右侧，且在左半区内 (tabbar_right < rect.left < right_limit)
            #       ② 在顶部标题栏 100px 以内 (rel_top < 100)
            #       ③ ClassName = mmui::XButton
            plus_btn = None
            try:
                from src.utils.safe_uia import safe_walk_control, safe_bounding_rect
                for ctrl, _ in safe_walk_control(main_win, max_depth=10):
                    if ctrl.ClassName == "mmui::XButton":
                        rect = safe_bounding_rect(ctrl)
                        if rect is None:
                            continue
                        rel_top = rect.top - _wt
                        if _tabbar_right < rect.left < _right_limit and 0 <= rel_top <= 100:
                            plus_btn = ctrl
                            logger.info(f"[add_friend] 侧边栏及左半区限制策略定位加号按钮成功: left={rect.left}, rel_top={rel_top}")
                            break
            except Exception as e_coord:
                logger.debug(f"[add_friend] 侧边栏定位加号按钮异常: {e_coord}")

            # B. 精确名称匹配（名称约束强，不会误触收藏/通讯录/最小化）
            if not plus_btn or not plus_btn.Exists(0.05):
                plus_btn = main_win.ButtonControl(Name="快捷操作")
            if not plus_btn or not plus_btn.Exists(0.05):
                plus_btn = main_win.ButtonControl(Name="+")
            if not plus_btn or not plus_btn.Exists(0.05):
                plus_btn = main_win.Control(Name="快捷操作", ClassName="mmui::XButton")
            if not plus_btn or not plus_btn.Exists(0.05):
                plus_btn = main_win.Control(Name="+")
            # ⚠️ ClassName="mmui::XImage" 兜底，同样用侧边栏和左半区限制过滤掉导航图标与右上角控制按钮
            if not plus_btn or not plus_btn.Exists(0.05):
                from src.utils.safe_uia import safe_walk_control, safe_bounding_rect
                for ctrl, _ in safe_walk_control(main_win, max_depth=8):
                    try:
                        if ctrl.ClassName == "mmui::XImage" and ctrl.ControlTypeName == "ButtonControl":
                            rect = safe_bounding_rect(ctrl)
                            if rect is None:
                                continue
                            rel_top = rect.top - _wt
                            if _tabbar_right < rect.left < _right_limit and 0 <= rel_top <= 100:
                                plus_btn = ctrl
                                logger.info(f"[add_friend] XImage 兜底定位加号按钮: left={rect.left}, rel_top={rel_top}")
                                break
                    except Exception:
                        continue

            # B. 若未直接定位到，再尝试去点 "微信" 导航 Tab 刷新状态
            if not plus_btn or not plus_btn.Exists(0.05):
                logger.info("[add_friend] 未直接定位到【+】加号按钮，尝试通过【微信】导航 Tab 进行切换...")
                chat_btn = None
                tabbar = main_win.ToolBarControl(ClassName="mmui::MainTabBar")
                if tabbar.Exists(0.5):
                    children = tabbar.GetChildren()
                    for child in children:
                        if child.ControlTypeName == "ButtonControl" and "微信" in (child.Name or ""):
                            chat_btn = child
                            break
                    if not chat_btn and children:
                        for child in children:
                            if child.ControlTypeName == "ButtonControl":
                                chat_btn = child
                                break
                
                # 回退全树深搜“微信”导航按钮
                if not chat_btn:
                    from src.utils.safe_uia import safe_walk_control, safe_control_type, safe_get_name
                    for ctrl, _ in safe_walk_control(main_win, max_depth=10):
                        if safe_control_type(ctrl) == "ButtonControl" and safe_get_name(ctrl) == "微信":
                            chat_btn = ctrl
                            break
                            
                if chat_btn:
                    try_click(chat_btn, max_retries=2, delay=0.1)
                    random_delay(0.3, 0.5)
                    
                    # 重新寻找加号按钮（与主策略保持一致，使用相对坐标约束与左半区约束）
                    plus_btn = main_win.ButtonControl(Name="快捷操作")
                    if not plus_btn.Exists(0.1):
                        plus_btn = main_win.ButtonControl(Name="+")
                    if not plus_btn.Exists(0.1):
                        plus_btn = main_win.Control(Name="快捷操作", ClassName="mmui::XButton")
                    if not plus_btn.Exists(0.1):
                        plus_btn = main_win.Control(Name="+")
                    # 带左半区位置约束的 XImage 兜底，防止误触收藏/通讯录/窗口右上角控制按钮
                    if not plus_btn.Exists(0.1):
                        from src.utils.safe_uia import safe_walk_control, safe_bounding_rect
                        for ctrl, _ in safe_walk_control(main_win, max_depth=8):
                            try:
                                if ctrl.ClassName == "mmui::XImage" and ctrl.ControlTypeName == "ButtonControl":
                                    rect = safe_bounding_rect(ctrl)
                                    if rect is None:
                                        continue
                                    rel_top = rect.top - _wt
                                    if _tabbar_right < rect.left < _right_limit and 0 <= rel_top <= 100:
                                        plus_btn = ctrl
                                        logger.info(f"[add_friend] Tab切换后XImage兜底定位加号按钮: left={rect.left}, rel_top={rel_top}")
                                        break
                            except Exception:
                                continue
                

                
            if plus_btn and plus_btn.Exists(0.5):
                logger.info("[add_friend] 定位到【+】加号按钮，正在执行点击与菜单弹出验证...")
                
                menu_item = None

                # 辅助函数：通过底层 C++ 极速定位 "添加朋友" 控件，严格限制深度以避免全树深搜卡死
                def _quick_find_menu_item() -> Optional[uia.Control]:
                    # 1. 优先在微信主窗口下受限深度查找
                    try:
                        for depth in [3, 6]:
                            candidate = main_win.Control(Name="添加朋友", searchDepth=depth)
                            if candidate.Exists(0.05):
                                return candidate
                    except Exception:
                        pass
                    # 2. 精准定位微信快捷菜单独立窗口，避免使用 GetRootControl() 导致子线程 COM 套间死锁
                    try:
                        from src.uia.retry.window_ops import find_wechat_menu_popover_hwnd
                        menu_hwnd = find_wechat_menu_popover_hwnd(self.driver.hwnd)
                        if menu_hwnd:
                            menu_win = uia.ControlFromHandle(menu_hwnd)
                            for depth in [2, 3, 4]:
                                candidate = menu_win.Control(Name="添加朋友", searchDepth=depth)
                                if candidate.Exists(0.05):
                                    return candidate
                    except Exception:
                        pass
                    return None

                # 1. 检查菜单是否其实已经在界面上了（如果之前已经弹出过）
                menu_item = _quick_find_menu_item()
                if menu_item:
                    logger.info("[add_friend] 检测到快捷菜单已处于弹出状态，直接定位到【添加朋友】")
                else:
                    # 2. 如果菜单没有弹出，则点击 plus_btn
                    logger.info("[add_friend] 点击【+】加号按钮弹出快捷菜单...")
                    try:
                        self._hover_control(plus_btn)
                    except Exception:
                        pass
                    try_click(plus_btn, max_retries=1, delay=0.05)
                    random_delay(0.5, 0.7)  # 稍微等待浮层渲染
                    
                    # 3. 再次查找菜单项
                    menu_item = _quick_find_menu_item()

                if menu_item:
                    logger.info("[add_friend] 成功定位到【添加朋友】菜单项，正在执行点击与窗口弹出验证...")
                    try:
                        self._hover_control(menu_item)
                    except Exception:
                        pass
                    for menu_retry in range(3):
                        try_click(menu_item, max_retries=1, delay=0.05)
                        random_delay(0.5, 0.8)
                        if self._win32_window_exists("添加朋友"):
                            logger.info("[add_friend] 成功打开【添加朋友】窗口")
                            return True
                        logger.warning(f"[add_friend] 点击菜单项后窗口未出现，重试第 {menu_retry + 2} 次点击...")
                else:
                    # 4. 键盘兜底模拟操作：Down 2次 + Enter
                    logger.info("[add_friend] 未直接定位到【添加朋友】菜单控件，采用键盘向下两次加回车兜底模拟...")
                    try:
                        uia.SendKeys("{DOWN}{DOWN}{ENTER}")
                        random_delay(0.6, 0.9)
                    except Exception as e_kbd:
                        logger.error(f"[add_friend] 键盘模拟按键失败: {e_kbd}")
                
                if self._win32_window_exists("添加朋友"):
                    logger.info("[add_friend] 成功打开【添加朋友】窗口")
                    return True
        except Exception as e:
            logger.warning(f"[add_friend] 尝试通过【+】快捷菜单打开失败: {e}")
        return False

    def _search_wxid(self, add_win, wxid: str) -> bool:
        logger.info("[add_friend] 开始执行 _search_wxid...")
        try:
            # 🌟 窗口微移 1 像素刷新 UIA 缓存树
            logger.info("[add_friend] 执行窗口微移 nudge_window...")
            UiaUtils._nudge_window(add_win)
        except Exception as e:
            logger.debug(f"[add_friend] 微移窗口失败: {e}")

        logger.info("[add_friend] 寻找输入框...")
        search_edit = add_win.EditControl(Name="微信号/手机号", searchDepth=4)
        if not search_edit.Exists(0.3):
            search_edit = add_win.EditControl(Name="搜索", searchDepth=4)
        if not search_edit.Exists(0.3):
            search_edit = add_win.EditControl(AutomationId="search_edit", searchDepth=4)
        if not search_edit.Exists(0.3):
            logger.warning("[add_friend] 无法定位到任何微信号搜索输入框")
            return False

        logger.info(f"[add_friend] 找到输入框，清空并粘贴微信号: {wxid}")
        try_click(search_edit, max_retries=2, delay=0.1)
        search_edit.SendKeys("{Ctrl}a{Delete}")
        pyperclip.copy(wxid)
        random_delay(0.1, 0.2)
        search_edit.SendKeys("{Ctrl}v")
        random_delay(0.3, 0.5)

        # 尝试按 Enter 键搜索
        logger.info("[add_friend] 发送 {Enter} 键执行搜索...")
        search_edit.SendKeys("{Enter}")
        
        # 兼容：如果界面有【搜索】按钮，再进行点击以确保搜索执行（已按用户要求注释）
        # search_btn = add_win.ButtonControl(Name="搜索", searchDepth=4)
        # if search_btn.Exists(0.3):
        #     logger.info("[add_friend] 点击【搜索】按钮...")
        #     try_click(search_btn, max_retries=2, delay=0.1)

        logger.info("[add_friend] 等待搜索结果加载...")
        random_delay(1.2, 1.8)
        logger.info("[add_friend] 搜索完成")
        return True

    def _get_avatar_name(self, main_win) -> str:
        try:
            name_text = main_win.TextControl(searchDepth=15, ClassName="mmui::XTextView")
            if name_text.Exists(0.5):
                return name_text.Name
        except Exception:
            pass
        return ""

    def _fail(self, message: str, status: str, wxid: str, nickname: str = "") -> Dict[str, Any]:
        return {"success": False, "message": message, "status": status, "nickname": nickname, "wxid": wxid}
