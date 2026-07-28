import time
import random
import logging
from typing import Optional, Dict, Any

import win32gui
import uiautomation as uia
import pyperclip

from .add_friend_helpers import AddFriendHelper
from .elements import WxClass, WxName
from .retry import try_click, exists_with_timeout, random_delay

logger = logging.getLogger(__name__)


class AddFriendEngine(AddFriendHelper):
    """加好友 UIA 自动化引擎（对标 V2 PyWeixinDriver.add_new_friend）"""

    def __init__(self, driver):
        self.driver = driver

    def add_new_friend(
        self,
        wxid: str,
        remark: Optional[str] = None,
        tags: Optional[str] = None,
        verify_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        """加好友主入口"""
        if not self.driver.is_connected():
            return self._fail("微信客户端未连接", "wx_disconnected", wxid)

        com_inited = False
        try:
            import comtypes
            try:
                comtypes.CoInitialize()
                com_inited = True
            except Exception:
                pass

            from src.uia.input_guard import uia_lock
            with uia_lock(f"正在添加好友【{wxid}】"):
                return self._do_add_new_friend(wxid, remark, tags, verify_message)
        except Exception as e:
            from src.uia.input_guard import UIAInterruptError
            if isinstance(e, UIAInterruptError) or "Interrupt" in type(e).__name__:
                raise e
            logger.error(f"add_new_friend error: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return self._fail(f"自动化崩溃: {str(e)}", "uia_crash", wxid)
        finally:
            if com_inited:
                try:
                    import comtypes
                    comtypes.CoUninitialize()
                except Exception:
                    pass

    def _do_add_new_friend(self, wxid, remark, tags, verify_message):
        logger.info("[add_friend] 正在激活微信窗口...")
        self.driver.SwitchToThisWindow()
        random_delay(0.5, 0.8)

        logger.info("[add_friend] 正在尝试打开【添加朋友】窗口...")
        if not self._open_add_friend_window():
            logger.warning("[add_friend] 打开【添加朋友】窗口失败")
            return self._fail("无法打开【添加朋友】窗口", "open_window_failed", wxid)

        logger.info("[add_friend] 正在定位【添加朋友】窗口句柄...")
        hwnd = self._find_add_friend_hwnd()
        logger.info(f"[add_friend] 查找到的窗口句柄为: {hwnd}")
        if not hwnd:
            logger.warning("[add_friend] 找不到标题为【添加朋友】的窗口")
            return self._fail("无法找到【添加朋友】窗口句柄", "open_window_failed", wxid)

        add_win = uia.ControlFromHandle(hwnd)
        logger.info(f"[add_friend] 成功绑定【添加朋友】窗口控件，准备搜索账号: {wxid}")
        if not self._search_wxid(add_win, wxid):
            logger.warning(f"[add_friend] 在【添加朋友】窗口搜索账号 {wxid} 失败")
            self._close_add_friend_dialogs(add_win)
            return self._fail(f"搜索微信号【{wxid}】失败", "search_failed", wxid)

        logger.info("[add_friend] 账号搜索成功，等待详细资料窗口加载...")
        random_delay(0.6, 1.0)
        return self._do_add_friend(add_win, wxid, remark, tags, verify_message)

    def _do_add_friend(self, add_win, wxid, remark, tags, verify_message) -> Dict[str, Any]:
        logger.info("[add_friend] 开始执行 _do_add_friend 控件解析...")
        main_win = uia.ControlFromHandle(self.driver.hwnd)

        profile_win = None
        container = main_win
        send_msg_btn = None
        add_to_contacts = None

        is_friend = False
        btn_found = False

        # 循环等待详细资料窗口/内嵌资料区渲染
        for idx in range(8):
            logger.info(f"[add_friend] 等待详细资料渲染中，轮询第 {idx + 1} 次...")
            profile_win = uia.WindowControl(searchDepth=1, ClassName="ContactProfileWnd")
            if not profile_win.Exists(0.05):
                profile_win = uia.WindowControl(searchDepth=1, ClassName="mmui::ProfileUniquePop")
            
            if profile_win.Exists(0.05):
                container = profile_win
                logger.info("[add_friend] 发现独立的详细资料窗口 ContactProfileWnd/ProfileUniquePop")
            elif add_win and add_win.Exists(0.05):
                container = add_win
                logger.info("[add_friend] 详细资料处于【添加朋友】内置窗口区域")
            else:
                container = main_win
                logger.info("[add_friend] 降级使用主窗口 container")
            
            logger.info("[add_friend] 正在查找【发消息】按钮...")
            send_msg_btn = container.ButtonControl(Name="发消息")
            if not send_msg_btn.Exists(0.2):
                send_msg_btn = container.Control(Name="发消息")
            if send_msg_btn.Exists(0.2) and send_msg_btn.ControlTypeName == "TextControl":
                parent = send_msg_btn.GetParentControl()
                if parent and parent.ControlTypeName in ("ButtonControl", "Control"):
                    send_msg_btn = parent
                
            logger.info("[add_friend] 正在查找【添加到通讯录】按钮...")
            add_to_contacts = container.ButtonControl(Name="添加到通讯录")
            if not add_to_contacts.Exists(0.2):
                add_to_contacts = container.Control(Name="添加到通讯录")
            if add_to_contacts.Exists(0.2) and add_to_contacts.ControlTypeName == "TextControl":
                parent = add_to_contacts.GetParentControl()
                if parent and parent.ControlTypeName in ("ButtonControl", "Control"):
                    add_to_contacts = parent

            if send_msg_btn.Exists(0.1):
                logger.info("[add_friend] 查找到【发消息】按钮，说明已经是好友")
                is_friend = True
                btn_found = True
                break
            if add_to_contacts.Exists(0.1):
                logger.info("[add_friend] 查找到【添加到通讯录】按钮，说明还不是好友")
                is_friend = False
                btn_found = True
                break

            # 兼容 4.x/MMUI: 检查是否弹出类似“无法找到该用户”的错误提示框
            logger.info("[add_friend] 检查是否有报错弹窗 (mmui::XDialog)...")
            err_dialog = main_win.WindowControl(searchDepth=3, ClassName="mmui::XDialog")
            if not err_dialog.Exists(0.05):
                err_dialog = uia.WindowControl(searchDepth=1, ClassName="mmui::XDialog")
            if err_dialog.Exists(0.05):
                try:
                    from src.utils.safe_uia import safe_walk_control, safe_control_type, safe_get_name
                    err_text = ""
                    for child, _ in safe_walk_control(err_dialog, max_depth=5):
                        if safe_control_type(child) == "TextControl" and safe_get_name(child):
                            err_text += safe_get_name(child)
                    if any(x in err_text for x in ["无法找到", "找不到", "不存在", "错误"]):
                        logger.warning(f"[add_friend] 搜到错误弹窗: {err_text}")
                        uia.SendKeys("{Escape}")
                        self._close_add_friend_dialogs(add_win)
                        return self._fail("无法找到该用户", "user_not_found", wxid)
                except Exception as ex:
                    logger.debug(f"读取错误弹窗失败: {ex}")

            time.sleep(0.2)

        if not btn_found:
            # 尝试做一次最后的判断
            nickname = self._get_avatar_name(container)
            self._close_add_friend_dialogs(add_win)
            if profile_win and profile_win.Exists(0.1):
                uia.SendKeys("{Escape}")
            return self._fail("未找到【添加到通讯录】或【发消息】按钮", "add_button_not_found", wxid, nickname)

        nickname = self._get_avatar_name(container)

        if is_friend:
            self._close_add_friend_dialogs(add_win)
            if profile_win and profile_win.Exists(0.1):
                uia.SendKeys("{Escape}")
            return {"success": True, "status": "already_friend", "nickname": nickname, "wxid": wxid, "message": "已经是好友"}

        try_click(add_to_contacts, max_retries=2, delay=0.1)
        random_delay(1.0, 1.5)

        apply_hwnd = None
        for _ in range(5):
            for t in ["申请添加朋友", "添加朋友请求", "添加到通讯录"]:
                hwnd = win32gui.FindWindow(None, t)
                if hwnd and win32gui.IsWindowVisible(hwnd):
                    apply_hwnd = hwnd
                    break
            if apply_hwnd:
                break
            time.sleep(0.3)

        if not apply_hwnd:
            self._close_add_friend_dialogs(add_win)
            if profile_win and profile_win.Exists(0.1):
                uia.SendKeys("{Escape}")
            return self._fail("未弹出申请添加朋友窗口", "apply_window_not_found", wxid, nickname)

        apply_win = uia.ControlFromHandle(apply_hwnd)

        if remark:
            try:
                remark_edit = apply_win.EditControl(Name="修改备注")
                if remark_edit.Exists(0.5):
                    try_click(remark_edit, max_retries=2, delay=0.1)
                    remark_edit.SendKeys("{Ctrl}a{Delete}")
                    pyperclip.copy(remark)
                    remark_edit.SendKeys("{Ctrl}v")
            except Exception as e:
                logger.error(f"填写备注失败: {e}")

        if verify_message:
            try:
                verify_edit = apply_win.EditControl(Name="发送添加朋友申请")
                if verify_edit.Exists(0.5):
                    try_click(verify_edit, max_retries=2, delay=0.1)
                    verify_edit.SendKeys("{Ctrl}a{Delete}")
                    pyperclip.copy(verify_message)
                    verify_edit.SendKeys("{Ctrl}v")
            except Exception as e:
                logger.error(f"填写验证消息失败: {e}")

        if tags:
            try:
                from src.uia.tag_sync.utils import fill_tags_via_search_and_select
                tag_list = tags.split(",") if "," in tags else tags.split("，")
                tag_list = [t.strip() for t in tag_list if t.strip()]
                # 统一复用聊天场景稳定版打标签底层方法，existing_tags 传空列表（申请窗口中不存在已打标签）
                fill_tags_via_search_and_select(apply_win, tag_list, existing_tags=[])
            except Exception as e:
                logger.error(f"打标签失败: {e}")

        ok_btn = apply_win.ButtonControl(Name="确定")
        if ok_btn.Exists(0.5):
            # 💡 防误触：点击确定前，若标签下拉仍开着，点击【标签】按钮 toggle 收起
            # 选择标签按钮（而非申请输入框）的原因：标签按钮是 toggle 控件，语义精确，
            # 点击同一按钮开关下拉；申请输入框依赖焦点副作用，可能被下拉遮挡或滚出可视区
            try:
                from src.uia.tag_sync.utils import wait_for_label_popover
                popover_check = wait_for_label_popover(0.3)
                if popover_check and popover_check.Exists(0.1):
                    tag_toggle_btn = None
                    for ctrl_name in ["修改标签", "标签"]:
                        _btn = apply_win.ButtonControl(Name=ctrl_name)
                        if _btn.Exists(0.2):
                            tag_toggle_btn = _btn
                            break
                    if not tag_toggle_btn:
                        for ctrl, _ in uia.WalkControl(apply_win, maxDepth=6):
                            if ctrl.ControlTypeName == "ButtonControl" and "标签" in (ctrl.Name or ""):
                                tag_toggle_btn = ctrl
                                break
                    if tag_toggle_btn and tag_toggle_btn.Exists(0.1):
                        tag_toggle_btn.Click(simulateMove=False)
                        random_delay(0.2, 0.35)
                        logger.info("[add_friend] 已点击【标签】按钮 toggle 收起下拉，防止误触确定按钮")
            except Exception as dismiss_e:
                logger.debug(f"[add_friend] 收起标签下拉失败（不影响主流程）: {dismiss_e}")
            try_click(ok_btn, max_retries=2, delay=0.1)
            random_delay(1.0, 1.5)

        success = not self._any_window_exists(["申请添加朋友", "添加朋友请求", "添加到通讯录"])
        self._close_add_friend_dialogs(add_win, apply_win)
        if profile_win and profile_win.Exists(0.1):
            uia.SendKeys("{Escape}")

        if success:
            return {"success": True, "status": "requested", "nickname": nickname, "wxid": wxid, "message": "已发送好友申请"}
        return self._fail("发送好友申请失败", "send_failed", wxid, nickname)

