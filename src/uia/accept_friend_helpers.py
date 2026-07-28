import logging
import time
import uiautomation as uia

logger = logging.getLogger(__name__)

def update_progress(progress: int, message: str, nickname: str = "新朋友"):
    """更新加锁遮罩的状态描述并向前端控制中心广播实时任务进度"""
    try:
        from src.uia.input_guard import uia_lock
        uia_lock.update_status(message)
    except Exception:
        pass
    try:
        from src.utils.websocket_manager import ws_manager
        import asyncio
        payload = ws_manager.broadcast_task_update(
            task_id="auto_accept_friend",
            task_type="自动通过好友",
            status="running",
            progress=progress,
            total=100,
            message=message,
            friend_name=nickname,
            incoming_msg="新好友申请"
        )
        loop = None
        try:
            import app.state as app_state
            if hasattr(app_state, "main_loop") and app_state.main_loop:
                loop = app_state.main_loop
        except Exception:
            pass
        if not loop:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                pass
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(payload, loop)
    except Exception as e:
        logger.debug(f"[好友通过] 广播任务进度失败: {e}")


def update_success(message: str, nickname: str = "新朋友"):
    """广播任务成功状态到前端控制中心"""
    try:
        from src.utils.websocket_manager import ws_manager
        import asyncio
        payload = ws_manager.broadcast_task_update(
            task_id="auto_accept_friend",
            task_type="自动通过好友",
            status="success",
            progress=100,
            total=100,
            message=message,
            friend_name=nickname,
            incoming_msg="新好友申请"
        )
        loop = None
        try:
            import app.state as app_state
            if hasattr(app_state, "main_loop") and app_state.main_loop:
                loop = app_state.main_loop
        except Exception:
            pass
        if not loop:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                pass
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(payload, loop)
            # 发送系统通知到通知中心
            try:
                from src.utils.alert_notifier import alert_notifier
                asyncio.run_coroutine_threadsafe(
                    alert_notifier.send_user_notification(
                        title="🤝 自动同意好友申请成功",
                        body=f"已成功通过来自新朋友 '{nickname}' 的申请，自动添加并应用了对应的备注和标签。",
                        category="chat"
                    ),
                    loop
                )
            except Exception as e_noti:
                logger.debug(f"[好友通过] 发送系统通知异常: {e_noti}")
    except Exception as e:
        logger.debug(f"[好友通过] 广播任务成功失败: {e}")


def update_failed(message: str):
    """广播任务失败状态到前端控制中心"""
    try:
        from src.utils.websocket_manager import ws_manager
        import asyncio
        payload = ws_manager.broadcast_task_update(
            task_id="auto_accept_friend",
            task_type="自动通过好友",
            status="failed",
            progress=100,
            total=100,
            message=message,
            friend_name="新朋友",
            incoming_msg="新好友申请"
        )
        loop = None
        try:
            import app.state as app_state
            if hasattr(app_state, "main_loop") and app_state.main_loop:
                loop = app_state.main_loop
        except Exception:
            pass
        if not loop:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                pass
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(payload, loop)
    except Exception as e:
        logger.debug(f"[好友通过] 广播任务失败异常: {e}")


def find_control(root, name: str, control_type: str = "", contains: bool = False):
    """在 UIA 控件树中查找指定名称的控件。"""
    try:
        for ctrl, depth in uia.WalkControl(root, maxDepth=14):
            try:
                if control_type and ctrl.ControlTypeName != control_type:
                    continue
                ctrl_name = ctrl.Name or ""
                if contains:
                    if name in ctrl_name:
                        return ctrl
                else:
                    if name == ctrl_name:
                        return ctrl
            except Exception:
                continue
    except Exception:
        pass
    return None


def apply_single_tag(win_control, tag_btn, tag_text: str) -> bool:
    """为通过朋友验证窗口，添加单个微信标签"""
    import pyperclip
    from .retry import try_click, exists_with_timeout, random_delay
    try:
        if not tag_btn or not tag_text:
            return False
        
        try_click(tag_btn, max_retries=2, delay=0.3)
        random_delay(0.4, 0.7)
        
        search_edit = None
        for ctrl, d in uia.WalkControl(win_control, maxDepth=6):
            if ctrl.ControlTypeName == "EditControl" and (ctrl.Name == "搜索" or d > 2):
                search_edit = ctrl
                break
        
        if not search_edit or not exists_with_timeout(search_edit, 0.5):
            return False
            
        try_click(search_edit, max_retries=1, delay=0.2)
        search_edit.SetFocus()
        
        search_edit.SendKeys("{Ctrl}a{Delete}")
        pyperclip.copy(tag_text)
        search_edit.SendKeys("{Ctrl}v")
        random_delay(0.5, 0.8)
        
        target_item = None
        for ctrl, depth in uia.WalkControl(win_control, maxDepth=6):
            if ctrl.ControlTypeName in ("ListItemControl", "ButtonControl", "TextControl"):
                name = (ctrl.Name or "").strip()
                if name == tag_text.strip():
                    target_item = ctrl
                    break
                elif name.startswith("创建新标签"):
                    target_item = ctrl
        
        if target_item:
            try_click(target_item, max_retries=2, delay=0.3)
            random_delay(0.3, 0.5)
        else:
            logger.warning(f"[好友通过] 未在列表中找到标签匹配项或创建项: {tag_text}")
            
        try_click(tag_btn, max_retries=2, delay=0.3)
        random_delay(0.4, 0.7)
        
        try:
            search_edit.SendKeys("{Ctrl}a{Delete}")
        except:
            pass
        return True
    except Exception as e:
        logger.error(f"[好友通过] apply_single_tag 异常: {e}")
        return False


def setup_remark_and_tags(apply_win, nickname: str, remark_template: str, tags: list, keyword_tag_rules: list = None, permission_type: str = "all", hide_my_moments: bool = False, hide_his_moments: bool = False) -> None:
    """在通过朋友验证弹窗中自动填写备注、添加标签、设置权限和隐私"""
    from .retry import try_click, random_delay, exists_with_timeout
    
    # 1. 尝试提取申请附言以进行关键字打标
    verify_text = ""
    try:
        # 遍历弹窗找除微信固定文字之外的 TextControl 作为附言
        for ctrl, d in uia.WalkControl(apply_win, maxDepth=8):
            if ctrl.ControlTypeName == "TextControl" and ctrl.Name:
                name = ctrl.Name.strip()
                if name and name not in ["通过朋友验证", "备注", "标签", "搜索或创建标签...", "朋友权限", "聊天、朋友圈、微信运动等", "仅聊天", "朋友圈和状态", "不让他（她）看", "不看他（她）", "不让他 (她) 看", "不看他 (她)", "确定", "取消", "完成"]:
                    # 避免误拿到昵称（昵称往往字数较短且在前面）
                    if len(name) > 1 and name != nickname:
                        verify_text = name
                        break
    except Exception as e:
        logger.debug(f"[好友通过] 提取附言异常: {e}")

    # 合并标签
    final_tags = list(tags) if tags else []
    if verify_text and keyword_tag_rules:
        logger.info(f"[好友通过] 提取到好友申请附言: '{verify_text}'")
        for rule in keyword_tag_rules:
            kw = rule.get("keyword", "")
            tg = rule.get("tag", "")
            if kw and tg and kw in verify_text:
                if tg not in final_tags:
                    final_tags.append(tg)
                    logger.info(f"[好友通过] 附言关键字 '{kw}' 匹配成功，自动追加标签: '{tg}'")

    # 2. 修改备注
    if remark_template and nickname:
        from datetime import datetime
        date_str = datetime.now().strftime("%m%d")
        remark_text = remark_template.replace("{nickname}", nickname).replace("{date}", date_str)
        
        try:
            update_progress(75, f"正在为好友 '{nickname}' 自动修改微信备注...", nickname)
            edit_ctrls = [c for c, d in uia.WalkControl(apply_win, maxDepth=5) if c.ControlTypeName == "EditControl"]
            if edit_ctrls:
                remark_edit = edit_ctrls[0]
                remark_edit.Click()
                random_delay(0.1, 0.2)
                remark_edit.SendKeys("{Ctrl}a")
                remark_edit.SendKeys("{BackSpace}")
                random_delay(0.1, 0.2)
                remark_edit.SendKeys(remark_text)
                logger.info(f"[好友通过] 已自动将好友 {nickname} 备注设置为: {remark_text}")
        except Exception as re_ex:
            logger.warning(f"[好友通过] 设置备注发生异常: {re_ex}")

    # 3. 打标签（统一复用聊天场景稳定版底层 fill_tags_via_search_and_select，避免多处维护）
    if final_tags:
        try:
            update_progress(85, f"正在为好友 '{nickname}' 自动应用标签: {final_tags}...", nickname)
            from src.uia.tag_sync.utils import fill_tags_via_search_and_select
            fill_tags_via_search_and_select(apply_win, final_tags)
        except Exception as tag_ex:
            logger.warning(f"[好友通过] 设置微信标签发生异常: {tag_ex}")

    # 4. 设置朋友权限 (仅聊天 / 聊天、朋友圈、微信运动等)
    if permission_type == "chat_only":
        try:
            update_progress(90, f"正在为好友 '{nickname}' 设置权限: 仅聊天...", nickname)
            # 点击“仅聊天”选项
            for ctrl, d in uia.WalkControl(apply_win, maxDepth=6):
                if ctrl.Name == "仅聊天":
                    try_click(ctrl, max_retries=2, delay=0.2)
                    logger.info(f"[好友通过] 已自动将权限设置为: 仅聊天")
                    break
        except Exception as perm_ex:
            logger.warning(f"[好友通过] 设置朋友权限仅聊天异常: {perm_ex}")

    # 5. 设置隐私屏蔽 (不让他看我 / 不看他)
    if hide_my_moments or hide_his_moments:
        try:
            update_progress(92, f"正在为好友 '{nickname}' 设置朋友圈屏蔽...", nickname)
            for ctrl, d in uia.WalkControl(apply_win, maxDepth=6):
                if hide_my_moments and ctrl.Name in ["不让他看我", "不让他 (她) 看"]:
                    parent = ctrl.GetParentControl()
                    target_switch = None
                    if parent:
                        for child in parent.GetChildren():
                            if child.ControlTypeName in ["CheckBoxControl", "ButtonControl", "SwitchControl"] and child != ctrl:
                                target_switch = child
                                break
                    if not target_switch:
                        target_switch = ctrl
                    try_click(target_switch, max_retries=1, delay=0.2)
                    logger.info(f"[好友通过] 已触发屏蔽朋友圈: 不让他看我")

                if hide_his_moments and ctrl.Name in ["不看他", "不看他 (她)"]:
                    parent = ctrl.GetParentControl()
                    target_switch = None
                    if parent:
                        for child in parent.GetChildren():
                            if child.ControlTypeName in ["CheckBoxControl", "ButtonControl", "SwitchControl"] and child != ctrl:
                                target_switch = child
                                break
                    if not target_switch:
                        target_switch = ctrl
                    try_click(target_switch, max_retries=1, delay=0.2)
                    logger.info(f"[好友通过] 已触发屏蔽朋友圈: 不看他")
        except Exception as hide_ex:
            logger.warning(f"[好友通过] 设置朋友圈屏蔽异常: {hide_ex}")

