"""
CRM 客户画像自动分析与长期记忆注入器 (Long-Term Memory Auto Analyser)
"""
import re
import json
import logging
import threading
import asyncio
from src.crm.profile_manager import ProfileManager
from src.crm.tag_manager import TagEntry

logger = logging.getLogger(__name__)


def inject_profile_memory(wxid: str, prompt: str, account_id: str) -> str:
    """在 AI 请求 prompt 中注入已知的长期画像备忘录，实现长效记忆的通用拼接"""
    if not wxid or wxid.endswith("@chatroom"):
        return prompt
    try:
        pm = ProfileManager(account_id=account_id)
        profile = pm.get_profile(wxid)
        if profile and profile.conversation_summary:
            summary = profile.conversation_summary.strip()
            if summary:
                logger.info(f"[CRM记忆注入] 成功为客户 '{wxid}' 注入画像摘要")
                return f"[已知该客户画像备忘录]：{summary}\n[当前用户消息]：{prompt}"
    except Exception as e:
        logger.debug(f"[CRM记忆注入] 忽略画像注入异常: {e}")
    return prompt


def trigger_async_auto_analyze(wxid: str, nickname: str, account_id: str):
    """异步触发 AI 后台提取画像摘要与标签"""
    if not wxid or wxid.endswith("@chatroom"):
        return

    def _run_sync_analyze():
        try:
            import app.state as app_state
            if not app_state.ai_service or not app_state.ai_service.is_configured():
                return

            from src.utils.chat_history import ChatHistoryManager
            history_mgr = ChatHistoryManager(account_id)
            # 获取最近 30 条对话上下文
            messages = history_mgr.get_context(wxid, window_size=30, max_chars=5000)
            if not messages or len(messages) < 3:
                return

            formatted_chat = ""
            for msg in messages:
                role_name = "客户" if msg["role"] == "user" else "我方"
                formatted_chat += f"{role_name}: {msg['content']}\n"

            system_prompt = (
                "你是一个专业的客户关系管理(CRM)助手。请分析以下给出的微信聊天记录上下文，并以 JSON 格式输出该客户的画像洞察。\n"
                "输出的 JSON 必须严格包含以下字段，键名必须完全相同：\n"
                "1. \"summary\": 1-2句话简明扼要地总结该客户最新的核心诉求、购买意愿以及当前跟进状态。\n"
                "2. \"intent_level\": 客户意向等级，必须且只能为以下三个字母之一：\n"
                "   - \"A\": 意向强烈（明确有购买/付费/合作意愿、咨询下单支付方式）\n"
                "   - \"B\": 普通咨询（对产品功能、方案表现出兴趣，正在了解和对比中）\n"
                "   - \"C\": 意向微弱或无意向（闲聊、打招呼、已拒绝、仅加好友无实质对话）\n"
                "3. \"tags\": 数组类型，包含1到5个最能代表该客户特性的短标签（例如：[\"关注价格\", \"咨询bot4\", \"代理合作\"]）。\n\n"
                "请仅输出合法的 JSON 字符串，不要包含任何解释文本。"
            )
            user_prompt = f"以下是与客户的聊天记录：\n\n{formatted_chat}"
            message_payload = f"{system_prompt}\n\n---\n\n{user_prompt}"

            # 使用新的事件循环跑 async 方法，避免在后台线程干扰原有主流程循环
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(app_state.ai_service.start_chat(
                    agent_id="",
                    message=message_payload,
                    session_id=f"crm_auto_{wxid}",
                    cache_session=False
                ))
            finally:
                loop.close()

            if not result or not result.get("success") or not result.get("content"):
                return

            content = result["content"].strip()
            
            def clean_and_parse_json(raw_str: str) -> dict:
                cleaned = raw_str.strip()
                if cleaned.startswith("```"):
                    cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
                    cleaned = re.sub(r'\s*```$', '', cleaned)
                cleaned = cleaned.strip()
                
                match = re.search(r'\{.*\}', cleaned, re.DOTALL)
                if not match:
                    raise ValueError("JSON block not found")
                
                json_str = match.group(0)
                # 简单清洗尾随逗号
                json_str = re.sub(r',\s*\}', '}', json_str)
                json_str = re.sub(r',\s*\]', ']', json_str)
                return json.loads(json_str)

            try:
                parsed = clean_and_parse_json(content)
            except Exception as parse_err:
                logger.warning(f"[CRM自动分析] 解析 LLM 返回的 JSON 失败 ({wxid}): {parse_err}. 原始文本: {content[:150]}")
                return

            summary_text = parsed.get("summary", "")
            intent_val = parsed.get("intent_level", "C")
            tags_val = parsed.get("tags", [])

            if not summary_text:
                return

            # 更新客户画像与标签数据
            pm = ProfileManager(account_id=account_id)
            profile = pm.get_profile(wxid, nickname=nickname)
            profile.conversation_summary = summary_text

            intent_map = {"A": "意向-强烈", "B": "意向-中等", "C": "意向-观望"}
            system_intent = intent_map.get(intent_val, "意向-观望")

            new_tag_entries = [
                TagEntry(category="business", subcategory="intent", value=system_intent, confidence=0.9, source="chat")
            ]
            for t in tags_val:
                new_tag_entries.append(
                    TagEntry(category="business", subcategory="need", value=t, confidence=0.8, source="chat")
                )

            pm.update_tags(wxid, new_tag_entries, source="chat")
            profile.conversation_summary = summary_text
            pm.save_profile(profile)
            logger.info(f"[CRM自动分析] 已后台自动为好友 '{wxid}' 提取并合成了最新客户画像")
        except Exception as e:
            logger.warning(f"[CRM自动分析] 后台异步提取画像失败 ({wxid}): {e}")

    threading.Thread(target=_run_sync_analyze, daemon=True, name=f"crm-auto-{wxid[:8]}").start()


def trigger_history_bootstrap_from_wcdb(wxid: str, nickname: str, account_id: str, db_msgs: list):
    """
    【冷启动历史接管】
    在 xm-bot4 首次接管一个已有长期聊天历史的私聊好友时调用。
    直接读取 WCDB 原始消息，后台异步分析出关键历史摘要，
    存入 conversation_summary，让 AI 从第一条回复起就拥有前任的记忆。

    仅在该好友的 ChatHistoryManager 会话为空（即真正的首次接管）且
    WCDB 中存在 >=8 条历史记录时触发。
    """
    if not wxid or not db_msgs or len(db_msgs) < 8:
        return

    def _run_bootstrap():
        try:
            import app.state as app_state
            if not app_state.ai_service or not app_state.ai_service.is_configured():
                return

            # 将 WCDB 原始消息格式化为可读对话文本
            # db_msgs 格式: [{"local_id": int, "is_self": bool, "content": str, "timestamp": int}]
            lines = []
            for m in db_msgs[::-1]:  # WCDB 返回最新在前，倒序还原时间顺序
                content = (m.get("content") or "").strip()
                if not content or content.startswith("<"):  # 过滤系统XML/空消息
                    continue
                is_self = bool(m.get("is_self"))
                role_label = "我方" if is_self else "客户"
                lines.append(f"{role_label}: {content}")

            if len(lines) < 5:
                return

            # 最多取 100 行，避免 token 过长
            chat_text = "\n".join(lines[-100:])

            system_prompt = (
                "你是一个专业的销售 CRM 助手。以下是机器人接管前，人工销售与某客户的历史微信聊天记录。\n"
                "请提取其中的关键信息，以 JSON 格式输出，必须包含以下字段：\n"
                "1. \"summary\": 用 2-3 句话总结该客户的核心需求、关键背景、当前跟进状态以及任何已达成的共识或承诺。\n"
                "2. \"intent_level\": 客户意向等级，只能为 A（意向强烈）、B（普通咨询）、C（意向微弱）之一。\n"
                "3. \"tags\": 数组，最多 5 个最能代表该客户特征的短标签（如 [\"已报过价\", \"关注售后\", \"决策人是老板\"]）。\n"
                "请仅输出合法 JSON，不含任何解释文字。"
            )
            user_prompt = f"以下是历史聊天记录（时间由旧到新）：\n\n{chat_text}"
            message_payload = f"{system_prompt}\n\n---\n\n{user_prompt}"

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(app_state.ai_service.start_chat(
                    agent_id="",
                    message=message_payload,
                    session_id=f"crm_bootstrap_{wxid}",
                    cache_session=False
                ))
            finally:
                loop.close()

            if not result or not result.get("success") or not result.get("content"):
                return

            content = result["content"].strip()
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if not json_match:
                return

            parsed = json.loads(json_match.group(0))
            summary_text = parsed.get("summary", "")
            intent_val = parsed.get("intent_level", "C")
            tags_val = parsed.get("tags", [])

            if not summary_text:
                return

            pm = ProfileManager(account_id=account_id)
            profile = pm.get_profile(wxid, nickname=nickname)

            # 仅当画像摘要为空时才写入（避免覆盖已有的最新摘要）
            if profile.conversation_summary:
                logger.info(f"[CRM冷启动] 好友 '{wxid}' 已有摘要，跳过历史接管分析")
                return

            profile.conversation_summary = f"[接管前历史摘要] {summary_text}"

            intent_map = {"A": "意向-强烈", "B": "意向-中等", "C": "意向-观望"}
            system_intent = intent_map.get(intent_val, "意向-观望")
            new_tag_entries = [
                TagEntry(category="business", subcategory="intent", value=system_intent, confidence=0.85, source="chat")
            ]
            for t in tags_val:
                new_tag_entries.append(
                    TagEntry(category="business", subcategory="need", value=str(t), confidence=0.75, source="chat")
                )

            pm.update_tags(wxid, new_tag_entries, source="chat")
            profile.conversation_summary = f"[接管前历史摘要] {summary_text}"
            pm.save_profile(profile)
            logger.info(f"[CRM冷启动] 已为好友 '{wxid}' 完成历史接管分析，摘要: {summary_text[:60]}...")
        except Exception as e:
            logger.warning(f"[CRM冷启动] 历史接管分析失败 ({wxid}): {e}")

    threading.Thread(target=_run_bootstrap, daemon=True, name=f"crm-boot-{wxid[:8]}").start()

