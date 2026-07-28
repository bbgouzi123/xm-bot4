import asyncio, hashlib, logging, re, time
from typing import Any, Optional
from src.utils.websocket_manager import ws_manager
from src.crm.account_data import get_active_account

logger = logging.getLogger(__name__)
from .reply_workflow_stages import (
    check_reply_preconditions,
    process_incoming_multimedia,
    run_uia_history_recovery
)
from .group_mention_handler import resolve_group_mention_sender
from .intent_router import determine_intent_and_routing
from .reply_notifier import send_high_intent_alerts
from .reply_workflow_helpers import (
    handle_filehelper_commands,
    check_chat_injection,
    get_originally_hidden_state,
    restore_hidden_state,
    finalize_workflow_cleanup,
    extract_and_save_profile,
    check_self_reply_prevention,
    handle_reply_success_actions,
    _broadcast_skip
)
from .reply_media_helper import deduplicate_materials

# check_crowd_reply_pattern 已移至 reply_workflow_prefilter.py 以遵守 300 行有效代码限制


async def execute_reply_workflow(engine: Any, name: str, message: str, is_group: bool = False, user_name: str = '', is_at: bool = False, account_id: str = None, task_id: str = None, wxid: str = None, is_physical_at: bool = None):
    from src.utils.stop_signal import stop_signal
    stop_signal.reset()

    if not task_id:
        task_id = f"auto_reply_{wxid or name}"

    # 0. 拦截文件传输助使的远程控制与高危动作审批指令
    if await handle_filehelper_commands(engine, name, user_name, message):
        return

    # === 🌟 快速群聊白名单预检：在耗时的 UIA @mention 解析之前，提前拦截非白名单群聊 ===
    # 背景：_workflow_lock 是全局串行锁，一旦进入 resolve_group_mention_sender 最长可能阻塞 20 秒（UIA 物理操作），
    # 导致其它加白群聊/好友的消息也无法被处理。此预检纯走内存缓存，耗时 < 1ms，通过后才执行昂贵的 mention 解析。
    if is_group:
        from .reply_precheck import quick_group_whitelist_precheck
        if not quick_group_whitelist_precheck(engine, name, wxid):
            await _broadcast_skip(engine, task_id, name, wxid, message, "群聊不在白名单中，已跳过（快速预检拦截）")
            return

    # === Group Mention Sender & Context Correction ===
    is_at_all = False
    if is_physical_at is None:
        is_physical_at = is_at
    is_at_original = is_physical_at  # 🌟 修复：保留 WCDB 注入时判定的原始 is_physical_at，防止 UIA 物理解析失败后被错误重置为 False
    if is_group:
        account_id = getattr(engine.driver, 'bot_wxid', None) or getattr(engine.driver, '_wxid', None) or 'default'
        user_name, message, is_at_all = await resolve_group_mention_sender(
            engine, name, is_group, is_at, account_id, user_name, message, wxid=wxid
        )
        if is_at_all:
            is_at = True
        elif is_at_original:
            # 🌟 修复：UIA 物理解析未能找到 @气泡（消息被滚走/已过期），
            # 但入队时 WCDB/check_is_at_message 已明确判定为 @所有人，
            # 此时必须保留原来的 is_at=True，不能让物理解析失败来覆盖正确的语义判定。
            is_at = True
            logger.info(f"[工作流] 群聊 '{name}' UIA @提及物理解析未找到气泡，但入队时已确认 is_at=True，保留放行。")

        # 🌟 艾特所有人双重保障：若消息内容包含 @所有人/@all，强制标为 is_at_all = True，杜绝误艾特群公告发布者
        _check_body = (message or "").replace('\u2005', ' ').replace('\u200b', '')
        for _all_tag in ('所有人', 'all', 'All'):
            if re.search(rf'@[\s\u2005]*{re.escape(_all_tag)}', _check_body, re.IGNORECASE):
                is_at_all = True
                is_at = True
                break

        # 🌟 艾特热度衰减算法（暂态放免@）
        # 绑定当前群聊 (wxid or name) 与发送人群名片/昵称 (user_name)
        _group_key = wxid or name
        if is_group and _group_key and user_name:
            from .group_mention_decay import group_mention_decay_mgr
            if is_at:
                # 真实 @ 触发，记录/刷新该用户的热度监控
                group_mention_decay_mgr.record_at_dual(_group_key, user_name)
            else:
                # 非 @，检查是否仍在热度有效期内
                if group_mention_decay_mgr.check_and_update_heat_dual(_group_key, user_name, None, message):
                    is_at = True
                    logger.info(f"[热度衰减监控] 检测到群聊 '{name}' 中的好友 '{user_name}' 处于 @ 热度活跃期，自动提升 is_at=True 放行连续对话")


    # 1. 检查自动回复前置条件（含群聊/好友白黑名单二次兜底校验）
    should_reply, message = await check_reply_preconditions(engine, name, message, is_group=is_group, is_at=is_at, wxid=wxid)
    if not should_reply:
        # 兜底广播完成并记录指纹，彻底释放 5% 任务卡片
        await _broadcast_skip(engine, task_id, name, wxid, message, "未满足自动回复前置条件，已跳过")
        return

    # === 🌟 连续群聊免 @ 与关键词校验提前阻断优化 🌟 ===
    from .reply_workflow_prefilter import prefilter_group_message
    if await prefilter_group_message(engine, name, message, is_group, is_at, wxid, task_id):
        return

    from src.uia.input_guard import uia_lock
    lock_msg = f"收到消息: \"{message}\"，正在调用大模型生成智能回复文案中..."

    # 🌟 [修复 5% 卡死] 在等待全局 UIA 互斥锁之前，先广播"排队"状态，
    # 防止当其他会话正在使用 UIA 锁时，前端进度永远停在 5% 无更新。
    if uia_lock._active:
        await ws_manager.broadcast_task_update(
            task_id=task_id, task_type="自动回复", status="running",
            progress=10, total=100,
            message="AI 正在处理其他会话，当前请求已排队等待...",
            friend_name=name, friend_wxid=wxid, incoming_msg=message
        )

    async with uia_lock.async_guard(lock_msg, hwnd=getattr(engine.driver, 'hwnd', None)):
        # 🌟 成功获取到 UIA 锁，立刻更新前端进度，避免用户看到长时间 5% 或 10% 不动
        await ws_manager.broadcast_task_update(
            task_id=task_id, task_type="自动回复", status="running",
            progress=20, total=100,
            message="已进入物理回复流程，正在切换微信会话窗口...",
            friend_name=name, friend_wxid=wxid, incoming_msg=message
        )

        # 🚀 统一安全前置锁定与会话切换：
        # 只要判定需要回复，立刻强行置顶微信、切换至目标会话并锁定全局键鼠输入，防御中途插队或焦点抢占。
        from src.utils.uia_task_runner import run_uia_with_timeout
        chat_ok = await run_uia_with_timeout(
            engine.driver.ChatWith, 15.0, name, lock_input=True, foreground=True, msg_hint=message, wxid=wxid
        )
        if not chat_ok:
            logger.warning(f"[工作流] 统一前置会话物理切换失败，终止自动回复流程以防御错发")
            await ws_manager.broadcast_task_update(
                task_id=task_id, task_type="自动回复", status="error", progress=0, total=1,
                message="切换微信窗口锁定失败或页面名字不匹配", friend_name=name, friend_wxid=wxid, incoming_msg=message
            )
            return


        account_id = getattr(engine.driver, 'bot_wxid', None) or getattr(engine.driver, '_wxid', None) or 'default'

        # === 🌟 自动加入群聊处理器拦截 🌟 ===
        if not is_group:
            from .group_invite_handler import check_and_execute_group_invite
            if await check_and_execute_group_invite(engine, name, message, wxid, task_id):
                return

        # 2. 消息前置处理（处理多媒体、消息投递）
        actual_message, media_meta = await process_incoming_multimedia(engine, name, message, is_group, user_name, account_id, wxid=wxid)

        # 2.1 图片/表情包 Vision 智能内容识别与文字提取（OCR）
        from .reply_workflow_prefilter import resolve_image_vision_ocr
        actual_message, message = await resolve_image_vision_ocr(engine, media_meta, task_id, name, actual_message, uia_lock)

        # 3. 意图分类与画像标签解析
        from src.utils.chat_history import ChatHistoryManager
        history_mgr = ChatHistoryManager(account_id)
        context_msgs = history_mgr.get_context(wxid or name, window_size=20)
        context_msgs = await run_uia_history_recovery(engine, name, is_group, account_id, history_mgr, context_msgs, msg_hint=message, wxid=wxid)
        if context_msgs is None:
            logger.warning(f"[工作流] 会话 '{name}' 的微信窗口物理切换失败，终止回复以防御错发")
            await ws_manager.broadcast_task_update(
                task_id=task_id, 
                task_type="自动回复", 
                status="error", 
                progress=0, 
                total=1, 
                message="切换微信窗口校验未通过，输入框未显现或名字不匹配", 
                friend_name=name, 
                friend_wxid=wxid,
                incoming_msg=message
            )
            await finalize_workflow_cleanup(engine, name, False)
            return

        # 🌟 4. 长周期（2小时）群聊核心记忆提取与背景注入，彻底防御“水群冲刷”导致的 AI 遗忘
        from .long_term_memory import get_long_term_context_msgs
        context_msgs = await get_long_term_context_msgs(engine, name, wxid, is_group, account_id, context_msgs)

        # === 🌟 连续多条消息聚合防漏回优化 🌟 ===
        from .reply_workflow_prefilter import aggregate_consecutive_messages
        actual_message = aggregate_consecutive_messages(context_msgs, actual_message)
        message = actual_message  # 同时更新 message 变量，用于匹配插队拦截及后续指纹计算

        # 🌟 提取好友发送消息中的电话、地址等画像标签
        extract_and_save_profile(name, actual_message, account_id, engine._profile_manager, wxid=wxid)

        # === P1 级自回复终极防御拦截 ===
        if check_self_reply_prevention(name, message, context_msgs, engine, ws_manager, actual_message, wxid=wxid, is_group=is_group):
            await finalize_workflow_cleanup(engine, name, False)
            return

        chat_round = engine._chat_round_counter.get(name, 0) + 1
        engine._chat_round_counter[name] = chat_round

        # === 🌟 意图与路由解析 🌟 ===
        from .reply_workflow_prefilter import resolve_reply_intent_and_routing
        intent, is_high_intent, fixed_reply, ai_prompt, file_to_send, identity_action = await resolve_reply_intent_and_routing(
            engine, name, actual_message, user_name, account_id, context_msgs, chat_round, is_group, wxid, is_at_all, is_at=is_at, is_physical_at=is_at_original
        )

        # 3.2 群聊免 @ 规则校验：如果是在群聊中，且用户未被 @，且没有匹配到任何关键词规则
        # 则在此处静默丢弃该消息（即不回复），防止机器人在未被 @ 且没有命中业务关键词时随意调用 AI 回复导致骚扰和刷屏
        # 🌟 [最终保险] 如果消息本身含 @所有人，则强制放行，防止中间处理环节丢失 is_at 标志
        if is_group and not is_at and not fixed_reply:
            _msg_for_at_check = (actual_message or message or "").replace('\u2005', ' ').replace('\u200b', '')
            for _at_tag in ('所有人', 'all', 'All'):
                if re.search(rf'@[\s\u2005]*{re.escape(_at_tag)}', _msg_for_at_check, re.IGNORECASE):
                    logger.info(f"[工作流保险] 群聊 '{name}' 消息含 @所有人 但 is_at=False（中间环节丢失），强制放行避免误拦截")
                    is_at = True
                    break

        if is_group and not is_at and not fixed_reply:
            logger.info(f"[工作流] 群聊 {name} 消息未被 @ 且未匹配到任何关键词规则，自动跳过自动回复。")
            for k in {name, wxid} - {None, ""}:
                engine._last_reply_time[k] = time.time()
                engine._fingerprints.setdefault(k, set()).add(hashlib.md5(f"{k}:{message}".encode()).hexdigest())
            await ws_manager.broadcast_task_update(task_id=task_id, task_type="自动回复", status="completed", progress=100, total=100, message="群聊消息未@且未命中关键词规则，已自动忽略", friend_name=name, friend_wxid=wxid, is_group=True, incoming_msg=actual_message)
            return

        reply = None
        need_capture_screen = False

        if fixed_reply:
            reply = fixed_reply
            log_reply = reply
            if reply and (reply.strip().startswith("<msg") or reply.strip().startswith("<?xml") or "<appmsg" in reply or "<img" in reply):
                log_reply = "[微信多媒体 XML 话术消息]"
            print(f"[工作流] 固定话术 -> {name}: \"{log_reply}\" (0ms)")
            uia_lock.update_status("已匹配到固定快捷回复，准备发送...")
            await ws_manager.broadcast_task_update(task_id=task_id, task_type="自动回复", status="running", progress=50, total=100, message="已匹配到固定快捷回复，准备发送...", friend_name=name, friend_wxid=wxid, incoming_msg=actual_message)
        else:
            from src.crm.account_data import get_account_settings
            settings = get_account_settings(account_id)
            reply_mode = settings.get("reply", {}).get("reply_mode", "local")

            from .reply_workflow_agent import handle_agent_reply_mode, handle_local_ai_reply
            if reply_mode == "agent":
                ok, reply = await handle_agent_reply_mode(
                    engine, name, message, actual_message, is_group, user_name, account_id, context_msgs, wxid=wxid, media_meta=media_meta, is_at_all=is_at_all
                )
                if not ok:
                    return
            else:
                ok, reply, need_capture_screen = await handle_local_ai_reply(
                    engine, name, message, actual_message, is_group, user_name, account_id,
                    context_msgs, media_meta, ai_prompt, intent, file_to_send, wxid=wxid, is_at_all=is_at_all,
                    is_physical_at=is_at_original
                )
                if not ok:
                    return

        # 4. 富卡片多模态内容编译
        from .reply_workflow_helpers import compile_and_clean_reply
        ok, reply, compiler_media_paths = await compile_and_clean_reply(engine, name, message, reply, task_id, wxid, actual_message)
        if not ok:
            return

        # 5. 空闲等待检测
        uia_lock.update_status("回复已就绪，正在检测用户键鼠操作是否空闲...")
        await ws_manager.broadcast_task_update(task_id=task_id, task_type="自动回复", status="running", progress=70, total=100, message="回复已就绪，正在检测用户键鼠操作是否空闲...", friend_name=name, friend_wxid=wxid, incoming_msg=actual_message)

        from src.utils.user_activity import is_user_active
        _wait_rounds = 0
        while is_user_active(cooldown_ms=1200, check_caret=True) and _wait_rounds < 10:
            if stop_signal.is_stopped:
                break
            _wait_rounds += 1
            await asyncio.sleep(0.3)

        if stop_signal.is_stopped:
            logger.warning(f"[工作流] 发送消息前检测到停止信号，已终止自动回复流程。好友: {name}")
            return

        # 6. 最后的调度排队发送
        uia_lock.update_status("键盘鼠标空闲，正在将消息加入总线队列并调度发送...")
        await ws_manager.broadcast_task_update(task_id=task_id, task_type="自动回复", status="running", progress=90, total=100, message="键盘鼠标空闲，正在将消息加入总线队列并调度发送...", friend_name=name, friend_wxid=wxid, incoming_msg=actual_message)

        # === 🌟 启用会话工作流独占锁，确保多段发送和打标不被抢占 🌟 ===
        from src.orchestrator.ui_bus import ui_bus
        try:
            ui_bus.acquire_session_lock(account_id, name)
        except Exception as lock_err:
            logger.warning(f"[工作流] 启用 UIBus 会话独占锁失败: {lock_err}")

        try:
            # 物料下载与分段准备
            downloaded_paths = list(compiler_media_paths)

            # ✅ 安全防线：智能解析大模型承诺并物理下载去重/流控过滤最终要发送的物料，确保防风控与行为契合
            from .reply_media_helper import resolve_and_filter_workflow_materials
            is_live_record = await resolve_and_filter_workflow_materials(
                file_to_send, downloaded_paths, reply, account_id
            )

            reply_segments = [s.strip() for s in re.split(r'\n{2,}', reply) if s.strip()] if reply else []

            # 💡 意图识别直发物料避让机制：只有确认有实际物料或录屏任务，才清空口头文本，进入纯物料直发模式
            has_materials = bool(downloaded_paths) or is_live_record
            if has_materials:
                logger.info(f"[工作流] 检测到明确的物料/录屏发送承诺，已自动拦截口头确认文本，转为纯物料直发模式")
                reply_segments = []

            # 记录当前微信窗口是否本就处于后台/不可见/最小化状态，以便发送完后有始有终还原
            originally_hidden = get_originally_hidden_state(engine)

            # === 插队新消息拦截校验 ===
            if await check_chat_injection(engine, name, message, reply, actual_message, downloaded_paths):
                return

            from src.crm.account_data import get_account_settings
            settings = get_account_settings(account_id)
            reply_cfg = settings.get("reply", {})
            voice_enabled = reply_cfg.get("voice_reply_enabled", False)
            voice_id = reply_cfg.get("voice_reply_id", "")

            from .reply_voice_sender import dispatch_voice_reply_if_enabled
            voice_handled, success, error_msg = await dispatch_voice_reply_if_enabled(
                engine, name, reply_segments, downloaded_paths, voice_enabled, voice_id, is_group, wxid
            )
            bus_used = False
            if not voice_handled:
                from .reply_helper import dispatch_reply_messages
                success, bus_used, error_msg = await dispatch_reply_messages(
                    engine.driver, name, reply_segments, downloaded_paths, need_capture_screen, is_group, wxid=wxid, is_live_record=is_live_record
                )

            if downloaded_paths:
                from .reply_helper import cleanup_temp_files
                cleanup_temp_files(downloaded_paths)

            if success:
                # 记录自动回复成功日志，不再向消息通知中心发送高频冗余通知
                chat_type_str = "群聊" if is_group else "单聊"
                logger.info(f"[工作流] 自动回复成功 ({chat_type_str}): 对象={name}, 收到={actual_message[:50]}, 回复={reply[:50]}")

                await finalize_workflow_cleanup(engine, name, originally_hidden)
                await handle_reply_success_actions(
                    engine, name, user_name, is_group, actual_message, reply,
                    intent, account_id, chat_round, originally_hidden,
                    downloaded_paths, is_live_record, bus_used, identity_action,
                    history_mgr, message, is_high_intent, wxid=wxid,
                    file_to_send=file_to_send
                )
            else:
                from src.utils.uia_task_runner import report_uia_failure
                report_uia_failure(name)
                engine._stats["errors"] += 1
                logger.error(f"[工作流] 自动回复物理发送失败 ({name}): error={error_msg or '未知驱动错误'}")
                # 即使发送失败（如草稿拦截、客服避让等），也必须记录当前消息指纹，以防下一轮扫描立刻再次触发死循环
                try:
                    for k in {name, wxid} - {None, ""}:
                        engine._last_reply_time[k] = time.time()
                        engine._fingerprints.setdefault(k, set()).add(hashlib.md5(f"{k}:{message}".encode()).hexdigest())
                        if reply:
                            engine._fingerprints.setdefault(k, set()).add(hashlib.md5(f"{k}:{reply}".encode()).hexdigest())
                except Exception:
                    pass
                await finalize_workflow_cleanup(engine, name, originally_hidden)
                await ws_manager.broadcast_task_update(task_id=task_id, task_type="自动回复", status="error", progress=0, total=1, message=error_msg or "回复消息通过总线/UIA发送失败，请查看系统日志", friend_name=name, friend_wxid=wxid, incoming_msg=actual_message)
        finally:
            # === 🌟 释放会话工作流独占锁 🌟 ===
            try:
                ui_bus.release_session_lock(account_id, name)
            except Exception as lock_err:
                logger.warning(f"[工作流] 释放 UIBus 会话独占锁失败: {lock_err}")



from .identity_action import execute_post_reply_identity_action  # noqa: E402 — 保持向后兼容导出
