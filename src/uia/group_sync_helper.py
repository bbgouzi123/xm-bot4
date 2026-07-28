import time
import random
import logging
import uiautomation as uia
from src.uia.retry import try_click, random_delay

logger = logging.getLogger("WeChatDriver")

def sync_group_members_via_uia(driver, group_name: str) -> dict:
    """通过 UIA 自动化同步微信群成员数据，支持 3.9+ / 4.x 多开及进度推送"""
    if not driver.is_connected():
        return {"success": False, "message": "微信未连接"}

    from src.uia.input_guard import uia_lock

    with uia_lock(f"正在同步微信群【{group_name}】的成员数据"):
        try:
            uia_lock.update_status(f"正在切换到群聊会话：{group_name}...")
            driver.SwitchToThisWindow()
            time.sleep(0.5)
            if not driver.ChatWith(group_name):
                return {"success": False, "message": f"无法切换到群聊【{group_name}】"}

            main_win = uia.ControlFromHandle(driver.hwnd) if getattr(driver, 'hwnd', None) else uia.WindowControl(searchDepth=1, ClassName="WeChatMainWndForPC")
            chat_container = main_win.GroupControl(ClassName="mmui::ChatDetailView")
            chat_info_btn = chat_container.ButtonControl(Name="聊天信息") if chat_container.Exists(0.5) else main_win.ButtonControl(Name="聊天信息")

            if not chat_info_btn or not chat_info_btn.Exists(1.0):
                return {"success": False, "message": "未找到聊天信息按钮"}

            # 打开侧边栏
            chat_member_list = main_win.ListControl(Name="聊天成员", ClassName="QFReuseGridWidget")
            if not chat_member_list.Exists(0.5):
                chat_member_list = main_win.ListControl(AutomationId="chat_member_list", ClassName="QFReuseGridWidget")

            if not chat_member_list.Exists(0.5):
                uia_lock.update_status("正在打开群详情侧边栏...")
                try_click(chat_info_btn, max_retries=2, delay=0.2)
                random_delay(0.8, 1.2)
                chat_member_list = main_win.ListControl(Name="聊天成员", ClassName="QFReuseGridWidget")
                if not chat_member_list.Exists(0.5):
                    chat_member_list = main_win.ListControl(AutomationId="chat_member_list", ClassName="QFReuseGridWidget")

            if not chat_member_list.Exists(1.0):
                return {"success": False, "message": "无法打开群信息侧边栏"}

            member_names = set()
            # 查找“查看更多群成员”
            more_btn = main_win.ButtonControl(Name="查看更多群成员")
            if not more_btn.Exists(0.3):
                for btn, _ in uia.WalkControl(main_win, maxDepth=10):
                    if btn.ControlTypeName == "ButtonControl" and (btn.Name or "").strip() in ("查看更多群成员", "查看全部群成员"):
                        more_btn = btn
                        break

            if more_btn and more_btn.Exists(0.3):
                uia_lock.update_status("正在打开完整群成员窗口...")
                try_click(more_btn, max_retries=2, delay=0.2)
                random_delay(1.0, 1.5)
                
                member_win = uia.WindowControl(searchDepth=1, Name="群成员")
                if not member_win.Exists(0.5):
                    member_win = uia.WindowControl(searchDepth=1, Name="微信群成员")
                if not member_win.Exists(0.5):
                    member_win = uia.WindowControl(searchDepth=1, ClassName="mmui::ContactsManagerWindow")

                if member_win.Exists(1.0):
                    list_ctrl = member_win.ListControl()
                    if not list_ctrl.Exists(0.3):
                        list_ctrl = member_win.ListControl(ClassName="QFReuseGridWidget")
                    
                    if list_ctrl.Exists(0.3):
                        scroll_attempts = 0
                        last_count = 0
                        while scroll_attempts < 40:
                            uia_lock.check_interrupt()
                            for item in list_ctrl.GetChildren():
                                name = (item.Name or "").strip()
                                if name and name not in ("添加", "添加成员", "删除", "删除成员", "add", "del") and name not in member_names:
                                    member_names.add(name)
                            
                            if len(member_names) == last_count:
                                scroll_attempts += 1
                            else:
                                scroll_attempts = 0
                                last_count = len(member_names)
                                uia_lock.update_status(f"已提取到 {last_count} 个群成员...")
                            
                            list_ctrl.SendKeys("{PageDown}")
                            time.sleep(0.15)
                    else:
                        for ctrl, _ in uia.WalkControl(member_win, maxDepth=8):
                            if ctrl.ControlTypeName == "TextControl" and ctrl.Name:
                                name = ctrl.Name.strip()
                                if name and name not in ("添加", "添加成员", "删除", "删除成员", "add", "del"):
                                    member_names.add(name)
                    member_win.SendKeys("{Escape}")
                    time.sleep(0.5)
                else:
                    logger.warning("未找到群成员弹出窗口，回退至侧栏抓取")
                    _extract_from_sidebar(chat_member_list, member_names, uia_lock)
            else:
                _extract_from_sidebar(chat_member_list, member_names, uia_lock)

            if chat_info_btn and chat_info_btn.Exists(0.5):
                try_click(chat_info_btn, max_retries=2, delay=0.2)

            if not member_names:
                return {"success": False, "message": "未提取到任何群成员"}

            return {"success": True, "members": list(member_names)}
        except Exception as e:
            logger.error(f"同步群成员异常: {e}", exc_info=True)
            return {"success": False, "message": f"同步异常: {str(e)}"}

def _extract_from_sidebar(list_ctrl, member_names, uia_lock):
    scroll_attempts = 0
    last_count = 0
    while scroll_attempts < 12:
        uia_lock.check_interrupt()
        for item in list_ctrl.GetChildren():
            name = (item.Name or "").strip()
            if name and name not in ("添加", "添加成员", "删除", "删除成员", "add", "del", "查看更多群成员") and name not in member_names:
                member_names.add(name)
        
        if len(member_names) == last_count:
            scroll_attempts += 1
        else:
            scroll_attempts = 0
            last_count = len(member_names)
            uia_lock.update_status(f"已提取到 {last_count} 个群成员...")
            
        list_ctrl.SendKeys("{PageDown}")
        time.sleep(0.15)
