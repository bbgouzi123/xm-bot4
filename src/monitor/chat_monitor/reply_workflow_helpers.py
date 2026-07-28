import asyncio
import logging
from typing import Any
from src.utils.websocket_manager import ws_manager
from .reply_workflow_post import handle_reply_success_actions

import hashlib

logger = logging.getLogger(__name__)

async def _broadcast_skip(engine: Any, task_id: str, name: str, wxid: str, message: str, skip_msg: str) -> None:
    """广播已跳过状态并记录指纹，防止未读消息在后续扫描中反复触发。"""
    await ws_manager.broadcast_task_update(
        task_id=task_id, task_type="自动回复", status="completed",
        progress=100, total=100, message=skip_msg,
        friend_name=name, friend_wxid=wxid, incoming_msg=message
    )
    keys_to_set = [name]
    if wxid and wxid != name:
        keys_to_set.append(wxid)
    for k in keys_to_set:
        engine._last_reply_time[k] = __import__("time").time()
        engine._fingerprints.setdefault(k, set()).add(hashlib.md5(f"{k}:{message}".encode()).hexdigest())
        try:
            from src.wechat_4x.wcdb_monitor_helpers import make_fingerprint
            engine._fingerprints.setdefault(k, set()).add(make_fingerprint(name, message))
            if wxid:
                engine._fingerprints.setdefault(wxid, set()).add(make_fingerprint(name, message))
        except Exception:
            pass

async def handle_filehelper_commands(engine: Any, name: str, user_name: str, message: str) -> bool:
    """拦截文件传输助手的远程控制与高危动作审批指令，如果命中控制指令则处理并返回 True，否则返回 False"""
    if name in ("filehelper", "文件传输助手") or user_name in ("filehelper", "文件传输助手"):
        from .promise_helper import handle_remote_approval_command
        is_cmd, resp_text = await handle_remote_approval_command(message, engine=engine)
        if is_cmd:
            from src.utils.uia_task_runner import run_uia_with_timeout
            try:
                await run_uia_with_timeout(engine.driver.send_message, 15.0, name, resp_text)
            except Exception as send_ex:
                logger.error(f"[FileHelper Remote] 发送控制命令回复失败: {send_ex}")
            return True
    return False

async def check_chat_injection(engine: Any, name: str, message: str, reply: str, actual_message: str, downloaded_paths: list) -> bool:
    """
    === 插队新消息拦截校验 ===
    为了防止在 AI 大模型接口响应期间或空闲等待期间，客户又发来新消息，
    导致当前的回复内容过时，且发送后将新消息强行顶成历史消息造成漏回。
    若检测到插队新消息，则终止本次发送并清理资源，返回 True，否则返回 False。
    """
    try:
        from src.utils.uia_task_runner import run_uia_with_timeout

        # 💡 【多会话竞态修复】只在当前微信前台活跃窗口确实是当前待发送会话 'name' 时，才执行前台实时插队校验。
        # 否则如果是后台会话，读取的其实是别的前台窗口的消息，会造成误拦截并导致卡死死循环。
        def _get_active_chat_name():
            try:
                if not engine.driver.root:
                    return ""
                chat_container = engine.driver.root.GroupControl(ClassName='mmui::ChatDetailView')
                if not chat_container.Exists(0.1):
                    return ""
                edit = chat_container.EditControl(ClassName="mmui::ChatInputField", searchDepth=4)
                if edit.Exists(0.1):
                    n = edit.Name or ""
                    import re
                    n = re.sub(r'\s+按住.*$', '', n)
                    n = re.sub(r'\(\d+\)$', '', n)
                    return n.strip()
            except Exception:
                pass
            return ""

        active_name = await run_uia_with_timeout(_get_active_chat_name, 2.0)
        if not active_name or active_name != name:
            logger.debug(f"[插队校验] 当前前台活跃会话为 '{active_name}'，与待发送会话 '{name}' 不一致，直接放行")
            return False

        last_msgs_before_send = await run_uia_with_timeout(
            engine.driver.get_all_messages, 8.0, False, 3, name, False
        )
        if last_msgs_before_send:
            last_sender, last_content = last_msgs_before_send[-1]
            driver_nickname = getattr(engine.driver, '_nickname', '') or '我'
            is_last_from_friend = last_sender not in (driver_nickname, "我", "自己", "SYS", "Time", "Recall", "GREET")
            # 如果微信聊天窗口内最后一条消息确实是好友发来的，且不属于我们这次自动回复的源消息
            if is_last_from_friend and last_content.strip() and last_content.strip() != message.strip():
                # 判断新消息内容是否已被我们刚才的 message 涵盖（例如由于防抖合并了它），如果没涵盖，则是全新插队消息
                if last_content.strip() not in message:
                    # 💡 【体验与速度优化】以下情况，我们不应该执行插队拦截，而是直接豁免放行：
                    # 1. 回复中包含下载物料、屏幕录制等重磅核心动作，拦截会导致大模型在下一轮重试时漏掉最关键的文件操作
                    has_heavy_action = bool(downloaded_paths) or (reply and any(kw in reply for kw in ["__live_record__", "http", "实时录制", "演示视频", "操作视频", "录个视频", "视频发给你", "录屏", "发资料", "发白皮书", "发文档", "发送文档", "发送文件"]))
                    
                    # 2. 插队消息是非常简短的确认词、语气词或催促词，长度 <= 4，通常不带有任何新业务问题
                    last_content_clean = last_content.strip().lower()
                    is_trivial_short = len(last_content_clean) <= 4
                    
                    # 3. 常见的催促/确认辅助词过滤
                    trivial_keywords = {
                        "好", "行", "okk", "ok", "👌", "嗯", "恩", "哦", "啊", "哈",
                        "发吧", "你发吧", "发给我", "发过来", "赶紧发", "发送", "确定", 
                        "没问题", "好吧", "好的吧", "好的呢", "好的哈", "那行", "收到", "知道了"
                    }
                    is_trivial_word = any(kw in last_content_clean for kw in trivial_keywords)
                    
                    if has_heavy_action or is_trivial_short or is_trivial_word:
                        logger.info(f"[插队豁免] 检测到会话 '{name}' 插队消息 '{last_content}' 属于非破坏性输入（重磅动作={has_heavy_action}, 极短={is_trivial_short}, 催促确认={is_trivial_word}），豁免拦截并继续直接发送原回复")
                        return False

                    import hashlib
                    logger.warning(f"[插队拦截] 会话 '{name}' 在发送前检测到新插队消息: '{last_content}'，终止本次过期发送，放回重试")
                    # 释放正在处理标志，以供下一轮循环识别该新消息
                    engine._clear_session_processing(name)
                    # 清除为该会话添加的本次已回复指纹，并清空当前会话的消息指纹缓存
                    # 这能确保下一轮扫描在当前活跃窗口中，能将未读数强行放行重新识别为新消息并触发新一轮自动回复，绝不漏掉
                    engine._fingerprints.pop(name, None)
                    if hasattr(engine, '_last_seen_msg'):
                        engine._last_seen_msg.pop(name, None)
                    await ws_manager.broadcast_task_update(
                        task_id=f"auto_reply_{name}", task_type="自动回复", status="completed",
                        progress=100, total=100, message="检测到用户在此期间新发了消息，已终止当前回复并放回重试",
                        friend_name=name, incoming_msg=actual_message
                    )
                    # 清理下载的临时文件
                    if downloaded_paths:
                        from .reply_helper import cleanup_temp_files
                        cleanup_temp_files(downloaded_paths)
                    return True
    except Exception as check_inject_ex:
        logger.warning(f"[插队拦截] 预检插队新消息异常: {check_inject_ex}")
    return False

def get_originally_hidden_state(engine: Any) -> bool:
    """获取当前微信窗口是否本就处于后台/不可见/最小化状态"""
    try:
        import win32gui
        import ctypes
        wx_hwnd = engine.driver.hwnd
        if wx_hwnd:
            is_visible = win32gui.IsWindowVisible(wx_hwnd)
            is_iconic = ctypes.windll.user32.IsIconic(wx_hwnd)
            if not is_visible or is_iconic:
                return True
    except Exception:
        pass
    return False

def restore_hidden_state(engine: Any):
    """如果有始有终要求且微信处于原本后台，重新将其最小化收回"""
    try:
        import win32gui
        import win32con
        wx_hwnd = engine.driver.hwnd
        if wx_hwnd:
            win32gui.ShowWindow(wx_hwnd, win32con.SW_SHOWMINIMIZED)
            logger.info("[工作流] 有始有终：已成功将微信窗口重新最小化隐藏")
    except Exception as hide_ex:
        logger.debug(f"[工作流] 有始有终最小化收回微信窗口异常: {hide_ex}")


def check_and_clear_unread_physically_sync(driver: Any, name: str) -> bool:
    """在后台 COM 工作线程中同步执行：检查目标会话未读红点，存在则进行物理点击以消除，并再次 ClearChatFocus"""
    try:
        # 💡 【防双击强固】发送回复后，微信原生机制会自动清除会话未读状态。
        # 此处的二次保障点击属于高危且冗余的交互，在微信 UI 刷新延迟时，极易与新一轮扫描切换连发拼成物理双击。
        # 我们在此处绝对禁止发起任何逻辑或物理点击动作。
        logger.info(f"[工作流] 会话 '{name}' 的自动回复已成功发送，安全跳过收尾阶段的消红点点击以绝对防御双击")
        return True
    except Exception as e:
        logger.error(f"[check_and_clear_unread_physically_sync] 异常: {e}")
        return False


async def finalize_workflow_cleanup(engine: Any, name: str, originally_hidden: bool):
    """清理焦点，保障消除未读红点，若需要则恢复最小化（从 reply_workflow 抽离保证 300 行规范）"""
    # 🚀 【前后台防干扰与死锁拦截】
    # 如果微信主窗口当前不处于活动前台（例如被遮挡、最小化或用户在使用其他程序），
    # 此时强行通过 UIA 物理点击消除红点或转移焦点极易引发 UIA 锁死超时（如 timeout_on_ClearChatFocus）。
    # 只要微信窗口不活动，我们直接安全跳过物理清理。
    import win32gui
    hwnd = getattr(engine.driver, 'hwnd', None)
    if not hwnd or not win32gui.IsWindow(hwnd) or win32gui.IsIconic(hwnd) or win32gui.GetForegroundWindow() != hwnd:
        logger.debug(f"[工作流] 微信窗口非活动，跳过收尾 UIA 物理清理 (ClearChatFocus)")
        return

    focus_cleared = False
    try:
        from src.utils.uia_lock import uia_lock, UIATaskPriority
        from src.utils.uia_task_runner import run_uia_with_timeout
        async with uia_lock(UIATaskPriority.NORMAL, f"释放焦点→{name[:10]}", timeout=10.0):
            focus_cleared = await run_uia_with_timeout(engine.driver.ClearChatFocus, 5.0, name, True)
    except Exception as clear_ex:
        logger.debug(f"[工作流] 优先释放聊天焦点异常: {clear_ex}")

    try:
        from src.utils.uia_task_runner import run_uia_with_timeout
        await run_uia_with_timeout(check_and_clear_unread_physically_sync, 10.0, engine.driver, name)
    except Exception as unread_clear_ex:
        logger.debug(f"[工作流] 强制消除未读二次保障异常: {unread_clear_ex}")

    if focus_cleared and originally_hidden:
        restore_hidden_state(engine)


def extract_and_save_profile(name: str, actual_message: str, account_id: str, profile_manager: Any, wxid: str = None):
    """提取消息中的电话、地址等 CRM 画像关键线索并同步"""
    try:
        from src.crm.profile_extractor import extract_contact_info_from_message
        extracted_info = extract_contact_info_from_message(actual_message)
        if extracted_info:
            logger.info(f"[CRM画像线索提取] 从好友 '{name}' 消息中提取到关键线索: {extracted_info}")
            contact_wxid = wxid or name
            if not wxid:
                try:
                    from src.utils.contacts_cache import contacts_cache
                    all_friends = contacts_cache.get_friends(account_id)
                    found_wxid = next((f.get("wxid", "") for f in all_friends if (f.get("name") or "").strip() == name.strip() or (f.get("remark") or "").strip() == name.strip()), "")
                    if found_wxid:
                        contact_wxid = found_wxid
                except Exception:
                    pass
            profile_manager.update_from_ai_tags(wxid=contact_wxid, raw_tags=extracted_info, source="chat", nickname=name)
    except Exception as crm_extract_err:
        logger.error(f"[CRM画像线索提取] 提取或更新关键标签失败: {crm_extract_err}")


def check_self_reply_prevention(name: str, message: str, context_msgs: list, engine: Any, ws_manager: Any, actual_message: str, wxid: str = None, is_group: bool = False) -> bool:
    """防自回复终极拦截：如果最新消息是我方回复，则终止当前回复，返回 True，否则返回 False"""
    import hashlib
    if context_msgs:
        last_msg_item = context_msgs[-1]
        
        # 🌟 核心修复：防止因数据库或物理UIA同步滞后导致最新历史还没来得及更新，误判为自己发送
        last_content = (last_msg_item.get("content") or "").strip()
        incoming_content = (actual_message or message or "").strip()
        if last_content != incoming_content:
            is_placeholder_match = (
                last_content in ("[图片]", "[语音]", "[文件]", "[视频]", "图片", "语音", "文件", "视频") and
                incoming_content in ("[图片]", "[语音]", "[文件]", "[视频]", "图片", "语音", "文件", "视频")
            )
            if not is_placeholder_match:
                logger.info(f"[工作流] 上下文最新消息 ('{last_content}') 与刚收到触发的消息 ('{incoming_content}') 不一致，说明最新记录未同步完成，跳过防自回复拦截")
                return False

        if last_msg_item.get("role", "user") == "assistant":
            logger.info(f"[工作流] 会话 '{name}' 的最新一条消息实际由机器人自己发送(role=assistant)，主动跳过本次自动回复，防御自回复")
            try:
                keys_to_set = [name]
                if wxid and wxid != name:
                    keys_to_set.append(wxid)
                for k in keys_to_set:
                    engine._last_reply_time[k] = __import__("time").time()
                    fp = hashlib.md5(f"{k}:{message}".encode()).hexdigest()
                    engine._fingerprints.setdefault(k, set()).add(fp)
            except Exception:
                pass
            # 广播已忽略状态，避免控制中心卡死
            bot_wxid = getattr(engine.driver, "bot_wxid", None) or getattr(engine.driver, "_wxid", None)
            asyncio.create_task(ws_manager.broadcast_task_update(
                task_id=f"auto_reply_{wxid or name}", task_type="自动回复", status="completed",
                progress=100, total=100, message="最新消息为自己发送，已自动忽略",
                friend_name=name, friend_wxid=wxid, bot_wxid=bot_wxid, is_group=is_group,
                incoming_msg=actual_message
            ))
            return True
    return False


# handle_reply_success_actions已解耦移出至reply_workflow_post.py中以遵守300行有效代码限制



async def compile_and_clean_reply(engine: Any, name: str, message: str, reply: str, task_id: str, wxid: str, actual_message: str) -> tuple[bool, str, list]:
    compiler_media_paths = []
    try:
        from src.utils.rich_reply_compiler import compile_rich_reply
        compiled_text, compiler_media_paths = compile_rich_reply(reply)
        reply = compiled_text
        # 💡 物理消除发送电话、地址等信息时尾随的干扰表情符号
        import re
        phone_address_keywords = ["电话", "手机", "号码", "联系方式", "微信", "地址", "位置", "在哪", "定位", "门牌"]
        if reply and any(k in reply for k in phone_address_keywords):
            emoji_only_tail = re.compile(
                r'[\s\U00010000-\U0010ffff\U00002600-\U000027BF\u200d\u2600-\u26FF\u2700-\u27BF]+$',
                flags=re.UNICODE
            )
            reply = emoji_only_tail.sub('', reply).strip()
            
        # 🚨 终极防线：绝对禁止将原始/残缺的微信多媒体 XML 配置作为文本直接发送，防止群聊显示乱码
        if reply and (reply.strip().startswith("<msg") or reply.strip().startswith("<?xml") or "<appmsg" in reply or "<img" in reply):
            logger.warning(f"[ReplyEngine] 检测到待回复内容中包含原始多媒体 XML 数据，已阻断以防错发乱码！内容摘要: {reply[:100]}")
            from src.utils.websocket_manager import ws_manager
            await ws_manager.broadcast_task_update(
                task_id=task_id, 
                task_type="自动回复", 
                status="error", 
                progress=0, 
                total=1, 
                message="回复内容包含原始 XML 多媒体格式，已安全拦截", 
                friend_name=name, 
                friend_wxid=wxid,
                incoming_msg=actual_message
            )
            return False, "", []
    except Exception as compile_err:
        logger.error(f"[ReplyEngine] 富文本逆向编译异常: {compile_err}", exc_info=True)

    if (not reply or not reply.strip()) and not compiler_media_paths:
        try:
            import hashlib
            fp = hashlib.md5(f"{name}:{message}".encode()).hexdigest()
            engine._fingerprints.setdefault(name, set()).add(fp)
            if wxid and wxid != name:
                engine._fingerprints.setdefault(wxid, set()).add(fp)
        except Exception:
            pass
        from src.utils.websocket_manager import ws_manager
        await ws_manager.broadcast_task_update(task_id=task_id, task_type="自动回复", status="completed", progress=100, total=100, message="回复内容为空，不执行发送", friend_name=name, friend_wxid=wxid, incoming_msg=actual_message)
        return False, reply, compiler_media_paths

    return True, reply, compiler_media_paths

