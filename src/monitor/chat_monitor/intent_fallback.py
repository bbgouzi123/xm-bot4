import logging
import re
import random
import json
from typing import Any, Tuple, Optional

logger = logging.getLogger(__name__)

def is_fixed_reply_duplicate(fixed_reply: str, context_msgs: list) -> bool:
    """
    检查新生成的固定回复是否与最近的历史回复重复，或者客户正在连续触发同类固定回复。
    如果是，返回 True 触发退避逻辑。
    """
    if not fixed_reply or not context_msgs:
        return False
        
    try:
        from src.ai import prompt_templates
        
        # 收集所有的固定回复模板/内容
        all_templates = set()
        # 问候回复
        for r in getattr(prompt_templates, 'GREETING_REPLIES', []):
            all_templates.add(r.strip())
        # 负面情绪回复
        for r in getattr(prompt_templates, 'NEGATIVE_REPLIES', []):
            all_templates.add(r.strip())
        # 价格兜底回复
        all_templates.add('价格方面，我让专人给你详细报个方案吧～你方便说下你的具体需求吗？这样我好给你推荐最合适的 😊')
        # 转人工回复
        all_templates.add("好的，已为您呼叫人工客服，他会尽快为您处理，请稍等哈 😊")
        # 资料整理回复
        for r in getattr(prompt_templates, 'MATERIAL_REPLIES', []):
            all_templates.add(r.strip())

        # 规范化清理以便进行比较
        def _normalize(s: str) -> str:
            return re.sub(r'[\s。，,！？!?\n\r~😊]', '', s).strip()

        normalized_templates = {_normalize(t) for t in all_templates if t}
        normalized_fixed = _normalize(fixed_reply)
        
        # 我们只关注最近 of 3 条消息中属于 assistant 的回复
        assistant_replies = [m.get("content", "").strip() for m in context_msgs if isinstance(m, dict) and m.get("role") == "assistant"]
        
        # 检查最后一次我方回复
        if assistant_replies:
            last_reply = assistant_replies[-1]
            normalized_last = _normalize(last_reply)
            
            # 情况一：如果当前的固定回复和最后一次发送的回复内容极其相似
            if normalized_last == normalized_fixed:
                return True
                
            # 情况二：如果最后一次发送的回复本身就是一个固定模板，且当前的回复也是一个固定模板
            # （说明我们在连续触发各种固定模板，用户体验极差，应该放行给 LLM 处理以增加真实感和灵活度）
            if normalized_last in normalized_templates and normalized_fixed in normalized_templates:
                return True
                
        # 情况三：如果在最近的 2 次回复中已经包含了当前要发的回复，也算重复
        for prev_reply in assistant_replies[-2:]:
            if _normalize(prev_reply) == normalized_fixed:
                return True
    except Exception as ex:
        logger.error(f"[智能退避] 校验重复异常: {ex}")
            
    return False


async def apply_fallback_routing(
    intent: str,
    actual_message: str,
    fixed_reply: Optional[str],
    ai_prompt: Optional[str],
    file_to_send: Optional[str],
    context_msgs: list,
    chat_round: int,
    name: str,
    account_id: str,
    industry_profile: Any,
    wxid: Optional[str] = None
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    智能退避拦截：如果检测到连续重复触发固定回复模板，则强制降级/放行至 LLM 进行个性化回复。
    """
    if fixed_reply and is_fixed_reply_duplicate(fixed_reply, context_msgs):
        logger.info(f"[智能退避] 检测到即将发送的固定回复 '{fixed_reply}' 与历史回复冲突或处于连续规则中，自动降级/放行给大模型高情商对话。")
        fixed_reply = None
        
        if not ai_prompt:
            # 获取 profile_tags_str & sdr_context_str，与 PromptRouter 内部逻辑保持一致
            profile_tags_str = "暂无"
            session_key = wxid or name
            if session_key:
                try:
                    from src.crm.profile_manager import ProfileManager
                    prof = ProfileManager(account_id=account_id).get_profile(session_key)
                    if prof and prof.tags:
                        profile_tags_str = ", ".join(f"[{t.subcategory}]{t.value}" for t in prof.tags)
                except Exception as e:
                    logger.error(f"[PromptRouter-退避] 获取画像标签失败: {e}")

            sdr_context_str = "未启用自动跟单"
            if session_key:
                try:
                    from src.utils.db_manager import WeChatDBManager
                    db = WeChatDBManager()
                    for task in db.get_auto_follow_tasks():
                        if task.get("status") == "active" and session_key in (task.get("targets") or []):
                            t_state = (task.get("execution_state") or {}).get(session_key) or {}
                            follow_count = t_state.get("follow_count", 0)
                            follow_days = task.get("follow_days", 7)
                            sdr_context_str = f"已挂载活跃自动跟单任务(任务ID:{task.get('task_id')})，当前处于第 {follow_count}/{follow_days} 天的触达阶段"
                            break
                except Exception as e:
                    logger.error(f"[PromptRouter-退避] 获取 SDR 跟单状态失败: {e}")

            from src.ai.prompt_builder_helpers import build_casual_prompt, build_business_prompt
            allow_emoji = random.random() < 0.05
            # 根据意图进行相应的 Prompt 构造
            if intent in ('greeting', 'negative', 'casual_chat'):
                ai_prompt = build_casual_prompt(
                    message=actual_message, industry_config=industry_profile,
                    history_messages=context_msgs, allow_emoji=allow_emoji,
                    profile_tags=profile_tags_str, sdr_context=sdr_context_str
                )
            else:
                ai_prompt = build_business_prompt(
                    message=actual_message, industry_config=industry_profile,
                    chat_round=chat_round, history_messages=context_msgs,
                    allow_emoji=allow_emoji, profile_tags=profile_tags_str,
                    sdr_context=sdr_context_str
                )
    return fixed_reply, ai_prompt, file_to_send


async def check_group_announcement_receipt(
    engine: Any,
    name: str,
    actual_message: str,
    user_name: str,
    account_id: str,
    is_group: bool,
    reply_cfg: dict,
    configs: dict
) -> Optional[str]:
    """
    群公告自动回执研判，如果是回执消息且确认需要回复，返回应答内容；否则返回 None。
    """
    auto_receipt_enabled = reply_cfg.get("auto_receipt_enabled", configs.get("auto_receipt_enabled", False))
    custom_receipt_keywords = reply_cfg.get("custom_receipt_keywords", configs.get("custom_receipt_keywords", []))
    
    if not (is_group and auto_receipt_enabled):
        return None

    last_msg_clean = actual_message.replace('\u2005', ' ').replace('\u200b', '').strip()
    is_announcement = False
    for all_tag in ("所有人", "all", "All"):
        pattern = re.compile(rf'@[\s\u2005]*{re.escape(all_tag)}', re.IGNORECASE)
        if pattern.search(last_msg_clean) or pattern.search(actual_message):
            is_announcement = True
            break
            
    if not is_announcement:
        return None

    matched_kw = False
    for kw in ("回", "扣", "答", "签", "吱", "阅", "1", "2", "打卡"):
        if kw in actual_message:
            matched_kw = True
            break
    if not matched_kw and custom_receipt_keywords:
        for kw in custom_receipt_keywords:
            if kw.strip() and kw.strip() in actual_message:
                matched_kw = True
                break
                
    if not matched_kw:
        return None

    logger.info(f"[自动回执] 检测到群聊 '{name}' 的消息符合回执过滤漏斗。启动 AI 研判...")
    receipt_prompt = (
        f"【系统研判指令】\n"
        f"你是一个微信群签到回执智能提取助手。请阅读群主/管理员发送的群通知，研判是否要求群成员进行回复确认，并提取出要求回复的字符。\n"
        f"群公告消息内容：\n"
        f"\"\"\"\n{actual_message}\n\"\"\"\n\n"
        f"判定原则：\n"
        f"1. 如果群公告中明确要求成员需要以特定形式进行回复（例如：“收到请扣1”、“看到请回复收到”、“收到吱一声”、“看到的打卡”、“回：收到”等），need_reply 应设为 true。\n"
        f"2. 如果群公告明确说明不用回复，或没有包含任何关于要求成员回复确认的含义，need_reply 应设为 false。\n"
        f"3. 如果 need_reply 为 true，提取出最希望回复的那个字符或词组。例如：“扣1”提取出 \"1\"；“回复收到”提取出 \"收到\"；“吱一声”提取出 \"吱\"；“回复已阅”提取出 \"已阅\"。\n\n"
        f"请忽略你原先的角色和口吻设定，必须输出且仅输出以下 JSON 格式（严禁包含 markdown 标记或任何前导/后置解释性文字）：\n"
        f"{{\n"
        f"  \"need_reply\": true/false,\n"
        f"  \"reply_content\": \"回复内容\"\n"
        f"}}"
    )
    
    try:
        chat_agent_id = ""
        if hasattr(engine.ai_service, 'get_agent_id_for_role'):
            chat_agent_id = engine.ai_service.get_agent_id_for_role("chat")
        
        result = await engine.ai_service.start_chat(
            agent_id=chat_agent_id, message=receipt_prompt, session_id=f"receipt_{name}",
            user_name=user_name or name, session_name=name, account_id=account_id,
            cache_session=False, history_messages=[]
        )
        
        if result.get("success") and result.get("content"):
            raw_content = result["content"].strip()
            logger.info(f"[自动回执] AI 研判原始返回: {raw_content}")
            
            if raw_content.startswith("```json"):
                raw_content = raw_content.replace("```json", "", 1)
            if raw_content.startswith("```"):
                raw_content = raw_content.replace("```", "", 1)
            if raw_content.endswith("```"):
                raw_content = raw_content[:-3]
            raw_content = raw_content.strip()
            
            try:
                resp_data = json.loads(raw_content)
                if resp_data.get("need_reply"):
                    reply_text = resp_data.get("reply_content", "").strip()
                    if reply_text:
                        logger.info(f"[自动回执] 研判命中且提取到应答词: '{reply_text}'")
                        return reply_text
                    else:
                        logger.info("[自动回执] AI 研判为需要回复，但提取出的回执词为空。")
                else:
                    logger.info("[自动回执] AI 研判该公告无需回复。")
            except Exception as json_ex:
                logger.error(f"[自动回执] 解析 AI 返回 of JSON 异常: {json_ex}, 原始文本: {raw_content}")
    except Exception as ai_ex:
        logger.error(f"[自动回执] AI 研判请求发送失败: {ai_ex}")
        
    return None
