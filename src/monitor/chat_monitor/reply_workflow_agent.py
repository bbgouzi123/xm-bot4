import time
import hashlib
import logging
import re
import os
from typing import Any, Optional
from src.utils.websocket_manager import ws_manager

logger = logging.getLogger(__name__)

def _add_fingerprint(engine, name: str, wxid: Optional[str], fp: str):
    keys = [name]
    if wxid and wxid != name:
        keys.append(wxid)
    for k in keys:
        engine._fingerprints.setdefault(k, set()).add(fp)


async def handle_agent_reply_mode(
    engine: Any, name: str, message: str, actual_message: str,
    is_group: bool, user_name: str, account_id: str, context_msgs: list,
    wxid: str = None, media_meta: Any = None, is_at_all: bool = False
) -> tuple[bool, Optional[str]]:
    """处理外部 Agent 回调模式的自动回复，返回 (should_continue_workflow, reply_content)"""
    task_id = f"auto_reply_{wxid or name}"
    await ws_manager.broadcast_task_update(
        task_id=task_id, task_type="自动回复", status="running", progress=40, total=100,
        message="已挂起，正在等待外部 AI 智能体回调处理中...", friend_name=name, incoming_msg=actual_message
    )
    from src.uia.input_guard import uia_lock
    uia_lock.update_status("已挂起，正在等待外部 AI 智能体回调处理中...")

    from src.task.agent_reply_waiter import agent_reply_waiter
    agent_reply_waiter.register(task_id, name)

    # 广播请求给外部 Agent 智能体
    context = [
        {"sender": m[0], "content": m[1]}
        for m in context_msgs[-10:]
    ] if context_msgs else []

    payload = {
        "type": "agent_reply_request",
        "taskId": task_id,
        "accountId": account_id,
        "sender": user_name or name,
        "content": actual_message,
        "sessionName": name,
        "isGroup": is_group,
        "isAtAll": is_at_all,
        "messages": [m[1] for m in context_msgs] if context_msgs else [],
        "context": context,
        "mediaMeta": media_meta,
        "timestamp": int(time.time() * 1000),
        "timeoutAt": int((time.time() + 60) * 1000)
    }
    await ws_manager.broadcast(payload)
    logger.info(f"[Agent 模式] 任务 {task_id} 已推送，会话: {name}，等待回调")

    # 挂起阻塞，等待外部智能体结果，超时设为 60 秒
    agent_result = await agent_reply_waiter.wait_result(task_id, timeout=60.0)

    if not agent_result:
        engine._stats["errors"] = engine._stats.get("errors", 0) + 1
        try:
            fp = hashlib.md5(f"{name}:{message}".encode()).hexdigest()
            _add_fingerprint(engine, name, wxid, fp)
        except Exception:
            pass
        await ws_manager.broadcast_task_update(
            task_id=task_id, task_type="自动回复", status="error", progress=0, total=1,
            message="外部智能体处理超时或失效，已取消回复", friend_name=name, incoming_msg=actual_message
        )
        return False, None

    action = agent_result.get("action")
    if action == "reply":
        return True, agent_result.get("reply")
    elif action == "no_reply":
        try:
            fp = hashlib.md5(f"{name}:{message}".encode()).hexdigest()
            _add_fingerprint(engine, name, wxid, fp)
        except Exception:
            pass
        await ws_manager.broadcast_task_update(
            task_id=task_id, task_type="自动回复", status="completed", progress=100, total=100,
            message="外部智能体决策：不予回复", friend_name=name, incoming_msg=actual_message
        )
        return False, None
    elif action == "defer":
        try:
            fp = hashlib.md5(f"{name}:{message}".encode()).hexdigest()
            _add_fingerprint(engine, name, wxid, fp)
        except Exception:
            pass
        await ws_manager.broadcast_task_update(
            task_id=task_id, task_type="自动回复", status="completed", progress=100, total=100,
            message="外部智能体决策：已转为人工客服跟进", friend_name=name, incoming_msg=actual_message
        )
        try:
            engine.send_message(name, "💬 [系统提示] 客服正在接入，请稍候...")
        except Exception:
            pass
        return False, None

    return False, None

async def handle_local_ai_reply(
    engine: Any, name: str, message: str, actual_message: str,
    is_group: bool, user_name: str, account_id: str, context_msgs: list,
    media_meta: Any, ai_prompt: str, intent: str, file_to_send: str,
    wxid: str = None, is_at_all: bool = False, is_physical_at: bool = True
) -> tuple[bool, Optional[str], bool]:
    """处理本地/大模型直接交互模式的自动回复，返回 (should_continue_workflow, reply_content, need_capture_screen)"""
    task_id = f"auto_reply_{wxid or name}"
    chat_agent_id = ""
    if hasattr(engine.ai_service, 'get_agent_id_for_role'):
        chat_agent_id = engine.ai_service.get_agent_id_for_role("chat")

    # 动态智能体路由逻辑（千人千面）
    chat_agent_id = match_dynamic_agent_route(engine, name, account_id, is_group, chat_agent_id)

    # 3. 广播大模型处理状态并开始聊天生成
    await ws_manager.broadcast_task_update(
        task_id=task_id, task_type="自动回复", status="running", progress=40, total=100,
        message="正在调用大模型生成智能回复文案中...", friend_name=name, incoming_msg=actual_message
    )
    from src.uia.input_guard import uia_lock
    uia_lock.update_status("正在调用大模型生成智能回复文案中...")

    # 3.1 图片多模态处理：检测图片路径 → 上传 → 获取 file_ids
    uploaded_file_ids = []
    if media_meta and media_meta.get("media_type") == "image":
        _img_path = media_meta.get("media_path")
        if _img_path and os.path.isfile(_img_path):
            logger.info(f"[工作流] 检测到图片消息，正在上传到 AI 平台: {_img_path}")
            try:
                upload_result = await engine.ai_service.upload_file(_img_path)
                if upload_result.get("success") and upload_result.get("file_id"):
                    uploaded_file_ids.append(upload_result["file_id"])
                    logger.info(f"[工作流] 图片上传成功: file_id={upload_result['file_id']}")
                else:
                    logger.warning(f"[工作流] 图片上传失败: {upload_result.get('error', '未知错误')}")
            except Exception as upload_err:
                logger.error(f"[工作流] 图片上传异常: {upload_err}")

    # 🌟 过滤无有效关联数据的纯媒体占位符消息，防止大模型弱智回复
    # 注意：如果 media_meta 已有 media_path（数据库解密导出的图片）或 Vision OCR 已识别图片内容
    # （actual_message 中已包含 "[图片] 识别结果为：" 字样），则不应该被跳过
    _has_media_path = bool(media_meta and media_meta.get("media_path") and os.path.isfile(media_meta.get("media_path", "")))
    _vision_ocr_done = "[图片] 识别结果为：" in actual_message
    is_pure_media_placeholder = actual_message.strip() in ("[图片]", "图片", "[视频]", "视频", "[文件]", "文件")
    if is_pure_media_placeholder and not uploaded_file_ids and not _has_media_path and not _vision_ocr_done:
        logger.info(f"[工作流] 会话 {name} 最新消息为纯媒体占位符 {actual_message} 且无有效多模态关联数据，主动跳过回复以避让。")
        for k in {name, wxid} - {None, ""}:
            engine._last_reply_time[k] = time.time()
        try:
            fp = hashlib.md5(f"{name}:{message}".encode()).hexdigest()
            _add_fingerprint(engine, name, wxid, fp)
        except Exception:
            pass
        await ws_manager.broadcast_task_update(
            task_id=task_id,
            task_type="自动回复",
            status="completed",
            progress=100,
            total=100,
            message=f"纯媒体消息 {actual_message} 且无多模态关联数据，已自动忽略",
            friend_name=name,
            incoming_msg=actual_message
        )
        return False, None, False

    # P1 修复：ai_prompt 为空时（意图路由未生成 prompt 或模板渲染失败），
    # 直接用原始消息作为兜底 prompt，避免向大模型发送空内容导致无回复/400 错误。
    effective_prompt = ai_prompt or actual_message
    if not ai_prompt:
        if actual_message.strip() in ("[图片]", "图片") and uploaded_file_ids:
            # 图片已成功上传并包含在请求里，请求 AI 进行图片内容分析并作为专业销售顾问回答
            effective_prompt = "[Vision识图] 用户发送了一张图片，请仔细分析图片内容，结合你的角色身份和业务背景进行高情商应答客户。"
        elif actual_message.strip() in ("[图片]", "图片") and _has_media_path and not uploaded_file_ids:
            # 图片已提取但上传失败，给 AI 一个友好的 fallback 引导
            effective_prompt = "[图片分析]用户发来了一张图片（系统将图片上传失败，暂无法查看图片内容）。请友好地询问对方发的是什么图片或要咨询什么问题，不要装作自己看到了图片。"
        elif _vision_ocr_done:
            # Vision OCR 已识别，使用包含识别结果的 actual_message 作为 prompt
            effective_prompt = actual_message
        logger.warning(f"[工作流] ai_prompt 为空（意图: {intent}），已使用图片感知兼容 prompt，好友: {name}")

    # 注入长期画像（已知客户画像备忘录）
    try:
        from src.crm.auto_analyser import inject_profile_memory
        effective_prompt = inject_profile_memory(wxid or name, effective_prompt, account_id)
    except Exception as e:
        logger.debug(f"[工作流] 画像注入处理异常: {e}")

    # 🌟 注入当前日期时间与会话时间感知上下文，彻底解决大模型由于缺失时间刻度导致 Perpetually Tomorrow (永远在聊明天) 的时空迷失问题
    try:
        import datetime
        now = datetime.datetime.now()
        weekday_cn = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][now.weekday()]
        time_context = (
            f"[当前系统时间]：{now.strftime('%Y-%m-%d %H:%M:%S')} ({weekday_cn})\n"
            f"重要指引：当前对话发生在此时间。请结合历史消息中的具体时间戳（每条消息已以 [YYYY-MM-DD HH:MM:SS] 标明），"
            f"精准判断历史约定的“明天”或“今天”是否已是过去。绝对不可盲目沿用历史消息中的旧相对时间约定。\n\n"
        )
        effective_prompt = time_context + effective_prompt
    except Exception as time_err:
        logger.debug(f"[工作流] 时间上下文注入异常: {time_err}")


    # 动态抓取并格式化前序微信聊天历史（包含 role 与 content 映射），自动过滤与当前消息重复的末尾记录
    history_list = []
    if context_msgs:
        for m in context_msgs:
            content_val = m.get("content", "")
            # 🌟 [群聊说话人显式注入] 
            # 如果是群聊且含有具体的发言人，则在内容开头显式注入发言者昵称，方便 LLM 准确厘清对话上下文
            if is_group and m.get("sender"):
                sender = m.get("sender")
                content_val = f"[{sender}]: {content_val}"
            history_list.append({
                "role": m.get("role", "user"),
                "content": content_val,
                "time": m.get("time", "")  # 暂存时间用于后续时间戳注入
            })
        
        # 🔧 核心修复：更鲁棒的消息内容归一化比较，忽略空格/句号/换行等标点符号，防止合并前后字符集差异（。 vs \n）导致过滤失败
        def _normalize(s: str) -> str:
            return re.sub(r'[\s。，,！？!?\n\r]', '', s).strip()

        if history_list and _normalize(history_list[-1]["content"]) == _normalize(actual_message):
            history_list.pop()

        # 🌟 对齐归一化校验后，将时间戳安全注入到 history 消息的 content 头部
        for msg in history_list:
            m_time = msg.pop("time", "")
            if m_time:
                m_time_clean = m_time.replace('T', ' ').replace('Z', '').split('.')[0].strip()
                msg["content"] = f"[{m_time_clean}] {msg['content']}"

    # 🌟 严密调试日志：记录发往 AI 的完整 Prompt、智能体 ID 以及前文上下文
    import json
    logger.info(
        f"[AI Prompt Debug] === 发送给 AI 的原始请求 ===\n"
        f"好友: {name} | 意图: {intent} | 智能体 ID: {chat_agent_id}\n"
        f"--- 历史消息上下文 ({len(history_list)} 条) ---\n"
        f"{json.dumps(history_list, ensure_ascii=False, indent=2)}\n"
        f"--- 最终组装 of Prompt ---\n"
        f"{effective_prompt}\n"
        f"========================================="
    )

    from src.utils.stop_signal import stop_signal
    if stop_signal.is_stopped:
        logger.warning(f"[工作流] AI 聊天启动前检测到停止信号，已终止自动回复流程。好友: {name}")
        return False, None, False

    result = await engine.ai_service.start_chat(
        agent_id=chat_agent_id, message=effective_prompt, session_id=f"chat_{name}",
        user_name=user_name or name, session_name=name, account_id=account_id,
        # 🌟 强制禁用 conv_id 云端缓存，否则 Coze 不会把本地历史带入 additional_messages，
        # 导致大模型完全失忆，套用默认人设乱回复。因此私聊也强制禁用云端会话缓存，以本地 WeChat 历史为准。
        cache_session=False,
        history_messages=history_list,
        file_ids=uploaded_file_ids if uploaded_file_ids else None,
    )

    if stop_signal.is_stopped:
        logger.warning(f"[工作流] AI 聊天结束后检测到停止信号，已终止自动回复流程。好友: {name}")
        return False, None, False

    if not result.get('success') or not result.get('content'):
        engine._stats["errors"] = engine._stats.get("errors", 0) + 1
        # 将底层错误原因一并暴露，方便用户自查（如 Token 失效、额度耗尽、URL 配置错误等）
        raw_err = result.get('error', '') if result else '调用超时或服务异常'
        err_hint = f"大模型回复生成失败：{raw_err}" if raw_err else "大模型回复生成失败，请检查模型配置"
        logger.error(f"[工作流] AI 生成失败 | 好友={name} | 原因={raw_err}")
        try:
            fp = hashlib.md5(f"{name}:{message}".encode()).hexdigest()
            _add_fingerprint(engine, name, wxid, fp)
        except Exception:
            pass
        await ws_manager.broadcast_task_update(
            task_id=task_id, task_type="自动回复", status="error", progress=0, total=1,
            message=err_hint, friend_name=name, incoming_msg=actual_message
        )
        return False, None, False

    raw_reply = result['content']
    need_capture_screen = False
    if re.search(r'<Action_CaptureScreen', raw_reply, re.IGNORECASE):
        need_capture_screen = True

    from .reply_helper import parse_and_process_ai_reply
    from .promise_helper import register_promise_tasks_from_reply
    reply, tags_count = parse_and_process_ai_reply(
        raw_reply, name, user_name, account_id, is_group, engine._profile_manager,
        is_at_all=is_at_all, is_physical_at=is_physical_at
    )
    if tags_count > 0:
        engine._stats["tags_extracted"] = engine._stats.get("tags_extracted", 0) + tags_count
    
    register_promise_tasks_from_reply(name, user_name, account_id, reply, file_to_send)
    return True, reply, need_capture_screen


def match_dynamic_agent_route(engine: Any, name: str, account_id: str, is_group: bool, chat_agent_id: str) -> str:
    """动态智能体路由匹配逻辑（千人千面），如果匹配成功返回匹配到的 agent_id，否则返回传入的 chat_agent_id"""
    try:
        from src.api.instance_settings_api import load_instance_settings
        from src.crm.industry_config.manager import IndustryConfigManager
        inst_settings = load_instance_settings(account_id)
        inst_profile_id = inst_settings.get("industry_profile_id", "")
        
        global_icm = IndustryConfigManager(account_id="global")
        industry_profile = None
        if inst_profile_id:
            industry_profile = global_icm.get_profile_by_id(inst_profile_id)
        
        if not industry_profile:
            industry_profile = global_icm.get_active_profile()
        if industry_profile and hasattr(industry_profile, 'agent_routes'):
            routes = industry_profile.agent_routes
            matched_agent_id = None

            if is_group:
                group_routes = routes.get("groups", []) if isinstance(routes, dict) else []
                for route in group_routes:
                    if route.get("group_name") == name:
                        matched_agent_id = route.get("agent_id")
                        logger.info(f"[智能路由] 群聊名 '{name}' 成功匹配到专属 AI 智能体: {matched_agent_id}")
                        break
            else:
                tag_routes = routes.get("tags", []) if isinstance(routes, dict) else []
                profile = engine._profile_manager.get_profile(name)
                if profile:
                    tags_set = set(profile.wx_synced_tags) if hasattr(profile, 'wx_synced_tags') else set()
                    if hasattr(profile, 'tags') and profile.tags:
                        for t in profile.tags:
                            if hasattr(t, 'value') and t.value:
                                tags_set.add(t.value)

                    for route in tag_routes:
                        target_tag = route.get("tag")
                        if target_tag in tags_set:
                            matched_agent_id = route.get("agent_id")
                            logger.info(f"[智能路由] 好友 '{name}' 标签 '{target_tag}' 成功匹配到专属 AI 智能体: {matched_agent_id}")
                            break

            if matched_agent_id:
                return matched_agent_id
    except Exception as route_ex:
        logger.error(f"[智能路由] 动态路由匹配发生异常: {route_ex}", exc_info=True)
    return chat_agent_id
