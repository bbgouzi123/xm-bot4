import time
import random
import logging
import uiautomation as uia
import pyperclip
from src.uia.elements import WxName
from src.uia.retry import try_click, random_delay

logger = logging.getLogger("WeChatDriver")

def invite_friend_to_group(driver, group_name: str, friend_name: str) -> bool:
    """邀请好友加入指定群聊，支持新版基于 Qt/mmui 的控件链及坐标偏移点击"""
    if not driver.is_connected():
        return False

    from src.uia.input_guard import uia_lock

    with uia_lock(f"正在拉好友【{friend_name}】入群【{group_name}】"):
        try:
            logger.info(f"[邀群] 开始准备邀请 {friend_name} 加入群聊 {group_name}")
            
            # 1. 切换到目标群聊会话
            uia_lock.update_status(f"正在切换到群聊会话：{group_name}...")
            switched = driver.ChatWith(group_name)
            if not switched:
                logger.error(f"[邀群] 无法切换到群聊会话 {group_name}")
                return False

            # 优先使用句柄获取主窗口以支持多开
            uia_lock.update_status("正在定位微信主窗口...")
            main_win = None
            if getattr(driver, 'hwnd', None):
                main_win = uia.ControlFromHandle(driver.hwnd)
                if not main_win.Exists(1):
                    main_win = None

            if not main_win:
                main_win = uia.WindowControl(searchDepth=1, ClassName="WeChatMainWndForPC")

            if not main_win.Exists(2):
                logger.error("[邀群] 找不到微信主窗口 WeChatMainWndForPC")
                return False

            # 2. 定位聊天细节面板和“聊天信息”按钮
            chat_container = main_win.GroupControl(ClassName="mmui::ChatDetailView")
            chat_info_btn = None
            if chat_container.Exists(0.5):
                chat_info_btn = chat_container.ButtonControl(Name="聊天信息")
            if not chat_info_btn or not chat_info_btn.Exists(0.2):
                chat_info_btn = main_win.ButtonControl(Name="聊天信息")
            if not chat_info_btn or not chat_info_btn.Exists(0.2):
                search_root = chat_container if chat_container.Exists(0.3) else main_win
                for btn in search_root.ButtonControlList():
                    if "聊天信息" in (btn.Name or "") or "聊天信息" in getattr(btn, 'HelpText', '') or "聊天信息" in getattr(btn, 'ToolTip', ''):
                        chat_info_btn = btn
                        break
            if not chat_info_btn or not chat_info_btn.Exists(0.2):
                search_root = chat_container if chat_container.Exists(0.3) else main_win
                candidates = [btn for btn in search_root.ButtonControlList() if btn.ClassName == "mmui::XImage"]
                if candidates:
                    candidates.sort(key=lambda b: b.BoundingRectangle.left, reverse=True)
                    chat_info_btn = candidates[0]

            if not chat_info_btn or not chat_info_btn.Exists(1.0):
                logger.error("[邀群] 未找到 '聊天信息' 按钮")
                return False

            # 点击打开群信息设置面板
            uia_lock.update_status("正在打开群详情侧边栏...")
            try_click(chat_info_btn, max_retries=2, delay=0.2)
            random_delay(0.8, 1.2)

            # 3. 寻找群成员列表控件
            uia_lock.update_status("正在查找群成员列表中的【+】号添加按钮...")
            chat_member_list = main_win.ListControl(Name="聊天成员", ClassName="QFReuseGridWidget")
            if not chat_member_list.Exists(0.5):
                chat_member_list = main_win.ListControl(AutomationId="chat_member_list", ClassName="QFReuseGridWidget")

            # 4. 寻找并点击「+」号/「添加」按钮
            clicked_add = False
            if chat_member_list.Exists(0.5):
                # 4.1 优先查找直接子项中是否有名字为“添加”的项
                try:
                    for item in chat_member_list.GetChildren():
                        if item.Name == "添加" or (item.Name and "添加" in item.Name):
                            try_click(item, max_retries=2, delay=0.2)
                            clicked_add = True
                            logger.info("[邀群] 成功通过遍历列表子项找到并点击了 '+' 添加按钮")
                            random_delay(0.8, 1.2)
                            break
                except Exception as child_err:
                    logger.debug(f"[邀群] 遍历子项查找 '+' 失败: {child_err}")

                # 4.2 尝试在列表内进行递归查找名称为“添加”的控件
                if not clicked_add:
                    try:
                        from src.uia.tag_sync.utils import bfs_find
                        add_btn = bfs_find(chat_member_list, Name="添加", max_depth=6)
                        if add_btn:
                            try_click(add_btn, max_retries=2, delay=0.2)
                            clicked_add = True
                            logger.info("[邀群] 成功通过 BFS 在列表内找到并点击了 '+' 添加按钮")
                            random_delay(0.8, 1.2)
                    except Exception as bfs_err:
                        logger.debug(f"[邀群] BFS 查找 '+' 失败: {bfs_err}")

                # 4.3 坐标偏移计算兜底
                if not clicked_add:
                    try:
                        items = chat_member_list.GetChildren()
                        item_count = len(items)
                        if item_count > 0:
                            last_item = items[-1]
                            last_rect = last_item.BoundingRectangle
                            item_width = last_rect.width()
                            item_height = last_rect.height()
                            if item_count == 15 or item_count % 4 != 0:
                                target_x = last_rect.left + item_width * 1.5
                                target_y = last_rect.top + item_height / 2
                            else:
                                first_item_of_last_row = items[-4]
                                first_rect = first_item_of_last_row.BoundingRectangle
                                target_x = first_rect.left + first_rect.width() / 2
                                target_y = first_rect.top + first_rect.height() / 2 + item_height
                            
                            list_rect = chat_member_list.BoundingRectangle
                            dx = int(target_x - list_rect.left)
                            dy = int(target_y - list_rect.top)
                            
                            chat_member_list.Click(dx, dy, simulateMove=True)
                            clicked_add = True
                            logger.info("[邀群] 通过列表最后成员的坐标偏移成功点击了 '+' 添加按钮")
                            random_delay(0.8, 1.2)
                    except Exception as offset_err:
                        logger.warning(f"[邀群] 通过坐标偏移计算点击 '+' 按钮失败: {offset_err}")

            # 4.4 如果依然没点击成功，使用传统的全局搜索寻找“添加”按钮
            if not clicked_add:
                add_btn = driver._walk_find('Button', name="添加")
                if not add_btn:
                    add_btn = driver._walk_find('Button', name="添加成员")
                if not add_btn:
                    for btn in main_win.ButtonControlList():
                        if btn.Name in ("添加", "添加成员", "add"):
                            add_btn = btn
                            break
                if add_btn:
                    try_click(add_btn, max_retries=2, delay=0.2)
                    clicked_add = True
                    random_delay(0.8, 1.2)

            if not clicked_add:
                logger.error("[邀群] 无法点击到 '+' 邀请按钮，退出流程")
                uia.SendKeys('{Esc}')
                return False

            # 5. 定位添加群成员/选择联系人弹窗
            uia_lock.update_status("正在等待联系人选择窗口弹出...")
            picker_win = uia.WindowControl(searchDepth=1, Name="微信添加群成员", ClassName="mmui::SessionPickerWindow")
            if not picker_win.Exists(0.5):
                picker_win = uia.WindowControl(searchDepth=1, Name="选择联系人")

            if not picker_win.Exists(2.0):
                logger.error("[邀群] 未能检测到加群选择联系人窗口弹出")
                uia.SendKeys('{Esc}')
                return False

            # 6. 在选择窗口进行搜索和勾选
            with picker_win:
                search_box = picker_win.EditControl(Name="搜索", ClassName="mmui::XValidatorTextEdit")
                if not search_box.Exists(0.5):
                    search_box = picker_win.EditControl(Name="搜索")
                if not search_box.Exists(0.5):
                    search_box = picker_win.EditControl()

                if not search_box.Exists(0.5):
                    logger.error("[邀群] 找不到搜索输入框")
                    uia.SendKeys('{Esc}')
                    return False

                uia_lock.update_status(f"正在检索好友：{friend_name}...")
                try_click(search_box, max_retries=2, delay=0.2)
                pyperclip.copy(friend_name)
                uia.SendKeys('{Ctrl}a{Delete}')
                random_delay(0.2, 0.3)
                uia.SendKeys('{Ctrl}v')
                random_delay(0.6, 1.0)
                uia.SendKeys('{Enter}')
                random_delay(0.5, 0.8)

                # 在搜索结果列表中勾选对应好友
                search_result_list = picker_win.ListControl(Name="请勾选需要添加的联系人", AutomationId="sp_search_result_list", ClassName="mmui::XTableView")
                checked_success = False
                if search_result_list.Exists(0.5):
                    for item in search_result_list.GetChildren():
                        if item.Name == friend_name:
                            try_click(item, max_retries=2, delay=0.1)
                            checked_success = True
                            break

                # 备用勾选逻辑
                if not checked_success:
                    target_item = picker_win.CheckBoxControl(Name=friend_name)
                    if not target_item.Exists(0.5):
                        for item in picker_win.CheckBoxControlList():
                            if friend_name in item.Name:
                                target_item = item
                                break
                    if target_item and target_item.Exists(0.5):
                        try_click(target_item, max_retries=2, delay=0.1)
                        checked_success = True

                if not checked_success:
                    logger.warning(f"[邀群] 未能精准勾选好友 {friend_name}，直接发送 Enter 确认尝试")
                    uia.SendKeys('{Enter}')
                    random_delay(0.5, 0.8)
                else:
                    random_delay(0.5, 0.8)

                # 点击确定/添加按钮
                ok_btn = picker_win.ButtonControl(Name="添加", ClassName="mmui::XOutlineButton")
                if not ok_btn.Exists(0.5):
                    ok_btn = picker_win.ButtonControl(Name="确定")
                if not ok_btn.Exists(0.5):
                    ok_btn = picker_win.ButtonControl(Name=WxName.CONFIRM)

                if not ok_btn.Exists(0.5):
                    logger.error("[邀群] 找不到提交确定按钮，退出")
                    uia.SendKeys('{Esc}')
                    return False

                uia_lock.update_status("正在提交入群邀请...")
                try_click(ok_btn, max_retries=2, delay=0.2)
                random_delay(1.0, 1.5)

                # 7. 确认是否触发风控或二次群邀请弹窗
                warning_dialog = uia.WindowControl(searchDepth=3, Name="Weixin", ClassName="mmui::XDialog")
                if warning_dialog.Exists(0.8):
                    logger.warning("[邀群] 触发风控或邀请限制提示，加群取消")
                    warning_dialog.SendKeys('{Esc}')
                    random_delay(0.3, 0.5)
                    if picker_win.Exists(0.1):
                        picker_win.SendKeys('{Esc}')
                    uia.SendKeys('{Esc}')
                    return False

                # 老版微信二次确认弹窗检测
                confirm_win = uia.WindowControl(Name="发送群邀请")
                if confirm_win.Exists(0.5):
                    confirm_ok = confirm_win.ButtonControl(Name="确定")
                    if confirm_ok.Exists(0.5):
                        try_click(confirm_ok, max_retries=2, delay=0.2)
                        random_delay(1.0, 1.5)

                # 8. 关闭群信息侧边栏
                uia_lock.update_status("正在恢复聊天会话焦点...")
                if chat_info_btn and chat_info_btn.Exists(0.5):
                    try_click(chat_info_btn, max_retries=2, delay=0.2)
                else:
                    uia.SendKeys('{Esc}')
                
                logger.info(f"[邀群] 顺利完成邀请 {friend_name} 加入群聊 {group_name}")
                return True
        except Exception as e:
            logger.error(f"[邀群] 邀请好友入群出现异常: {e}", exc_info=True)
            return False
