import os
import time
import logging
import uiautomation as uia

from src.uia.elements import WxName
from src.uia.message import parse_message

logger = logging.getLogger("WeChatDriver")

def get_all_messages_impl(driver_obj, parse_file: bool = False,
                         context_count: int = 20,
                         session_name: str = "",
                         scroll_to_bottom: bool = False) -> list:
    """获取当前聊天窗口的所有可见消息"""
    if not driver_obj.is_connected():
        return []

    try:
        import win32gui
        import win32con
        if win32gui.IsIconic(driver_obj.hwnd):
            logger.info(f"[UIA] 检测到微信窗口当前处于最小化状态，正在静默还原以加载 UIA 子树...")
            win32gui.ShowWindow(driver_obj.hwnd, win32con.SW_SHOWNOACTIVATE)
            time.sleep(0.2)
    except Exception as win_ex:
        logger.debug(f"[UIA] 检查/还原微信最小化窗口状态异常: {win_ex}")

    try:
        msg_list = driver_obj._walk_find('ListControl', name=WxName.MESSAGE_LIST,
                                   class_name='mmui::RecyclerListView', max_depth=8) or driver_obj._walk_find('ListControl', name=WxName.MESSAGE_LIST, max_depth=8)
        if not msg_list:
            return []

        messages = []
        if scroll_to_bottom:
            try:
                msg_list.SetFocus()
                uia.SendKeys('{End}')
                time.sleep(0.1)
            except Exception:
                pass

        children = list(msg_list.GetChildren())
        total_children = len(children)
        
        media_target_idx = -1
        if parse_file:
            for i in range(total_children - 1, -1, -1):
                try:
                    child = children[i]
                    parsed_temp = parse_message(child, nickname=driver_obj._nickname, session_name=session_name, use_click_check=False)
                    if parsed_temp and not parsed_temp.get("isSelf", False):
                        msg_type_temp = parsed_temp.get("type", "")
                        content_temp = parsed_temp.get("content", "")
                        if msg_type_temp in ("image", "voice", "file") or any(p in content_temp for p in ("[图片]", "[语音]", "[文件]")):
                            media_target_idx = i
                            logger.info(f"[多媒体处理] 智能匹配到最新多媒体消息索引: {i}, 内容: {content_temp}")
                            break
                except Exception:
                    continue

        for idx, item in enumerate(children):
            try:
                is_last = (idx == total_children - 1)
                parsed = parse_message(item, nickname=driver_obj._nickname, session_name=session_name, use_click_check=is_last)
                if not parsed:
                    continue

                try:
                    from src.uia.messager_intercept import audit_and_intercept_message
                    audit_and_intercept_message(driver_obj, item, parsed, session_name or "")
                except Exception as intercept_ex:
                    logger.error(f"[飞单拦截] 执行拦截模块错误: {intercept_ex}")

                content = parsed.get("content", "")
                is_self = parsed.get("isSelf", False)
                msg_type = parsed.get("type", "")

                if msg_type == "greet":
                    sender = "GREET"
                elif msg_type == "system":
                    sender = "SYS"
                elif msg_type == "recall":
                    sender = "Recall"
                elif msg_type == "time":
                    sender = "Time"
                else:
                    sender = (driver_obj._nickname or "我") if is_self else (session_name or "对方")

                is_media_target = (idx == media_target_idx) if media_target_idx != -1 else is_last
                if parse_file and not is_self and is_media_target:
                    from src.uia.messager_media import translate_voice_to_text, locate_local_voice_file, call_whisper_api
                    if msg_type == "voice" or content == "[语音]":
                        trans_text = translate_voice_to_text(item)
                        if trans_text:
                            content = f"[语音识别结果]: {trans_text}"
                        else:
                            try:
                                from src.crm.account_data import get_active_account
                                voice_path = locate_local_voice_file(getattr(driver_obj, "_wxid", None) or get_active_account())
                                if voice_path and os.path.exists(voice_path):
                                    logger.info(f"[语音兜底] 原生转文字超时，启用 Whisper 转换: {voice_path}")
                                    trans_text = call_whisper_api(voice_path)
                                    if trans_text:
                                        content = f"[语音识别结果]: {trans_text} [语音本地路径]: {voice_path}"
                                    else:
                                        content = f"[语音识别结果]: (翻译超时且 Whisper 未能提取) [语音本地路径]: {voice_path}"
                                else:
                                    content = "[语音识别结果]: (微信原生翻译超时)"
                            except Exception as err:
                                logger.error(f"[语音兜底] Whisper 兜底转换异常: {err}")
                                content = "[语音识别结果]: (微信翻译超时)"

                messages.append((sender, content))
            except Exception:
                continue

        return messages[-context_count:] if context_count and len(messages) > context_count else messages
    except Exception as e:
        logger.error(f"获取消息失败: {e}")
        return []
