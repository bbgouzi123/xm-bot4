import os
import time
import random
import logging
import ctypes
import pyperclip
import uiautomation as uia

from src.uia.retry import try_click, random_delay, physical_click

logger = logging.getLogger("WeChatDriver")

def send_message_impl(driver_obj, who: str, message: str, wxid: str = None) -> bool:
    """发送文本消息"""
    if not driver_obj.is_connected() or not message:
        return False

    # 🛡️ 智能自愈：如果 who 是 wxid，反查其真实姓名
    if who and (who.startswith("wxid_") or "@chatroom" in who):
        if not wxid:
            wxid = who
        try:
            from src.utils.contacts_cache import contacts_cache
            _bot_wxid = getattr(driver_obj, "bot_wxid", None) or getattr(driver_obj, "_wxid", None) or "main"
            is_group = "@chatroom" in who
            resolved_name = contacts_cache.find_name_with_db_sync(_bot_wxid, who, is_group=is_group)
            if resolved_name:
                who = resolved_name
        except Exception as e:
            logger.debug(f"[发送消息] 自动反查微信号昵称异常: {e}")

    try:
        from src.utils.config_cache import config_cache
        global_forbidden_words = config_cache.get("forbidden_words", [])
        if global_forbidden_words:
            for word in global_forbidden_words:
                if word and word in message:
                    logger.warning(f"[违禁词拦截] 消息包含全局违禁词「{word}」，强行阻断发送！目标: {who}, 原文: {message}")
                    return False
    except Exception as e:
        logger.error(f"[违禁词拦截检测异常]: {e}")

    try:
        from src.utils.config_cache import config_cache
        blacklist = config_cache.get("blacklist", [])
        if blacklist:
            for b_item in blacklist:
                b_val = b_item if isinstance(b_item, str) else b_item.get("wxid") or b_item.get("nickname")
                if b_val and (b_val == who or b_val in who):
                    logger.warning(f"[黑名单拦截] 目标 {who} 属于企业黑名单，已强行阻断消息发送！")
                    return False
    except Exception as e:
        logger.error(f"[黑名单发送检测异常]: {e}")

    try:
        from src.uia.input_guard import uia_lock
        from src.utils.status_overlay import status_overlay
        with driver_obj._lock, uia_lock("正在发送消息"):
            status_overlay.update("发送中", "正在切换到微信聊天窗口...", who)
            max_retry = 3
            switched = False
            for attempt in range(max_retry):
                try:
                    from src.uia.retry.window_ops import ensure_wechat_foreground
                    ensure_wechat_foreground(driver_obj.hwnd)
                except Exception as e:
                    logger.error(f"[发送] 强行置前窗口异常: {e}")

                # 🛡️ 白屏自愈：在切换/操作聊天窗口前，检测微信是否处于白屏保护状态。
                # 白屏时 UIA 树元素仍在内存中（Exists() 为 True），但实际 UI 渲染冻结，
                # 任何点击/输入都会失效。点击任务栏微信图标可强制触发 UI 重绘解除白屏。
                # 耗时约 30~50ms（像素采样），每次发送消息执行一次，性能影响可忽略不计。
                try:
                    from src.uia.retry.wechat_healer import _heal_white_screen_if_needed
                    _heal_white_screen_if_needed(driver_obj.hwnd)
                except Exception as _ws_err:
                    logger.debug(f"[发送] 白屏预检异常(可忽略): {_ws_err}")

                if driver_obj.ChatWith(who, wxid=wxid):
                    switched = True
                    break
                
                logger.warning(f"[发送] 切换到 {who} 的聊天窗口失败，正在进行第 {attempt + 1} 次重试...")
                time.sleep(1.0)
            
            if not switched:
                print(f"[发送] 切换到 {who} 的聊天窗口彻底失败 (已尝试 {max_retry} 次)")
                status_overlay.update("发送失败", "切换目标聊天窗口失败", who)
                return False

            if ctypes.windll.user32.GetForegroundWindow() != driver_obj.hwnd:
                from src.uia.retry.window_ops import ensure_wechat_foreground
                ensure_wechat_foreground(driver_obj.hwnd)

            random_delay(0.3, 0.6)
            input_box = driver_obj._get_edit_control(who)

            if not input_box or not input_box.Exists(0.5):
                status_overlay.update("发送失败", "无法定位微信输入框控件", who)
                return False

            try:
                existing_text = ""
                val_pattern = input_box.GetValuePattern()
                if val_pattern:
                    existing_text = val_pattern.Value or ""
                if not existing_text:
                    legacy_pattern = input_box.GetLegacyIAccessiblePattern()
                    if legacy_pattern:
                        existing_text = legacy_pattern.Value or ""
                
                clean_existing = existing_text.replace('\ufffc', '').replace('\u200b', '').strip()
                if clean_existing:
                    logger.warning(f"[草稿拦截] 发现用户在 '{who}' 输入框中存有草稿 \"{clean_existing}\"，为避免强行覆盖用户文字，已安全拦截自动回复！")
                    status_overlay.update("客服避让", "检测到输入框内有草稿，已安全避让", who)
                    return False
            except Exception as draft_ex:
                logger.debug(f"[草稿拦截] 读取输入框草稿状态异常(可忽略): {draft_ex}")

            try:
                from src.utils.user_activity import is_user_active
                from src.uia.retry.window_ops import ensure_wechat_foreground
                from src.utils.stop_signal import stop_signal

                _wait_start = time.time()
                while is_user_active(cooldown_ms=1500, check_caret=True):
                    status_overlay.update("客服避让", "检测到用户活跃，等待中...", who)
                    if stop_signal.is_stopped:
                        logger.warning(f"[避让] 终止发送消息给 '{who}'，因为检测到停止信号")
                        status_overlay.update("客服避让", "收到全局停止信号", who)
                        return False
                    if time.time() - _wait_start > 3.0:
                        logger.warning(f"[避让] 放弃发送消息给 '{who}'，因为检测到客服持续活跃，避免强行干扰键盘鼠标操作")
                        status_overlay.update("客服避让", "用户在使用电脑，安全放弃", who)
                        return False
                    time.sleep(0.1)

                if stop_signal.is_stopped:
                    status_overlay.update("客服避让", "收到全局停止信号", who)
                    return False

                is_fg = (ctypes.windll.user32.GetForegroundWindow() == driver_obj.hwnd)

                if not is_fg:
                    if not is_user_active(cooldown_ms=1500, check_caret=True) and not stop_signal.is_stopped:
                        logger.info("[UIA] 微信不在前台，且客服空闲，尝试置顶微信窗口...")
                        ensure_wechat_foreground(driver_obj.hwnd)
                        time.sleep(0.2)
                        is_fg = (ctypes.windll.user32.GetForegroundWindow() == driver_obj.hwnd)

                if is_fg and not is_user_active(cooldown_ms=1500, check_caret=True) and not stop_signal.is_stopped:
                    rect = input_box.BoundingRectangle
                    physical_click((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2, restore_cursor=True)
                    random_delay(0.05, 0.1)
                else:
                    logger.warning("[UIA] 客服活跃或微信不在前台，放弃物理点击，安全回退到内存 try_click 兜底")
                    try_click(input_box, max_retries=2, delay=0.2)
            except Exception as e:
                logger.warning(f"[UIA] 点击输入框异常: {e}，回退 to try_click")
                try_click(input_box, max_retries=2, delay=0.2)

            if is_user_active(cooldown_ms=800, check_caret=True) or stop_signal.is_stopped:
                logger.warning(f"[避让] 在即将输入文本前检测到客服活动或停止信号，紧急终止发送消息以防冲突")
                status_overlay.update("客服避让", "键盘准备输入时用户介入，已紧急止付", who)
                return False

            status_overlay.update("发送中", "正在向输入框填充消息内容...", who)

            paste_verified = False

            try:
                val_pattern = input_box.GetValuePattern()
                if val_pattern:
                    val_pattern.SetValue(message)
                    random_delay(0.01, 0.03)
                    if (val_pattern.Value or "").strip() == message.strip():
                        paste_verified = True
                        logger.info("[发送] 优先通过 ValuePattern.SetValue 内存直写成功")
            except Exception as set_val_ex:
                logger.debug(f"[发送] 优先 ValuePattern 直写异常(将自动执行剪贴板兜底): {set_val_ex}")

            if not paste_verified:
                clipboard_ok = False
                for attempt in range(5):
                    try:
                        pyperclip.copy(message)
                        clipboard_ok = True
                        break
                    except Exception as clip_ex:
                        logger.warning(f"[发送] 复制到剪贴板失败，重试中 ({attempt + 1}/5): {clip_ex}")
                        time.sleep(0.08)

                if clipboard_ok:
                    try:
                        uia.SendKeys('{Ctrl}a')
                        random_delay(0.02, 0.05)
                        uia.SendKeys('{Ctrl}v')
                        random_delay(0.08, 0.15)
                        
                        val_pattern = input_box.GetValuePattern()
                        if val_pattern:
                            current_val = val_pattern.Value or ""
                            if current_val.strip() == message.strip():
                                paste_verified = True
                        else:
                            paste_verified = True
                    except Exception as paste_ex:
                        logger.warning(f"[发送] 粘贴/验证过程中出现异常: {paste_ex}")

            if not paste_verified:
                logger.warning("[发送] 所有静默写入与粘贴均失败，回退到键盘逐字模拟发送...")
                try:
                    uia.SendKeys('{Ctrl}a{Delete}')
                    random_delay(0.08, 0.15)
                    escaped_msg = message.replace('{', '{{}').replace('}', '{}}')
                    uia.SendKeys(escaped_msg, waitTime=0.03)
                    paste_verified = True
                except Exception as send_keys_ex:
                    logger.error(f"[发送] 键盘逐字模拟输入失败: {send_keys_ex}")

            try:
                uia.SendKeys('{Enter}')
                random_delay(0.1, 0.25)
                
                try:
                    from src.uia.message_direction import mark_message_direction
                    mark_message_direction(message, is_self=True)
                except Exception as cached_ex:
                    logger.error(f"[发送] 写入已发送消息缓存失败: {cached_ex}")

                try:
                    from src.utils.chat_history import ChatHistoryManager
                    history_mgr = ChatHistoryManager()
                    history_mgr.add_message(who, "我", "assistant", message)
                    logger.info(f"[发送] 成功发送消息并同步记入本地历史: session='{who}', content='{message[:20]}'")
                except Exception as hist_ex:
                    logger.error(f"[发送] 发送消息记入本地历史记录失败: {hist_ex}")

                status_overlay.update("发送成功", f"成功发送了 {len(message)} 字消息", who)
                return True
            except Exception as enter_ex:
                logger.error(f"[发送] 发送回车键失败: {enter_ex}")
                status_overlay.update("发送失败", f"回车确认失败: {enter_ex}", who)
                return False
    except Exception as e:
        logger.error(f"发送消息失败: {e}")
        from src.utils.status_overlay import status_overlay
        status_overlay.update("发送失败", f"异常崩溃: {e}", who)
        return False

def send_files_impl(driver_obj, who: str, file_path: str, wxid: str = None) -> bool:
    """发送文件"""
    if not driver_obj.is_connected() or not file_path or not os.path.exists(file_path):
        return False

    # 🛡️ 智能自愈：如果 who 是 wxid，反查其真实姓名
    if who and (who.startswith("wxid_") or "@chatroom" in who):
        if not wxid:
            wxid = who
        try:
            from src.utils.contacts_cache import contacts_cache
            _bot_wxid = getattr(driver_obj, "bot_wxid", None) or getattr(driver_obj, "_wxid", None) or "main"
            is_group = "@chatroom" in who
            resolved_name = contacts_cache.find_name_with_db_sync(_bot_wxid, who, is_group=is_group)
            if resolved_name:
                who = resolved_name
        except Exception as e:
            logger.debug(f"[发送文件] 自动反查微信号昵称异常: {e}")

    try:
        from src.uia.input_guard import uia_lock
        with driver_obj._lock, uia_lock(f"正在给 {who} 发送文件"):
            if not driver_obj.ChatWith(who, wxid=wxid):
                return False

            from src.utils.user_activity import is_user_active
            from src.utils.stop_signal import stop_signal
            
            _wait_start = time.time()
            while is_user_active(cooldown_ms=1500, check_caret=True):
                if stop_signal.is_stopped:
                    logger.warning(f"[避让] 终止发送文件给 '{who}'，因为检测到停止信号")
                    return False
                if time.time() - _wait_start > 5.0:
                    logger.warning(f"[避让] 放弃发送文件给 '{who}'，因为检测到客服持续活跃，避免强行干扰键盘鼠标操作")
                    return False
                time.sleep(0.1)

            if stop_signal.is_stopped:
                return False

            import ctypes
            if ctypes.windll.user32.GetForegroundWindow() != driver_obj.hwnd:
                from src.uia.retry.window_ops import ensure_wechat_foreground
                ensure_wechat_foreground(driver_obj.hwnd)

            random_delay(0.2, 0.4)
            input_box = driver_obj._get_edit_control(who)

            if not input_box or not input_box.Exists(0.5):
                return False

            try_click(input_box, max_retries=2, delay=0.2)
            random_delay(0.1, 0.2)

            from src.uia.clipboard_helper import copy_file_to_clipboard
            copy_file_to_clipboard(file_path)
            random_delay(0.2, 0.3)

            if is_user_active(cooldown_ms=800, check_caret=True) or stop_signal.is_stopped:
                logger.warning(f"[避让] 在即将粘贴文件前检测到客服活动或停止信号，紧急终止发送文件以防冲突")
                return False

            uia.SendKeys('{Ctrl}v')
            random_delay(1.5, 2.0)
            uia.SendKeys('{Enter}')
            random_delay(1.0, 1.5)

            try:
                filename = os.path.basename(file_path)
                is_img = file_path.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'))
                msg_tag = "[图片]" if is_img else f"[文件] {filename}"
                
                from src.uia.message_direction import mark_message_direction
                mark_message_direction(msg_tag, is_self=True)
                mark_message_direction(filename, is_self=True)
                mark_message_direction("[图片]", is_self=True)
                mark_message_direction("[文件]", is_self=True)
                
                from src.utils.chat_history import ChatHistoryManager
                history_mgr = ChatHistoryManager()
                history_mgr.add_message(who, "我", "assistant", msg_tag)
                logger.info(f"[发送文件] 成功发送文件并同步记入历史: {msg_tag}")
            except Exception as cache_ex:
                logger.error(f"[发送文件] 登记消息方向缓存或历史记录异常: {cache_ex}")

            return True
    except Exception as e:
        logger.error(f"发送文件失败: {e}")
        return False
