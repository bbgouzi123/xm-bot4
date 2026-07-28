import time
import random
import logging
import uiautomation as uia
import pyperclip
from src.uia.elements import WxName
from src.uia.retry import try_click, random_delay

logger = logging.getLogger("WeChatDriver")

def invite_friends_to_group(driver, group_name: str, friend_names: list) -> dict:
    """批量邀请多位好友加入群聊，严格对齐竞品一次性在同一个弹窗中搜索、勾选并提交的批量邀请机制"""
    if not driver.is_connected():
        return {"success": False, "message": "微信未连接", "success_count": 0, "failed_names": friend_names}

    from src.uia.input_guard import uia_lock

    # 过滤空值
    friend_names = [f.strip() for f in friend_names if f and f.strip()]
    if not friend_names:
        return {"success": False, "message": "待邀请好友列表为空", "success_count": 0, "failed_names": []}

    with uia_lock(f"正在批量邀请好友入群【{group_name}】"):
        try:
            logger.info(f"[批量邀群] 开始准备邀请 {len(friend_names)} 位好友加入群 {group_name}")
            
            # 1. 切换到目标群聊会话
            uia_lock.update_status(f"正在切换到群聊会话：{group_name}...")
            switched = driver.ChatWith(group_name)
            if not switched:
                logger.error(f"[批量邀群] 无法切换到群聊会话 {group_name}")
                return {"success": False, "message": f"无法切换到群聊 {group_name}", "success_count": 0, "failed_names": friend_names}

            # 优先使用句柄获取主窗口
            uia_lock.update_status("正在定位微信主窗口...")
            main_win = None
            if getattr(driver, 'hwnd', None):
                main_win = uia.ControlFromHandle(driver.hwnd)
                if not main_win.Exists(1):
                    main_win = None

            if not main_win:
                main_win = uia.WindowControl(searchDepth=1, ClassName="WeChatMainWndForPC")

            if not main_win.Exists(2):
                logger.error("[批量邀群] 找不到微信主窗口 WeChatMainWndForPC")
                return {"success": False, "message": "找不到微信主窗口", "success_count": 0, "failed_names": friend_names}

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
                logger.error("[批量邀群] 未找到 '聊天信息' 按钮")
                return {"success": False, "message": "未找到 '聊天信息' 按钮", "success_count": 0, "failed_names": friend_names}

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
                            logger.info("[批量邀群] 成功通过遍历列表子项找到并点击了 '+' 添加按钮")
                            random_delay(0.8, 1.2)
                            break
                except Exception as child_err:
                    logger.debug(f"[批量邀群] 遍历子项查找 '+' 失败: {child_err}")

                # 4.2 尝试在列表内进行递归查找名称为“添加”的控件
                if not clicked_add:
                    try:
                        from src.uia.tag_sync.utils import bfs_find
                        add_btn = bfs_find(chat_member_list, Name="添加", max_depth=6)
                        if add_btn:
                            try_click(add_btn, max_retries=2, delay=0.2)
                            clicked_add = True
                            logger.info("[批量邀群] 成功通过 BFS 在列表内找到并点击了 '+' 添加按钮")
                            random_delay(0.8, 1.2)
                    except Exception as bfs_err:
                        logger.debug(f"[批量邀群] BFS 查找 '+' 失败: {bfs_err}")

                # 4.3 坐标偏移计算兜底 (对齐竞品)
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
                            logger.info("[批量邀群] 通过列表最后成员的坐标偏移成功点击了 '+' 添加按钮")
                            random_delay(0.8, 1.2)
                    except Exception as offset_err:
                        logger.warning(f"[批量邀群] 通过坐标偏移计算点击 '+' 按钮失败: {offset_err}")

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
                logger.error("[批量邀群] 无法点击到 '+' 邀请按钮，退出流程")
                uia.SendKeys('{Esc}')
                return {"success": False, "message": "无法打开添加成员窗口", "success_count": 0, "failed_names": friend_names}

            # 5. 定位添加群成员/选择联系人弹窗
            uia_lock.update_status("正在等待联系人选择窗口弹出...")
            picker_win = uia.WindowControl(searchDepth=1, Name="微信添加群成员", ClassName="mmui::SessionPickerWindow")
            if not picker_win.Exists(0.5):
                picker_win = uia.WindowControl(searchDepth=1, Name="选择联系人")

            if not picker_win.Exists(2.0):
                logger.error("[批量邀群] 未能检测到加群选择联系人窗口弹出")
                uia.SendKeys('{Esc}')
                return {"success": False, "message": "未弹出选择联系人窗口", "success_count": 0, "failed_names": friend_names}

            # 6. 在同一个选择窗口中批量搜索和勾选
            success_names = []
            failed_names = []

            with picker_win:
                search_box = picker_win.EditControl(Name="搜索", ClassName="mmui::XValidatorTextEdit")
                if not search_box.Exists(0.5):
                    search_box = picker_win.EditControl(Name="搜索")
                if not search_box.Exists(0.5):
                    search_box = picker_win.EditControl()

                if not search_box.Exists(0.5):
                    logger.error("[批量邀群] 找不到搜索输入框")
                    uia.SendKeys('{Esc}')
                    return {"success": False, "message": "找不到搜索输入框", "success_count": 0, "failed_names": friend_names}

                for friend_name in friend_names:
                    uia_lock.check_interrupt()
                    uia_lock.update_status(f"正在检索勾选好友：{friend_name}...")
                    
                    try_click(search_box, max_retries=2, delay=0.1)
                    pyperclip.copy(friend_name)
                    search_box.SendKeys('{Ctrl}a{Delete}')
                    random_delay(0.1, 0.2)
                    search_box.SendKeys('{Ctrl}v')
                    random_delay(0.3, 0.5)
                    search_box.SendKeys('{Enter}')
                    random_delay(0.4, 0.6)

                    # 在搜索结果列表中勾选对应好友
                    search_result_list = picker_win.ListControl(Name="请勾选需要添加的联系人", AutomationId="sp_search_result_list", ClassName="mmui::XTableView")
                    checked_success = False
                    if search_result_list.Exists(0.4):
                        for item in search_result_list.GetChildren():
                            if item.Name == friend_name:
                                # 点击勾选
                                try_click(item, max_retries=2, delay=0.1)
                                checked_success = True
                                break

                    # 备用勾选逻辑
                    if not checked_success:
                        target_item = picker_win.CheckBoxControl(Name=friend_name)
                        if not target_item.Exists(0.2):
                            for item in picker_win.CheckBoxControlList():
                                if friend_name in item.Name:
                                    target_item = item
                                    break
                        if target_item and target_item.Exists(0.2):
                            try_click(target_item, max_retries=2, delay=0.1)
                            checked_success = True

                    if checked_success:
                        logger.info(f"[批量邀群] 成功勾选好友: {friend_name}")
                        success_names.append(friend_name)
                    else:
                        logger.warning(f"[批量邀群] 勾选好友失败: {friend_name}")
                        failed_names.append(friend_name)

                    # 每次搜索勾选间稍微等待，减少操作过快引发的无响应
                    random_delay(0.2, 0.4)

                # 7. 提交添加
                if not success_names:
                    logger.warning("[批量邀群] 未能成功勾选任何好友，取消拉群")
                    uia.SendKeys('{Esc}')
                    # 恢复群信息侧边栏
                    if chat_info_btn and chat_info_btn.Exists(0.5):
                        try_click(chat_info_btn, max_retries=2, delay=0.2)
                    return {"success": False, "message": "未能成功勾选任何好友，操作取消", "success_count": 0, "failed_names": failed_names}

                # 点击确定/添加按钮
                ok_btn = picker_win.ButtonControl(Name="添加", ClassName="mmui::XOutlineButton")
                if not ok_btn.Exists(0.5):
                    ok_btn = picker_win.ButtonControl(Name="确定")
                if not ok_btn.Exists(0.5):
                    ok_btn = picker_win.ButtonControl(Name=WxName.CONFIRM)

                if not ok_btn.Exists(0.5):
                    logger.error("[批量邀群] 找不到提交确定按钮，退出")
                    uia.SendKeys('{Esc}')
                    return {"success": False, "message": "找不到提交按钮", "success_count": 0, "failed_names": friend_names}

                # 检查按钮是否可用。如果勾选成功，但按钮不可用，说明好友本来就都在群里了
                if not ok_btn.IsEnabled:
                    logger.warning("[批量邀群] 添加按钮为禁用状态，可能好友均已在群聊中，直接退出")
                    uia.SendKeys('{Esc}')
                    if chat_info_btn and chat_info_btn.Exists(0.5):
                        try_click(chat_info_btn, max_retries=2, delay=0.2)
                    return {"success": True, "message": "所选好友已均在群中", "success_count": 0, "failed_names": failed_names}

                uia_lock.update_status("正在提交入群邀请...")
                try_click(ok_btn, max_retries=2, delay=0.2)
                random_delay(1.0, 1.5)

                # 8. 确认是否触发风控或二次群邀请弹窗
                warning_dialog = uia.WindowControl(searchDepth=3, Name="Weixin", ClassName="mmui::XDialog")
                if warning_dialog.Exists(0.8):
                    logger.warning("[批量邀群] 触发风控或邀请限制提示")
                    warning_dialog.SendKeys('{Esc}')
                    random_delay(0.3, 0.5)
                    if picker_win.Exists(0.1):
                        picker_win.SendKeys('{Esc}')
                    uia.SendKeys('{Esc}')
                    return {"success": False, "message": "操作频繁触发风控或无群邀请权限", "success_count": 0, "failed_names": friend_names}

                # 老版微信二次确认弹窗检测
                confirm_win = uia.WindowControl(Name="发送群邀请")
                if confirm_win.Exists(0.5):
                    confirm_ok = confirm_win.ButtonControl(Name="确定")
                    if confirm_ok.Exists(0.5):
                        try_click(confirm_ok, max_retries=2, delay=0.2)
                        random_delay(1.0, 1.5)

                # 9. 关闭群信息侧边栏
                uia_lock.update_status("正在恢复聊天会话焦点...")
                if chat_info_btn and chat_info_btn.Exists(0.5):
                    try_click(chat_info_btn, max_retries=2, delay=0.2)
                else:
                    uia.SendKeys('{Esc}')
                
                logger.info(f"[批量邀群] 顺利完成邀请任务，成功勾选: {success_names}，失败: {failed_names}")
                return {"success": True, "success_count": len(success_names), "failed_names": failed_names}
        except Exception as e:
            logger.error(f"[批量邀群] 批量邀请好友入群出现异常: {e}", exc_info=True)
            return {"success": False, "message": f"操作异常: {str(e)}", "success_count": 0, "failed_names": friend_names}
