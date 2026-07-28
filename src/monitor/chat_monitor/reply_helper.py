import logging
import os
import time
import asyncio
from typing import Any, List, Optional
from .reply_media_helper import (
    capture_and_send_screen,
    cleanup_temp_files,
    handle_live_record_action,
    download_reply_materials
)

logger = logging.getLogger(__name__)

def update_crm_profile(raw_reply: str, reply_text: str, name: str, user_name: str, account_id: str, profile_manager: Any) -> int:
    """提取大模型回复中的 CRM 画像标签并同步到客户管理库中"""
    import re, json
    crm_match = re.search(r'<CRM_Action>(.*?)</CRM_Action>', raw_reply, re.DOTALL)
    crm_action_data = None
    if crm_match:
        try:
            crm_action_data = json.loads(crm_match.group(1).strip())
        except Exception:
            pass

    from src.crm.profile_extractor import extract_profile_from_reply
    clean_reply, extracted_tags = extract_profile_from_reply(reply_text)
    profile_tags = extracted_tags or {}

    if crm_action_data:
        profile_tags.update({
            "sales_stage": crm_action_data.get("当前阶段", ""),
            "intent": crm_action_data.get("意向度", ""),
            "psychology": crm_action_data.get("客户心理", ""),
            "budget": crm_action_data.get("预算推测", ""),
            "pain_point": crm_action_data.get("需求痛点概括", "")
        })
        profile_tags = {k: v for k, v in profile_tags.items() if v}

    if profile_tags:
        try:
            from src.utils.contacts_cache import contacts_cache
            real_wxid = next((f.get('wxid') for f in contacts_cache.get_friends(account_id) if f.get('name') == name), "")
            profile_manager.update_from_ai_tags(wxid=real_wxid or user_name or name, raw_tags=profile_tags, source="chat", nickname=name)
            return len(profile_tags)
        except Exception as e:
            logger.error(f"[ReplyHelper] 写入 CRM 画像失败: {e}")
    return 0

def parse_and_process_ai_reply(raw_reply: str, name: str, user_name: str, account_id: str, is_group: bool, profile_manager: Any, is_at_all: bool = False, is_physical_at: bool = True) -> tuple[str, int]:
    """解析AI回复内容，更新 CRM 画像并返回最终发送文本段与提取的标签数"""
    import re
    reply_match = re.search(r'<Reply>(.*?)</Reply>', raw_reply, re.DOTALL)
    reply_text = reply_match.group(1).strip() if reply_match else raw_reply
    if not reply_match:
        for p in (r'\{[^}]*"当前阶段"[^}]*\}', r'<CRM_Action>.*?</CRM_Action>', r'<Action_CaptureScreen.*?>.*?</Action_CaptureScreen>', r'<Action_CaptureScreen.*?/>'):
            flags = re.DOTALL | re.IGNORECASE if "Action" in p or "CRM" in p else 0
            reply_text = re.sub(p, '', reply_text, flags=flags).strip()

    tags_count = update_crm_profile(raw_reply, reply_text, name, user_name, account_id, profile_manager)
    from src.crm.profile_extractor import extract_profile_from_reply
    reply, _ = extract_profile_from_reply(reply_text)
    
    # 🌟 只有当消息是真实物理艾特（is_physical_at=True）时才在群聊中加 @ 提醒，热度免艾特期间不加 @，并清除 AI 误生成的开头的 @ 提及，更贴近真实聊天场景
    if is_group and user_name and user_name not in (name, "对方", "自己", "我") and not is_at_all:
        if is_physical_at:
            if not reply.startswith(f"@{user_name}"):
                reply = f"@{user_name} \n{reply}"
        else:
            # 清除 AI 可能在开头误生成的 @ 提及前缀，例如 "@至尊宝"、"@至尊宝，"、"@至尊宝：" 等，使其聊天回复显得更自然真实
            pattern = rf"^@[\s\u2005]*{re.escape(user_name)}[\s\u2005\n,，:：]*"
            reply = re.sub(pattern, "", reply).strip()
    return reply, tags_count

def report_ai_usage(ai_service: Any, actual_message: str, reply: str):
    """异步上报 AI Token 的使用额度统计数据"""
    async def _report():
        try:
            from src.utils.license_validator import LicenseValidator
            uid = LicenseValidator._get_sso_user_id()
            if uid:
                LicenseValidator._http_request("POST", "/api/ai/report-usage", {
                    "user_id": uid, "platform": getattr(ai_service, 'platform', 'coze'),
                    "model": "unknown", "message_length": len(actual_message), "response_length": len(reply)
                })
        except Exception:
            pass
    asyncio.create_task(_report())

def deduct_ai_quota(account_id: str, friend_wxid: str, message: str, reply: str, is_group: bool, nickname: str = "", friend_nickname: str = ""):
    """回复成功后，向服务端申请扣减 1 个额外额度（如果已超套餐），并记录审计流水"""
    try:
        from src.utils.license_validator import LicenseValidator
        body = {
            "wechat_id": account_id, "friend_wxid": friend_wxid, "friend_nickname": friend_nickname,
            "wechat_nickname": nickname, "is_group": is_group, "prompt": message, "reply": reply, "amount": 1
        }
        res = LicenseValidator._http_request("POST", "/api/ai/quota/deduct", body)
        if res and not res.get("success"):
            err_msg = res.get("detail") or res.get("message") or res.get("msg") or "未知错误"
            logger.warning(f"[额度扣费] 原子扣费接口返回失败: {err_msg}")
        else:
            logger.info(f"[额度扣费] 原子扣费成功: {account_id}")
    except Exception as e:
        logger.error(f"[额度扣费] 调用扣费接口发生异常: {e}")

def handle_reply_success(
    driver: Any, name: str, user_name: str, is_group: bool, actual_message: str, reply: str,
    intent: str, account_id: str, chat_round: int, stats: dict, ai_service: Any, history_mgr: Any,
    downloaded_paths: Optional[list] = None, wxid: str = None
):
    """处理自动回复成功后的状态及数据上报"""
    from src.utils.uia_task_runner import report_uia_success
    report_uia_success(name)
    stats["replied"] += 1
    
    from .base import _chat_daily_counter
    _chat_daily_counter.increment("group_message" if is_group else "auto_reply", account_id)
    if not is_group and intent not in ("超额提示", "关键词自动回复"):
        _chat_daily_counter.increment(f"friend_reply_{name}", account_id)
    _chat_daily_counter.increment("total_tokens", account_id, amount=len(actual_message) + len(reply))

    # 🌟 统一使用 wxid or name 作为主键 session_key，防止昵称与 wxid 键值冲突导致脑裂/失忆问题
    session_key = wxid or name

    report_ai_usage(ai_service, actual_message, reply)
    deduct_ai_quota(
        account_id=account_id, friend_wxid=session_key, message=actual_message, reply=reply,
        is_group=is_group, nickname=getattr(driver, "_nickname", ""), friend_nickname=name
    )

    history_mgr.add_message(session_key, user_name if is_group else name, "user", actual_message, is_group=is_group)
    history_mgr.add_message(session_key, "assistant", "assistant", reply, is_group=is_group)
    
    # 💡 将成功发送的营销物料文件名同步登记到本地历史记录中，防止冷启动时无法识别白底卡片导致重复自动回复
    if downloaded_paths:
        import os
        for path in downloaded_paths:
            filename = os.path.basename(path)
            history_mgr.add_message(session_key, "assistant", "assistant", f"[文件]{filename}", is_group=is_group)

    _record_chat_collection(account_id, session_key, user_name if is_group else name, actual_message, reply, is_group)

    try:
        from src.utils.cloud_sync import get_cloud_client
        client = get_cloud_client()
        if is_group:
            client.report_event(event_type="group_message", event_data={
                "wxid": session_key, "sender": user_name, "message": actual_message, "reply": reply
            })
        else:
            client.report_chat_log(wxid=session_key, message=actual_message, reply=reply)
        logger.info(f"[ReplyHelper] 事件上报成功: wxid={session_key}")
    except Exception as report_err:
        logger.error(f"[ReplyHelper] 事件上报失败: {report_err}")

    # 异步触发长期客户画像更新
    if not is_group:
        try:
            from src.crm.auto_analyser import trigger_async_auto_analyze
            trigger_async_auto_analyze(session_key, nickname=name, account_id=account_id)
        except Exception as crm_err:
            logger.debug(f"[CRM] 异步触发客户画像提取异常: {crm_err}")

def _record_chat_collection(account_id: str, name: str, sender: str, actual_message: str, reply: str, is_group: bool):
    try:
        from src.utils.chat_collection import ChatCollectionManager
        ChatCollectionManager.get_instance().collect_messages(account_id, name, [
            {"sender": sender, "role": "user", "content": actual_message},
            {"sender": "assistant", "role": "assistant", "content": reply}
        ])
    except Exception:
        pass

async def dispatch_reply_messages(
    driver: Any, name: str, reply_segments: list, downloaded_paths: list, need_capture_screen: bool, is_group: bool = False, wxid: str = None, is_live_record: bool = False
) -> tuple[bool, bool, str]:
    """通过 UIBus 或者 UIA 锁机制发送回复文本 and 多媒体附件，返回 (success, bus_used, error_msg)"""
    bus_used, success, loop = False, False, asyncio.get_event_loop()
    error_msg = ""
    from src.uia.input_guard import uia_lock as physical_lock, UIAInterruptError
    from src.utils.stop_signal import stop_signal
    from src.utils.status_overlay import status_overlay
    
    # 🌟 防御启动竞争：如果微信主窗口还没连接就绪，尝试循环等待其连接就绪（最大等待 10 秒），防止 WCDB 感知先行而 UIA 尚未就绪导致物理驱动失败
    wait_time = 0
    while not driver.is_connected() and wait_time < 20:
        logger.info(f"[ReplyHelper] 微信实例 UIA 尚未连接就绪，自动回复静默等待中 (已等待 {wait_time * 0.5:.1f} 秒)...")
        status_overlay.update("等待中", "等待微信窗口连接就绪...", name)
        await asyncio.sleep(0.5)
        wait_time += 1
        
    initial_msg = "【群聊回复中】正在执行自动化回复，请勿操作键盘鼠标..." if is_group else f"正在发送智能回复 (共 {len(reply_segments)} 段)..."
    status_overlay.update("发送中", f"准备投递共 {len(reply_segments)} 段回复...", name)

    try:
        with physical_lock(initial_msg, hwnd=getattr(driver, 'hwnd', None)):
            if not need_capture_screen:
                try:
                    from src.orchestrator.ui_bus import ui_bus, UICommand, UICommandKind, UICommandPriority, UICommandStatus
                    cur_wxid = getattr(driver, "_wxid", "") or ""
                    segments_success = []
                    for idx, seg in enumerate(reply_segments):
                        if stop_signal.is_stopped:
                            raise UIAInterruptError("检测到全局停止信号")
                        physical_lock.update_status(f"正在发送第 {idx + 1}/{len(reply_segments)} 段回复...")
                        if idx > 0:
                            status_overlay.update("避让中", f"等待分段发送间隔 (第 {idx + 1}/{len(reply_segments)} 段)...", name)
                            await asyncio.sleep(max(0.3, 0.3 + len(seg) * 0.02 + __import__("random").uniform(0.1, 0.3)))
                            
                        status_overlay.update("发送中", f"投递第 {idx + 1}/{len(reply_segments)} 段消息...", name)
                        cmd = UICommand(wxid=cur_wxid, kind=UICommandKind.SEND_MESSAGE, payload={"target": name, "text": seg, "wxid": wxid}, priority=UICommandPriority.NORMAL, timeout=40.0)
                        ui_bus.submit(cmd)
                        res = await loop.run_in_executor(None, ui_bus.await_result, cmd.id, 45.0)
                        ok = res.status == UICommandStatus.SUCCESS and res.result
                        segments_success.append(ok)
                        if ok:
                            bus_used = True
                        else:
                            logger.error(f"[ReplyHelper] UIBus 发送文本第 {idx + 1} 段失败: status={res.status}, cmd_id={cmd.id}")
                            error_msg = f"UIBus 发送文本段失败 (段 {idx + 1}), 请检查微信窗口或查看系统日志"
                            
                    success = all(segments_success) if segments_success else (True if (downloaded_paths or is_live_record) else False)
                    if success and downloaded_paths:
                        for f_idx, path in enumerate(downloaded_paths):
                            if stop_signal.is_stopped:
                                raise UIAInterruptError("检测到全局停止信号")
                            physical_lock.update_status(f"正在发送多媒体附件 ({f_idx + 1}/{len(downloaded_paths)})...")
                            status_overlay.update("发送中", f"发送文件附件 ({f_idx + 1}/{len(downloaded_paths)})...", name)
                            await asyncio.sleep(1.0)
                            file_cmd = UICommand(wxid=cur_wxid, kind=UICommandKind.SEND_FILE, payload={"target": name, "file_path": path, "wxid": wxid}, priority=UICommandPriority.NORMAL, timeout=50.0)
                            ui_bus.submit(file_cmd)
                            res = await loop.run_in_executor(None, ui_bus.await_result, file_cmd.id, 55.0)
                            if res.status != UICommandStatus.SUCCESS or not res.result:
                                success = False
                                error_msg = "UIBus 发送附件媒体失败，请检查微信窗口或查看系统日志"
                                logger.error(f"[ReplyHelper] UIBus 发送附件失败: cmd_id={file_cmd.id}, status={res.status}")
                    elif not success and not error_msg:
                        error_msg = "UIBus 分段发送未全部成功"
                        bus_used = False
                except UIAInterruptError:
                    raise
                except Exception as e:
                    logger.error(f"[ReplyHelper] UIBus 分段发送异常: {e}", exc_info=True)
                    error_msg = f"UIBus 分段发送异常: {e}"
                    bus_used = False

            if not bus_used:
                try:
                    from src.utils.uia_lock import uia_lock, UIATaskPriority
                    from src.utils.uia_task_runner import run_uia_with_timeout
                    async with uia_lock(UIATaskPriority.NORMAL, f"自动回复→{name[:10]}", timeout=30):
                        segments_success = []
                        for idx, seg in enumerate(reply_segments):
                            if stop_signal.is_stopped:
                                raise UIAInterruptError("检测到全局停止信号")
                            physical_lock.update_status(f"正在发送第 {idx + 1}/{len(reply_segments)} 段回复 (直发)...")
                            if idx > 0:
                                status_overlay.update("避让中", f"等待分段发送间隔 (直发 {idx + 1}/{len(reply_segments)}段)...", name)
                                await asyncio.sleep(max(0.3, 0.3 + len(seg) * 0.02 + __import__("random").uniform(0.1, 0.3)))
                            try:
                                status_overlay.update("发送中", f"正在直发第 {idx + 1}/{len(reply_segments)} 段消息...", name)
                                segments_success.append(await run_uia_with_timeout(driver.send_message, 15.0, name, seg, wxid=wxid))
                            except Exception as send_err:
                                logger.error(f"[ReplyHelper] 直发文本失败: {send_err}")
                                segments_success.append(False)
                            
                        success = all(segments_success) if segments_success else (True if (downloaded_paths or is_live_record) else False)
                        if success and downloaded_paths:
                            for f_idx, path in enumerate(downloaded_paths):
                                if stop_signal.is_stopped:
                                    raise UIAInterruptError("检测到全局停止信号")
                                physical_lock.update_status(f"正在发送多媒体附件 ({f_idx + 1}/{len(downloaded_paths)}) (直发)...")
                                status_overlay.update("发送中", f"直发多媒体文件 ({f_idx + 1}/{len(downloaded_paths)})...", name)
                                await asyncio.sleep(1.0)
                                try:
                                    await run_uia_with_timeout(driver.SendFiles, 30.0, name, path, wxid=wxid)
                                except Exception as file_send_err:
                                    logger.error(f"[ReplyHelper] 直发附件失败: {file_send_err}")
                                    success = False
                        if success and need_capture_screen:
                            if stop_signal.is_stopped:
                                raise UIAInterruptError("检测到全局停止信号")
                            physical_lock.update_status("正在截屏并发送截图...")
                            status_overlay.update("发送中", "截屏安全核验与发送中...", name)
                            await capture_and_send_screen(driver, name)
                        
                        if not success and not error_msg:
                            error_msg = "直发 UIA 执行失败，请检查微信窗口状态或查看系统日志"
                except UIAInterruptError:
                    raise
                except TimeoutError as te:
                    logger.error(f"[ReplyHelper] 直发 UIA 锁获取或操作超时/熔断: {te}", exc_info=True)
                    success = False
                    error_msg = "获取 UIA 锁超时（可能是其他自动化动作积压较多），请查看系统日志"
                except Exception as direct_err:
                    logger.error(f"[ReplyHelper] 直发 UIA 过程发生异常: {direct_err}", exc_info=True)
                    success = False
                    error_msg = f"直发 UIA 发送异常: {direct_err}"
    except UIAInterruptError as esc_err:
        logger.warning(f"[ReplyHelper] 自动回复发送流被用户通过 ESC 或停止信号中断: {esc_err}")
        success = False
        error_msg = "自动回复已由客服手动按 ESC 或停止信号紧急终止"
        status_overlay.update("客服避让", "回复已被用户人工 ESC 键紧急终止", name)
    except Exception as outer_err:
        logger.error(f"[ReplyHelper] 自动回复发送流发生外层未捕获异常: {outer_err}", exc_info=True)
        success = False
        error_msg = f"发送过程未捕获异常: {outer_err}"
        status_overlay.update("发送失败", f"投递错误: {outer_err}", name)

    if success:
        status_overlay.update("已完成", "所有回复投递成功", name)
    return success, bus_used, error_msg
