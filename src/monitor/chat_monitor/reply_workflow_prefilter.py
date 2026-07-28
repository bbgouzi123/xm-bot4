import logging
import re
import hashlib
import time
from typing import Any, Optional
from src.utils.websocket_manager import ws_manager
from .group_assistant import build_group_chat_assistant_prompt

logger = logging.getLogger(__name__)

def check_crowd_reply_pattern(context_msgs: list) -> Optional[str]:
    """群公告自动回执跟风判断"""
    if not context_msgs:
        return None

    at_all_idx = -1
    for i in range(len(context_msgs) - 1, -1, -1):
        msg = context_msgs[i]
        content = msg.get("content", "").strip()
        if msg.get("role") == "user":
            body = content
            colon_idx = content.find(':') if content.find(':') != -1 else content.find('：')
            if colon_idx != -1 and colon_idx < 35:
                body = content[colon_idx+1:].strip()
            body_clean = body.replace('\u2005', ' ').replace('\u200b', '').strip()
            if any(re.search(rf'@[\s\u2005]*{re.escape(t)}', body_clean, re.IGNORECASE) for t in ("所有人", "all", "All")):
                at_all_idx = i
                break

    target_replies = []
    receipt_keywords = ("收到", "1", "2", "已阅", "打卡", "吱", "👌", "👍", "收到1", "已签到", "好的")
    
    if at_all_idx != -1:
        for msg in context_msgs[at_all_idx + 1:]:
            content = msg.get("content", "").strip()
            if msg.get("role") == "user":
                body = content
                colon_idx = content.find(':') if content.find(':') != -1 else content.find('：')
                if colon_idx != -1 and colon_idx < 35:
                    body = content[colon_idx+1:].strip()
                body_clean = re.sub(r'[\s。，,！？!?~😊👌👍+]', '', body).strip()
                if body_clean and (body_clean.isdigit() or len(body_clean) <= 8):
                    if any(kw in body_clean for kw in receipt_keywords) or body_clean.isdigit() or len(body_clean) <= 6:
                        target_replies.append(body)
    else:
        for msg in reversed(context_msgs):
            if msg.get("role") == "user":
                body = msg.get("content", "").strip()
                colon_idx = body.find(':') if body.find(':') != -1 else body.find('：')
                if colon_idx != -1 and colon_idx < 35:
                    body = body[colon_idx+1:].strip()
                body_clean = re.sub(r'[\s。，,！？!?~😊👌👍+]', '', body).strip()
                if body_clean and (body_clean.isdigit() or any(kw in body_clean for kw in receipt_keywords) or len(body_clean) <= 6):
                    target_replies.append(body)
            elif msg.get("role") == "assistant":
                break

    if not target_replies:
        return None

    # 3. 统计最主流的回复内容
    from collections import Counter
    counts = Counter(target_replies)
    
    # 按照出现次数降序排列，取最频繁的那一个
    most_common = counts.most_common(1)
    if most_common:
        best_reply = most_common[0][0]
        logger.info(f"[跟风回复] 分析 @所有人 广播消息后的回复历史，检测到跟风候选: {counts}。决定跟随回复: '{best_reply}'")
        return best_reply

    return None


async def prefilter_group_message(engine: Any, name: str, message: str, is_group: bool, is_at: bool, wxid: str, task_id: str) -> bool:
    """
    对群聊消息进行免 @ 与关键词校验提前阻断。
    如果消息既不是群公告回执，也没有匹配任何静态关键词规则，返回 True 表示应该被静默过滤/忽略（不执行 UIA 点击）。
    否则返回 False，表示应继续后续回复流程。
    """
    if not (is_group and not is_at):
        return False

    # 🌟 [配置尊重] 如果全局/账号设置为"回复所有群消息"（at_only=False），则无论是否被@均放行，
    # 否则与评估器决策矛盾（评估器已放行，prefilter 不应再次拦截）
    try:
        from src.crm.account_data import get_account_settings
        _wxid = getattr(engine.driver, 'bot_wxid', None) or getattr(engine.driver, '_wxid', None) or 'default'
        _reply_cfg = get_account_settings(_wxid).get('reply', {})
        if not _reply_cfg.get('auto_chat_group_at_only', True):
            logger.info(f"[提前阻断] 群聊 '{name}' 已设置'回复所有消息'模式，跳过 @ 前置过滤")
            return False
    except Exception:
        return False  # 读取配置异常时保守放行，避免误拦截

    is_announcement = False
    last_msg_clean = message.replace('\u2005', ' ').replace('\u200b', '').strip()
    for all_tag in ("所有人", "all", "All"):
        pattern = re.compile(rf'@[\s\u2005]*{re.escape(all_tag)}', re.IGNORECASE)
        if pattern.search(last_msg_clean) or pattern.search(message):
            is_announcement = True
            break

    has_matched_kw = False
    try:
        from src.utils.db_manager import WeChatDBManager
        db = WeChatDBManager()
        rules = db.get_all_keyword_replies()
        for rule in rules:
            if not rule.get("is_active", True):
                continue
            scope = rule.get("scope", "all")
            if scope == "friend":
                continue
            keywords = rule.get("keywords", [])
            match_type = rule.get("match_type", "fuzzy")
            for kw in keywords:
                if not kw:
                    continue
                if match_type == "exact":
                    if message.strip() == kw.strip():
                        has_matched_kw = True
                        break
                    parts = [p.strip() for p in message.split("。") if p.strip()]
                    if any(p == kw.strip() for p in parts):
                        has_matched_kw = True
                        break
                else:  # fuzzy
                    if kw.strip() in message:
                        kw_strip = kw.strip()
                        if len(kw_strip) <= 4:
                            rest = message.replace(kw_strip, "")
                            rest_clean = re.sub(r'[\s，。？！；：,.\?!;:\(\)（）—\-\_\*]+', '', rest)
                            if len(rest_clean) > 0:
                                continue
                        has_matched_kw = True
                        break
            if has_matched_kw:
                break
    except Exception as kw_ex:
        logger.error(f"[提前阻断] 预检关键词异常: {kw_ex}")
        has_matched_kw = True

    if not is_announcement and not has_matched_kw:
        logger.info(f"[提前阻断] 群聊 {name} 消息未被 @ 且未匹配到任何关键词规则，直接跳过 UIA 物理界面操作。")
        keys_to_set = [name]
        if wxid and wxid != name:
            keys_to_set.append(wxid)
        for k in keys_to_set:
            engine._last_reply_time[k] = time.time()
            engine._fingerprints.setdefault(k, set()).add(hashlib.md5(f"{k}:{message}".encode()).hexdigest())
        
        await ws_manager.broadcast_task_update(
            task_id=task_id, 
            task_type="自动回复", 
            status="completed", 
            progress=100, 
            total=100, 
            message="群聊消息未@且未命中关键词规则，已自动忽略", 
            friend_name=name, 
            friend_wxid=wxid,
            is_group=True,
            incoming_msg=message
        )
        return True

    return False


def aggregate_consecutive_messages(context_msgs: list, actual_message: str) -> str:
    """
    连续多条消息聚合防漏回优化：
    在连续用户消息数量 >= 2 时，将最近 30 秒内的同一个人的连续用户消息聚合成单条消息。
    【群聊隔离修复】绝对禁止在群聊中将不同发送人的消息合并！
    """
    if not context_msgs:
        return actual_message

    from src.utils.chat_history import parse_time_to_ts
    consecutive_user_msgs = []
    last_user_time = None
    target_sender = context_msgs[-1].get("sender", "")
    
    for msg in reversed(context_msgs):
        if msg.get("role") == "user":
            # 🌟 群聊核心防护：如果发送人与最新消息发送人不同，立即截断，绝不合并！
            if msg.get("sender", "") != target_sender:
                break
            msg_time_str = msg.get("time", "")
            if last_user_time and msg_time_str:
                t1 = parse_time_to_ts(msg_time_str)
                t2 = parse_time_to_ts(last_user_time)
                # 超过 30 秒说明是不同时间段的不连贯消息，不再聚合
                if t1 > 0 and t2 > 0 and abs(t2 - t1) > 30.0:
                    break
            consecutive_user_msgs.insert(0, msg.get("content", "").strip())
            last_user_time = msg_time_str
        else:
            break

    # 只有连发 2 条及以上时才聚合
    if len(consecutive_user_msgs) >= 2:
        if consecutive_user_msgs and actual_message.strip() in consecutive_user_msgs[-1]:
            aggregated_msg = "。".join([m for m in consecutive_user_msgs if m])
            if aggregated_msg and aggregated_msg != actual_message:
                logger.info(f"[消息防抖聚合] 检测到发送人 '{target_sender}' 连发 {len(consecutive_user_msgs)} 条，已将原始消息融合为: '{aggregated_msg}'")
                return aggregated_msg
        else:
            logger.debug(f"[消息防抖聚合] 新消息与连续消息列表末尾不匹配，放弃聚合")
    return actual_message


async def resolve_reply_intent_and_routing(
    engine: Any, name: str, actual_message: str, user_name: str, account_id: str,
    context_msgs: list, chat_round: int, is_group: bool, wxid: str, is_at_all: bool,
    is_at: bool = False, is_physical_at: bool = True
) -> tuple[str, bool, Any, Any, Any, Any]:
    """解析回复意图与路由选择"""
    from .intent_router import determine_intent_and_routing

    intent = "casual_chat"
    is_high_intent = False
    fixed_reply = None
    ai_prompt = None
    file_to_send = None

    if is_group and is_at_all:
        crowd_reply = check_crowd_reply_pattern(context_msgs)
        if crowd_reply:
            logger.info(f"[跟风回复] 判定为 @所有人 广播消息，且检测到群内主流回执词为: '{crowd_reply}'。机器人将自动跟风回复该词。")
            intent = "群公告自动回执"
            fixed_reply = crowd_reply

    # 🌟 群聊助理模式拦截：群聊中被 @ 时，以助手身份基于群聊历史回答，绝对禁止走销售 Prompt
    if is_group and not fixed_reply:
        try:
            ai_prompt = build_group_chat_assistant_prompt(actual_message, user_name or name, name, context_msgs, is_at=is_at, is_physical_at=is_physical_at)
            logger.info(f"[群聊助理] 群 '{name}' 助理模式，历史 {len(context_msgs)} 条")
            return "群聊助理", False, None, ai_prompt, None, None
        except Exception as _ge:
            logger.error(f"[群聊助理] 构建 Prompt 异常，降级走销售路由: {_ge}")

    # 3.1 优先进行新友智能分流与拉群引导状态机拦截
    from .identity_flow import handle_identity_routing_flow
    routing_res = await handle_identity_routing_flow(engine, name, actual_message, is_group, account_id)
    identity_action = None
    if routing_res is not None:
        identity_action = routing_res.get("identity_action")
        if routing_res.get("reply") or routing_res.get("file_to_send"):
            fixed_reply = routing_res.get("reply", "")
            intent = "identity_routing"
            is_high_intent = False
            ai_prompt = ""
            file_to_send = routing_res.get("file_to_send", "")
        else:
            if not fixed_reply:
                intent, is_high_intent, fixed_reply, ai_prompt, file_to_send = await determine_intent_and_routing(
                    engine, name, actual_message, user_name, account_id, context_msgs, chat_round, is_group, wxid=wxid
                )
    else:
        if not fixed_reply:
            intent, is_high_intent, fixed_reply, ai_prompt, file_to_send = await determine_intent_and_routing(
                engine, name, actual_message, user_name, account_id, context_msgs, chat_round, is_group, wxid=wxid
            )

    return intent, is_high_intent, fixed_reply, ai_prompt, file_to_send, identity_action


async def resolve_image_vision_ocr(engine: Any, media_meta: dict, task_id: str, name: str, actual_message: str, uia_lock: Any) -> tuple[str, str]:
    """处理图片 Vision 多模态识别，返回 (actual_message, message)"""
    import os
    message = actual_message
    if media_meta and media_meta.get("media_type") == "image":
        _img_path = media_meta.get("media_path")
        if _img_path and os.path.isfile(_img_path):
            if hasattr(engine.ai_service, 'describe_image'):
                logger.info(f"[工作流] 检测到图片/表情包消息，正在进行 Vision 多模态识别: {_img_path}")
                uia_lock.update_status("正在使用 AI 多模态识别图片文字与画面...")
                await ws_manager.broadcast_task_update(
                    task_id=task_id, task_type="自动回复", status="running", progress=30, total=100,
                    message="检测到图片/表情包，正在使用 AI 多模态识别图片文字与画面...", friend_name=name, incoming_msg=actual_message
                )
                try:
                    vision_description = await engine.ai_service.describe_image(_img_path)
                    if vision_description:
                        logger.info(f"[工作流] Vision 识别图片结果: {vision_description}")
                        actual_message = f"[图片] 识别结果为：{vision_description}"
                        message = actual_message
                except Exception as desc_ex:
                    logger.error(f"[工作流] 调用 Vision 多模态识别异常: {desc_ex}")
    return actual_message, message



