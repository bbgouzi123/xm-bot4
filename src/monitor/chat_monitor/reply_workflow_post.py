import time
import logging
import hashlib
import asyncio
from typing import Any
from src.utils.websocket_manager import ws_manager
from .reply_helper import handle_reply_success
from .identity_action import execute_post_reply_identity_action

logger = logging.getLogger(__name__)

async def handle_reply_success_actions(
    engine: Any, name: str, user_name: str, is_group: bool, actual_message: str, reply: str,
    intent: str, account_id: str, chat_round: int, originally_hidden: bool,
    downloaded_paths: list, is_live_record: bool, bus_used: bool, identity_action: Any,
    history_mgr: Any, message: str, is_high_intent: bool, wxid: str = None,
    file_to_send: Any = None
):
    """处理自动回复发送成功后的画像同步、打标、智能分流、录像动作等辅助操作，以彻底精简主流程单文件代码行数"""
    for k in {name, wxid} - {None, ""}:
        engine._last_reply_time[k] = time.time()
    try:
        import app.state as app_state
        if not hasattr(app_state, 'last_sent_messages'):
            app_state.last_sent_messages = []
        if reply:
            app_state.last_sent_messages.append(reply.strip())
            # 对于分段发送的消息，进行分割存储，确保任意一段都能与摘要匹配成功
            for s in reply.split("\n\n"):
                if s.strip():
                    app_state.last_sent_messages.append(s.strip())
        
        # 🌟 针对多媒体发送（图片、视频、文件、录像），记录其在会话列表中对应的摘要占位符，防御自回复误判
        if downloaded_paths:
            for p in downloaded_paths:
                p_lower = str(p).lower()
                if any(p_lower.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")):
                    app_state.last_sent_messages.append("[图片]")
                    app_state.last_sent_messages.append("图片")
                elif any(p_lower.endswith(ext) for ext in (".mp4", ".mov", ".avi", ".mkv", ".flv", ".3gp")):
                    app_state.last_sent_messages.append("[视频]")
                    app_state.last_sent_messages.append("视频")
                else:
                    app_state.last_sent_messages.append("[文件]")
                    app_state.last_sent_messages.append("文件")
        if is_live_record:
            app_state.last_sent_messages.append("[视频]")
            app_state.last_sent_messages.append("视频")

        if len(app_state.last_sent_messages) > 50:
            app_state.last_sent_messages = app_state.last_sent_messages[-50:]
    except Exception as e:
        logger.debug(f"[工作流] 记录我方发送历史异常: {e}")
        
    if not hasattr(engine, '_last_reply_msg'):
        engine._last_reply_msg = {}
    engine._last_reply_msg[name] = actual_message
    
    handle_reply_success(
        engine.driver, name, user_name, is_group, actual_message, reply,
        intent, account_id, chat_round, engine._stats, engine.ai_service, history_mgr,
        downloaded_paths, wxid=wxid
    )

    # 包含多媒体占位符的缓存更新
    media_reply = reply
    if downloaded_paths:
        for p in downloaded_paths:
            p_lower = str(p).lower()
            if any(p_lower.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")):
                media_reply = (media_reply or "") + "\n[图片]"
            elif any(p_lower.endswith(ext) for ext in (".mp4", ".mov", ".avi", ".mkv", ".flv", ".3gp")):
                media_reply = (media_reply or "") + "\n[视频]"
            else:
                media_reply = (media_reply or "") + "\n[文件]"
    if is_live_record:
        media_reply = (media_reply or "") + "\n[视频]"

    engine._update_message_cache(name, message, media_reply, engine.get_account_partition())
    keys_to_set = [name]
    if wxid and wxid != name:
        keys_to_set.append(wxid)
    for k in keys_to_set:
        engine._fingerprints.setdefault(k, set()).add(hashlib.md5(f"{k}:{reply}".encode()).hexdigest())
        if media_reply != reply:
            engine._fingerprints[k].add(hashlib.md5(f"{k}:{media_reply}".encode()).hexdigest())

    # 🌟 主动向全局消息方向缓存中登记，防止列表重复扫描时产生多余的物理点击
    try:
        from src.uia.message_direction import mark_message_direction
        mark_message_direction(message, is_self=False, session_name=name)
        if reply:
            mark_message_direction(reply, is_self=True, session_name=name)
        # 如果有发送多媒体物料，也主动写进 session_name 的 is_self = True 缓存中，防御自回复误判
        if downloaded_paths:
            for p in downloaded_paths:
                p_lower = str(p).lower()
                if any(p_lower.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")):
                    mark_message_direction("[图片]", is_self=True, session_name=name)
                    mark_message_direction("图片", is_self=True, session_name=name)
                elif any(p_lower.endswith(ext) for ext in (".mp4", ".mov", ".avi", ".mkv", ".flv", ".3gp")):
                    mark_message_direction("[视频]", is_self=True, session_name=name)
                    mark_message_direction("视频", is_self=True, session_name=name)
                else:
                    mark_message_direction("[文件]", is_self=True, session_name=name)
                    mark_message_direction("文件", is_self=True, session_name=name)
        if is_live_record:
            mark_message_direction("[视频]", is_self=True, session_name=name)
            mark_message_direction("视频", is_self=True, session_name=name)
    except Exception as cache_ex:
        logger.debug(f"[工作流] 登记全局消息方向缓存失败: {cache_ex}")

    # 实时检测是否有未同步的官方微信标签，如果有则立刻触发物理打标
    if not engine._tag_syncing and not is_group and not identity_action:
        from src.utils.db_manager import WeChatDBManager
        identity_cfg = WeChatDBManager().get_identity_routing()
        if identity_cfg and identity_cfg.get("enabled"):
            target_wxid = wxid or user_name or name
            try:
                from src.utils.contacts_cache import contacts_cache
                all_friends = contacts_cache.get_friends(account_id)
                f_wxid = next((f.get("wxid", "") for f in all_friends if (f.get("name") or "").strip() == name.strip() or (f.get("remark") or "").strip() == name.strip()), "")
                if f_wxid:
                    target_wxid = f_wxid
            except Exception as cache_err:
                logger.debug(f"[工作流] 标签同步查询微信 ID 缓存异常: {cache_err}")

            from src.crm.account_data import get_account_settings
            if get_account_settings(account_id).get("reply", {}).get("auto_tag_enabled", True):
                need_sync = engine._profile_manager.get_tags_needing_sync(target_wxid, max_tags=3)
                has_unsynced_phone = False
                try:
                    profile = engine._profile_manager.get_profile(target_wxid)
                    if profile:
                        phone_val = next((t.value for t in profile.tags if t.subcategory == "phone"), None)
                        if phone_val and phone_val not in getattr(profile, "wx_synced_tags", []):
                            has_unsynced_phone = True
                except Exception:
                    pass

                if need_sync or has_unsynced_phone:
                    from src.utils.uia_task_runner import run_uia_with_timeout
                    await run_uia_with_timeout(engine.sync_tags_impl, 45.0, name, target_wxid, need_sync)

    if intent == "transfer_to_manual":
        # 1. 触发转人工报警通知 (微信、前端声音、外部告警)
        from .reply_notifier import send_transfer_to_manual_alert
        asyncio.create_task(send_transfer_to_manual_alert(engine, name, user_name, actual_message, account_id))
        
        # 2. 写入永久人工干预挂起，进行人工接管锁
        if not hasattr(engine, "_human_takeover_sessions"):
            engine._human_takeover_sessions = set()
        engine._human_takeover_sessions.add(name)
        engine._manual_interventions[name] = time.time()
        logger.info(f"[转人工] 已成功挂起好友 '{name}'，进入永久人工接管状态。")
    elif is_high_intent:
        from .reply_notifier import send_high_intent_alerts
        asyncio.create_task(send_high_intent_alerts(engine, name, user_name, actual_message, intent, account_id))

    if identity_action:
        await execute_post_reply_identity_action(engine, name, account_id, identity_action)

    warning_msg = None
    error_type = None
    if is_live_record:
        is_allowed_industry = False
        industry_name = "未知"
        try:
            from src.crm.industry_config.manager import IndustryConfigManager
            icm = IndustryConfigManager(account_id=account_id)
            profile = icm.get_active_profile()
            if profile:
                industry_name = getattr(profile, "name", "") or ""
                profile_id = getattr(profile, "id", "") or ""
                if profile_id == "sys_001" or "xm-bot4" in industry_name.lower():
                    is_allowed_industry = True
        except Exception as check_ex:
            logger.debug(f"[工作流] 录屏行业安全校验异常: {check_ex}")

        if is_allowed_industry:
            from .reply_media_helper import handle_live_record_action
            logger.info(f"[工作流] 开启实时录屏联动，正在录屏并发送给好友 '{name}'...")
            live_record_paths = []
            try:
                await handle_live_record_action(engine.driver, name, bus_used, live_record_paths)
            except Exception as live_err:
                logger.error(f"[工作流] 实时录屏联动执行异常: {live_err}")
            finally:
                if live_record_paths:
                    from .reply_helper import cleanup_temp_files
                    cleanup_temp_files(live_record_paths)
        else:
            logger.error(
                f"❌ [工作流录屏阻断] 收到实时录像动作，但当前行业为 '{industry_name}' (非 xm-bot4 系统演示场景)。"
                "由于在客服电脑上录制微信界面会泄露聊天记录与隐私，已自动终止实时录屏动作！\n"
                "💡 解决方案：请立即在【行业设置】中补全您的演示视频链接或本地物料路径 ('materials')，以便机器人直接向客户发送高清视频！"
            )
            warning_msg = "已自动终止桌面实时录屏，请在行业配置中补全 materials 视频物料"
            error_type = "materials_missing"

    msg_to_send = warning_msg if warning_msg else "自动回复消息成功触达好友"
    key = wxid or name

    # 构造具体的回复描述以方便用户在运行历史中追溯
    reply_detail = reply or ""
    
    # 提取原始物料路径（如 /api/xm-oss/... 或者是本地绝对路径）
    raw_files = []
    if file_to_send:
        if isinstance(file_to_send, list):
            raw_files = file_to_send
        elif isinstance(file_to_send, str) and file_to_send.strip():
            import json
            if file_to_send.startswith("[") or file_to_send.startswith("{"):
                try:
                    parsed = json.loads(file_to_send)
                    if isinstance(parsed, list):
                        raw_files = parsed
                    elif isinstance(parsed, dict):
                        raw_files = [parsed.get("path") or parsed.get("url") or file_to_send]
                except Exception:
                    pass
            if not raw_files:
                if ',' in file_to_send:
                    raw_files = [f.strip() for f in file_to_send.split(',') if f.strip()]
                elif ';' in file_to_send:
                    raw_files = [f.strip() for f in file_to_send.split(';') if f.strip()]
                else:
                    raw_files = [file_to_send.strip()]

    # 如果解析出了原始物料路径，优先使用原始物料路径，否则回退到 downloaded_paths 的文件名
    media_items = []
    if raw_files:
        media_items = [str(f) for f in raw_files if f]
    elif downloaded_paths:
        import os
        media_items = [os.path.basename(p) for p in downloaded_paths if p]

    if media_items:
        media_str = f"[发送物料: {', '.join(media_items)}]"
        reply_detail = f"{reply_detail} {media_str}".strip() if reply_detail else media_str

    await ws_manager.broadcast_task_update(
        task_id=f"auto_reply_{key}", task_type="自动回复", 
        status="completed", progress=100, total=100, 
        message=msg_to_send, friend_name=name, 
        friend_wxid=wxid,
        incoming_msg=actual_message, error_type=error_type,
        reply_msg=reply_detail
    )

    # 🌟 【滚动式长期记忆】每满 5 轮私聊后，后台异步提炼本次对话摘要写入 CRM
    # 群聊不触发（历史消息量大且 token 成本高）；异步线程执行，不影响主回复链路
    if not is_group and wxid and chat_round % 5 == 0:
        try:
            from src.crm.auto_analyser import trigger_async_auto_analyze
            trigger_async_auto_analyze(wxid, name, account_id)
            logger.debug(f"[长期记忆] 已触发好友 '{name}' 第 {chat_round} 轮后台摘要分析")
        except Exception as mem_ex:
            logger.debug(f"[长期记忆] 触发摘要分析异常: {mem_ex}")
