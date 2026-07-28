import os
import uuid
import logging
import urllib.request
import tempfile
from datetime import datetime
from typing import List, Optional

logger = logging.getLogger(__name__)

def should_follow_up_now(last_follow_time_str: str, frequency: str, follow_count: int = 0) -> bool:
    if not last_follow_time_str:
        return True
    try:
        elapsed = (datetime.now() - datetime.fromisoformat(last_follow_time_str)).total_seconds()
        limit = 86000 if follow_count < 3 else 172000 if frequency == "front3_then_interval2" else {
            "daily": 86000, "every_2_days": 172000, "every_3_days": 258000
        }.get(frequency, 86000)
        return elapsed >= limit
    except Exception:
        return True

def get_friend_display_name(friend_wxid: str) -> str:
    from src.utils.contacts_cache import contacts_cache
    from src.crm.account_data import get_active_account
    friends = contacts_cache.get_friends(get_active_account() or "main")
    f = next((x for x in friends if x.get("wxid") == friend_wxid), {})
    return f.get("remark") or f.get("name") or friend_wxid

def download_media_if_url(text: str) -> Optional[str]:
    url = text.strip()
    if url.startswith("http://") or url.startswith("https://"):
        if any(ext in url.lower() for ext in [".jpg", ".jpeg", ".png", ".gif", ".pdf", ".docx", ".xlsx", ".mp4", ".mp3", ".txt"]):
            try:
                suffix = os.path.splitext(url.split("?")[0])[1] or ".tmp"
                local_path = os.path.join(tempfile.gettempdir(), f"dl_{uuid.uuid4().hex[:8]}{suffix}")
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=15) as r, open(local_path, 'wb') as f:
                    f.write(r.read())
                return local_path
            except Exception as e:
                logger.error(f"[AutoFollowSDR] 下载附件异常: {e}")
    return None

async def generate_sdr_reply(task: dict, friend_wxid: str, friend_name: str, fallback_text: str) -> List[str]:
    """使用 AI 智能生成跟进文案，支持多段自动拆分"""
    use_ai = task.get("use_ai", False)
    follow_scenario = task.get("follow_scenario", "")
    
    if not use_ai:
        return [fallback_text] if fallback_text else ["您好，请问有什么可以帮您的？"]
    
    try:
        from src.utils.chat_history import ChatHistoryManager
        from src.crm.account_data import get_active_account
        account_id = get_active_account() or "main"
        history_mgr = ChatHistoryManager(account_id)
        history = history_mgr.get_context(friend_wxid, window_size=10)
        
        chat_history_str = ""
        for msg in history:
            role_name = "客户" if msg.get("role") != "assistant" else "我(销售助理)"
            chat_history_str += f"[{role_name}]: {msg.get('content')}\n"
            
        prompt = f"""你是一个专业的销售跟单助手 (SDR)。
当前跟进策略场景为：{follow_scenario}。
以下是与该客户的最近聊天记录（最近10条）：
{chat_history_str or "（暂无历史聊天记录）"}

请结合上述跟进策略场景与聊天记录，为该客户生成一条最合适、自然、有吸引力的跟进消息。
规则约束：
1. 不要包含任何括号占位符，例如 [客户姓名] 或 {{name}} 等。
2. 语气要真诚、亲切，像一个真实的真人销售，不要有 AI 腔调。
3. 如果你想发送多条消息，请使用空行分割它们。我们会自动将其拆分为多条消息依次发送。
4. 不要输出任何解释或前言，直接输出你要回复的内容。
"""
        from src.api.config_api import _ai_service
        ai_service = _ai_service
        if not ai_service:
            from src.ai.factory import AIServiceFactory
            from src.api.config_api import _load_configs
            configs = _load_configs()
            ai_service = AIServiceFactory.create_from_full_config(configs)
            
        agent_id = task.get("agent_id", "")
        if not agent_id and hasattr(ai_service, 'get_agent_id_for_role'):
            agent_id = ai_service.get_agent_id_for_role("chat")

        if ai_service and ai_service.is_configured():
            res = await ai_service.start_chat(
                agent_id=agent_id,
                message=prompt,
                cache_session=False
            )
            if res.get("success"):
                reply_text = res.get("content", "").strip()
                if reply_text:
                    parts = [p.strip() for p in reply_text.split("\n") if p.strip()]
                    if parts:
                        return parts
        logger.warning("[AutoFollowSDR] AI 智能跟进生成失败，回退到 fallback_text")
    except Exception as e:
        logger.error(f"[AutoFollowSDR] 调用 AI 生成跟单消息异常: {e}")
        
    return [fallback_text] if fallback_text else ["您好，请问有什么可以帮您的？"]
