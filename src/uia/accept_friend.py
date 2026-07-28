import logging
from typing import List, Dict, Any
import uiautomation as uia

from .elements import WxName, WxClass
from .retry import try_click, exists_with_timeout, random_delay
from .accept_friend_helpers import update_progress, update_success, update_failed, find_control, setup_remark_and_tags

logger = logging.getLogger(__name__)


class AcceptFriendEngine:
    def __init__(self, driver):
        self.driver = driver

    def accept_all(self, remark_template: str = "", tags: List[str] = None, keyword_tag_rules: List[Dict[str, str]] = None, permission_type: str = "all", hide_my_moments: bool = False, hide_his_moments: bool = False) -> List[Dict[str, Any]]:
        """批量接受通讯录中的新朋友请求"""
        if not self.driver.is_connected() or not getattr(self.driver, "hwnd", None):
            return []

        # 建立当前线程专用的微信窗口，防止 cross-thread COM 报错
        wechat_win = uia.ControlFromHandle(self.driver.hwnd)
        if not wechat_win.Exists(1.0):
            return []

        from src.uia.input_guard import uia_lock
        with uia_lock("正在批量接受朋友申请"):
            accepted_friends = []
            try:
                update_progress(10, "正在激活微信窗口以准备自动同意好友申请...")
                self.driver.SwitchToThisWindow()
                random_delay(0.2, 0.4)

                # 1. 寻找“通讯录”和“微信”聊天按钮
                update_progress(25, "正在切换到通讯录页面...")
                tabbar = wechat_win.ToolBarControl(ClassName="mmui::MainTabBar")
                contact_btn = None
                chat_btn = None
                if tabbar.Exists(1.0):
                    for child in tabbar.GetChildren():
                        if child.ControlTypeName == "ButtonControl":
                            c_name = child.Name or ""
                            if WxName.CONTACTS_NAV in c_name or "ͨѶ¼" in c_name:
                                contact_btn = child
                            elif WxName.CHAT_NAV in c_name or "微信" in c_name:
                                chat_btn = child
                
                if not contact_btn:
                    update_failed("未找到‘通讯录’按钮，自动同意失败")
                    return []
                
                # 切换到通讯录页面，并增加严格的状态检查与重试机制，确保物理点击跳转成功
                success_switch = False
                for attempt in range(3):
                    try_click(contact_btn, max_retries=1, delay=0.2)
                    random_delay(0.6, 1.0)
                    
                    # 验证方式 1: 查找通讯录下的列表控件 mmui::StickyHeaderRecyclerListView
                    contacts_list = wechat_win.ListControl(ClassName="mmui::StickyHeaderRecyclerListView")
                    if contacts_list.Exists(0.5):
                        success_switch = True
                        break
                        
                    # 验证方式 2: 查找 "新的朋友"ListItemControl
                    for name_try in ["新的朋友", "新联系人", "朋友申请", "好友请求", "Ͳ"]:
                        btn = wechat_win.Control(Name=name_try, searchDepth=10)
                        if btn.Exists(0.2):
                            success_switch = True
                            break
                    if success_switch:
                        break
                        
                if not success_switch:
                    update_failed("未能成功跳转至通讯录页面，自动同意失败")
                    return []


                # 2. 寻找"新的朋友"
                update_progress(40, "正在寻找‘新的朋友’入口...")
                new_friend_btn = None

                # 优先方案：在通讯录的 RecyclerListView 列表内快速定位，避免全局深层 Walk 导致性能问题或超时
                contacts_list = wechat_win.ListControl(ClassName="mmui::StickyHeaderRecyclerListView")
                if contacts_list.Exists(0.5):
                    for name_try in ["新的朋友", "新联系人", "朋友申请", "好友请求", "Ͳ"]:
                        # 1. 尝试直接按名称和类型匹配子控件
                        btn = contacts_list.Control(Name=name_try, ControlType=uia.ControlType.ListItemControl)
                        if btn.Exists(0.2):
                            new_friend_btn = btn
                            break
                        # 2. 尝试 Walk 子列表（深度限定为 5，效率极高）
                        for ctrl, depth in uia.WalkControl(contacts_list, maxDepth=5):
                            if ctrl.ControlTypeName in ["ListItemControl", "ButtonControl"]:
                                c_name = ctrl.Name or ""
                                if name_try in c_name or "新的朋友" in c_name:
                                    new_friend_btn = ctrl
                                    break
                        if new_friend_btn:
                            break

                # 退化方案：如果以上未定位到，全窗口深层查找
                if not new_friend_btn:
                    for name_try in ["新的朋友", "新联系人", "朋友申请", "好友请求", "Ͳ"]:
                        for ctrl, depth in uia.WalkControl(wechat_win, maxDepth=12):
                            if ctrl.ControlTypeName in ["ListItemControl", "ButtonControl"]:
                                c_name = ctrl.Name or ""
                                if name_try in c_name or "新的朋友" in c_name:
                                    new_friend_btn = ctrl
                                    break
                        if new_friend_btn:
                            break

                if not new_friend_btn or not exists_with_timeout(new_friend_btn, 1.0):
                    update_failed("未找到‘新的朋友’入口，自动同意失败")
                    if chat_btn:
                        try_click(chat_btn, max_retries=2, delay=0.2)
                    return []
                
                update_progress(50, "正在进入‘新的朋友’列表页...")
                try_click(new_friend_btn, max_retries=2, delay=0.2)
                random_delay(0.6, 1.0)

                # 3. 寻找待验证好友申请并一一处理（跳过黑名单申请人）
                skipped_nicknames = set()
                skipped_item_names = set()
                
                while True:
                    accept_btn = None
                    target_nickname = ""
                    import time
                    
                    # 3.1 探测是否可以直接在当前页面上找到 “接受” 或 “前往验证” 按钮（旧版微信直接在右侧铺开列表）
                    direct_buttons = []
                    try:
                        for ctrl, depth in uia.WalkControl(wechat_win, maxDepth=14):
                            if ctrl.ControlTypeName == "ButtonControl" and ctrl.Name in ["接受", "前往验证"]:
                                nickname = ""
                                parent = ctrl.GetParentControl()
                                if parent:
                                    for c in parent.GetChildren():
                                        if c.ControlTypeName == "TextControl" and c.Name and c.Name not in ["接受", "前往验证"]:
                                            nickname = c.Name
                                            break
                                if nickname not in skipped_nicknames:
                                    direct_buttons.append((ctrl, nickname))
                    except Exception as direct_ex:
                        logger.debug(f"检查直铺按钮异常: {direct_ex}")

                    if direct_buttons:
                        # 使用直铺列表中的按钮
                        accept_btn, target_nickname = direct_buttons[0]
                    else:
                        # 3.2 新版微信流程：需在左侧列表先点击“等待验证”好友，再从右侧面板提取按钮
                        wait_verify_items = []
                        try:
                            for ctrl, depth in uia.WalkControl(contacts_list, maxDepth=4):
                                if ctrl.ControlTypeName == "ListItemControl":
                                    c_name = ctrl.Name or ""
                                    if "等待验证" in c_name and c_name not in skipped_item_names:
                                        wait_verify_items.append(ctrl)
                        except Exception:
                            pass

                        if not wait_verify_items:
                            # 兜底全窗口搜索 ListItemControl
                            try:
                                for ctrl, depth in uia.WalkControl(wechat_win, maxDepth=14):
                                    if ctrl.ControlTypeName == "ListItemControl" and "等待验证" in (ctrl.Name or ""):
                                        c_name = ctrl.Name or ""
                                        if c_name not in skipped_item_names:
                                            wait_verify_items.append(ctrl)
                            except Exception:
                                pass

                        if not wait_verify_items:
                            # 两种布局都无待验证好友，处理完毕
                            break

                        target_item = wait_verify_items[0]
                        item_name = target_item.Name or ""
                        
                        # 解析大概昵称（“等待验证”前面的文字）
                        if "等待验证" in item_name:
                            target_nickname = item_name.split("等待验证")[0].strip()

                        update_progress(60, f"正在载入待验证好友 '{target_nickname or '未知'}' 的详情...")
                        try_click(target_item, max_retries=2, delay=0.3)
                        random_delay(0.8, 1.2)

                        # 在右侧详情区域寻找接受/验证按钮
                        for btn_name in ["前往验证", "接受", "通过验证"]:
                            btn = wechat_win.ButtonControl(Name=btn_name)
                            if btn.Exists(0.5):
                                accept_btn = btn
                                break
                            for ctrl, depth in uia.WalkControl(wechat_win, maxDepth=14):
                                if ctrl.ControlTypeName == "ButtonControl" and ctrl.Name == btn_name:
                                    accept_btn = ctrl
                                    break
                            if accept_btn:
                                break

                        if not accept_btn or not exists_with_timeout(accept_btn, 0.5):
                            # 如果实在未找到按钮，记录此项以免死循环
                            skipped_item_names.add(item_name)
                            continue

                    # 校验是否需要跳过（黑名单拦截）
                    is_black = False
                    if target_nickname:
                        if target_nickname in skipped_nicknames:
                            is_black = True
                        else:
                            from src.utils.config_cache import config_cache
                            blacklist = config_cache.get("blacklist", [])
                            if blacklist:
                                for b_item in blacklist:
                                    b_val = b_item if isinstance(b_item, str) else b_item.get("wxid") or b_item.get("nickname")
                                    if b_val and b_val == target_nickname:
                                        is_black = True
                                        break
                    
                    if is_black:
                        if target_nickname and target_nickname not in skipped_nicknames:
                            logger.warning(f"[黑名单过滤] 发现黑名单好友申请: {target_nickname}，跳过不予自动同意")
                            skipped_nicknames.add(target_nickname)
                        if 'item_name' in locals():
                            skipped_item_names.add(item_name)
                        continue

                    nickname = target_nickname
                    update_progress(65, f"发现待验证好友申请 '{nickname}'，正在点击验证...", nickname)
                    try_click(accept_btn, max_retries=2, delay=0.3)
                    random_delay(0.8, 1.2)
                    
                    # 4. 点了“接受”或“前往验证”后，获取朋友验证窗口
                    apply_win = None
                    for _ in range(5):
                        for win_name in ["通过朋友验证", "新的朋友", "验证申请"]:
                            w = uia.WindowControl(Name=win_name)
                            if w and exists_with_timeout(w, 0.2):
                                apply_win = w
                                break
                        if not apply_win:
                            w = uia.WindowControl(ClassName="WeUIDialog")
                            if w and exists_with_timeout(w, 0.2):
                                apply_win = w
                        if apply_win:
                            break
                        time.sleep(0.3)
                    
                    #                     # (注: 下面引用 apply_win 部分保持一致)
                    found_ok = False
                    if apply_win and exists_with_timeout(apply_win, 0.5):
                        if not nickname:
                            try:
                                for ctrl, d in uia.WalkControl(apply_win, maxDepth=6):
                                    if ctrl.ControlTypeName == "EditControl" and ctrl.Name == "修改备注":
                                        p = ctrl.GetParentControl()
                                        if p and p.Name:
                                            nickname = p.Name
                                            break
                            except Exception:
                                pass
                        setup_remark_and_tags(apply_win, nickname, remark_template, tags, keyword_tag_rules, permission_type, hide_my_moments, hide_his_moments)

                        update_progress(95, f"正在保存并提交 '{nickname}' 的通过验证...", nickname)
                        for name in ["确定", "完成"]:
                             btn = apply_win.ButtonControl(Name=name)
                             if btn and exists_with_timeout(btn, 0.5):
                                  try_click(btn, max_retries=2, delay=0.2)
                                  found_ok = True
                                  random_delay(1.5, 2.5)
                                  break
                    
                    if not found_ok:
                        for name in ["确定", "完成"]:
                             btn = find_control(wechat_win, name, "ButtonControl")
                             if btn and exists_with_timeout(btn, 0.5):
                                  try_click(btn, max_retries=2, delay=0.2)
                                  random_delay(1.5, 2.5)
                                  break

                    accepted_friends.append({"nickname": nickname or "未知联系人"})
                    update_progress(98, f"已成功通过好友: '{nickname}'", nickname)

                update_progress(99, "好友处理完成，正在返回聊天主页...")
                if chat_btn:
                    try_click(chat_btn, max_retries=2, delay=0.2)
                if accepted_friends:
                    update_success(f"自动通过完成，共成功接受 {len(accepted_friends)} 个好友申请", accepted_friends[0]["nickname"])
                
            except Exception as e:
                logger.error(f"批量接受朋友申请失败: {e}")
                update_failed(f"自动同意好友申请发生异常: {e}")
                if chat_btn:
                    try:
                        try_click(chat_btn, max_retries=2, delay=0.2)
                    except:
                        pass

        return accepted_friends



