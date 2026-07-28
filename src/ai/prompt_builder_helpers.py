"""
Prompt 路由器辅助构造模块 — 拆分复杂 Prompt 生成逻辑以精简主路由器
"""
import random
import logging
from typing import Optional
from src.crm.industry_config import IndustryProfile
from src.crm.prompt_builder import PromptBuilder
from . import prompt_templates

logger = logging.getLogger(__name__)

def emoji_rule(allow: bool) -> str:
    return "- 如果此刻聊天氛围轻松愉快，可以在句尾加一个 emoji；如果氛围一般或严肃，就不要加" if allow else "- 本次回复不要使用任何 emoji 或表情符号，用纯文字回复"

def rewrite_media_message(message: str) -> str:
    """对多模态媒体占位符进行重写，注入高情商 AI 指导指令，使其不会硬性报错或给出生硬回复"""
    msg = message.strip()
    if msg == "[图片]" or msg.startswith("[图片本地路径]"):
        return "[对方给您发了一张图片，你当前无法看清具体画面。请高情商、幽默且富有亲和力地用微信真人语气回应（例如：收到你的图片啦，不过我现在还没法直接看图哈 🙈 咱们今天是在看什么产品呀？），不要生硬说看不到，并适度引导对方说出具体问题。]"
    elif msg == "[表情]" or msg == "表情":
        return "[对方给您发了一个微信表情包，你当前无法看清具体画面。请极其高情商、轻松幽默地用微信真人语气回应（例如：哈哈好可爱的表情包！你也是来了解我们拓客系统的嘛？😊），保持亲切互动，并引导回咱们的产品上。]"
    elif msg == "[文件]" or msg.startswith("[文件本地路径]"):
        return "[对方给您发了一个文件，你当前无法直接打开读取。请友好并职业地提示对方您已收到文件，并询问这个文件是关于什么内容的，以便您更好提供帮助。]"
    elif msg == "[视频]":
        return "[对方给您发了一段视频，你当前无法直接播放。请高情商、真诚地回复已收到视频，并客气询问视频中展示的是什么内容或遇到什么操作疑问。]"
    elif msg == "[语音]":
        return "[对方给您发了一条语音，由于微信原生翻译超时，您暂时无法听清。请礼貌地抱歉，并高情商地提示对方您现在不方便听语音，麻烦对方打字说明一下，或者询问是不是有什么问题需要解答。]"
    return message

def _format_history_msg(m: dict) -> str:
    role = m.get('role')
    content = m.get('content', '').strip()
    if role == 'user':
        sender = m.get('sender')
        if sender:
            return f"[{sender}]：{content}"
        return f"客户：{content}"
    else:
        return f"你：{content}"

def build_casual_prompt(
    message: str,
    industry_config: Optional[IndustryProfile] = None,
    history_messages: list = None,
    allow_emoji: bool = False,
    profile_tags: str = "暂无",
    sdr_context: str = "未启用自动跟单"
) -> str:
    h_txt = "\n\n[上下文]\n" + "\n".join([_format_history_msg(m) for m in history_messages[-20:]]) if history_messages else ""
    persona = getattr(industry_config, 'persona', None) or '专业的销售顾问'
    product = getattr(industry_config, 'product', None) or '通用产品'
    pt = prompt_templates.CASUAL_PROMPT.format(persona=persona, product=product, emoji_rule=emoji_rule(allow_emoji))
    processed_message = rewrite_media_message(message)
    return f"{pt}\n\n## 【商机与销售流上下文 (CRM & SDR Context)】\n- 客户 CRM 标签: {profile_tags}\n- SDR 自动跟单状态: {sdr_context}{h_txt}\n\n---\n对方说：{processed_message}"

def build_business_prompt(
    message: str,
    industry_config: Optional[IndustryProfile],
    chat_round: int,
    history_messages: list = None,
    allow_emoji: bool = False,
    profile_tags: str = "暂无",
    sdr_context: str = "未启用自动跟单"
) -> str:
    base = PromptBuilder.build(industry_config, include_profiling=False)
    round_info = f"\n当前对话轮次：第{chat_round}轮"
    persona = getattr(industry_config, 'persona', None) or '专业的销售顾问'
    product = getattr(industry_config, 'product', None) or '通用产品'
    enh = prompt_templates.BUSINESS_ENHANCEMENT.format(persona=persona, product=product, emoji_rule=emoji_rule(allow_emoji))
    h_txt = "\n\n[上下文记忆]\n" + "\n".join([_format_history_msg(m) for m in history_messages[-20:]]) if history_messages else ""
    prof = PromptBuilder._get_profiling_instruction()
    processed_message = rewrite_media_message(message)
    return f"{base}{round_info}\n\n## 【商机与销售流上下文 (CRM & SDR Context)】\n- 客户 CRM 标签: {profile_tags}\n- SDR 自动跟单状态: {sdr_context}\n{enh}{h_txt}\n\n{prof}\n\n---\n客户当前消息：{processed_message}"

def build_price_prompt(
    message: str,
    industry_config: Optional[IndustryProfile],
    chat_round: int,
    history_messages: list = None,
    profile_tags: str = "暂无",
    sdr_context: str = "未启用自动跟单",
    extra_pricing_text: str = ""
) -> str:
    base_prompt = PromptBuilder.build(industry_config, include_profiling=False)
    round_info = f"\n当前对话轮次：第{chat_round}轮"
    pricing_section = ""
    if extra_pricing_text:
        pricing_section = f"\n\n## 【产品定价表】（你必须基于此数据报价，禁止编造价格！立刻告诉客户具体价格！）\n{extra_pricing_text}"
    elif industry_config:
        price_list = getattr(industry_config, 'price_list', None) or []
        if price_list:
            lines = ["\n## 【产品定价表】（你必须基于此数据报价，禁止编造！）"]
            for item in price_list:
                if isinstance(item, dict):
                    lines.append(f"- {item.get('name', '')}：{item.get('price', '')}" + (f"（{item.get('description', '')}）" if item.get('description', '') else ''))
                elif isinstance(item, str):
                    lines.append(f"- {item}")
            pricing_section = "\n".join(lines)
    history_text = ""
    if history_messages:
        history_text = "\n\n[上下文记忆]\n" + "\n".join([_format_history_msg(m) for m in history_messages[-20:]])
    profiling_instruction = PromptBuilder._get_profiling_instruction()
    context_section = f"\n\n## 【商机与销售流上下文 (CRM & SDR Context)】\n- 客户 CRM 标签: {profile_tags}\n- SDR 自动跟单状态: {sdr_context}"
    processed_message = rewrite_media_message(message)
    return f"{base_prompt}{round_info}{context_section}{pricing_section}\n{prompt_templates.PRICE_ENHANCEMENT}{history_text}\n\n{profiling_instruction}\n\n---\n客户当前消息：{processed_message}"

def build_friend_accepted_prompt(
    message: str,
    industry_config: Optional[IndustryProfile] = None,
    allow_emoji: bool = False
) -> str:
    persona = getattr(industry_config, 'persona', None) or '专业的销售顾问'
    product = getattr(industry_config, 'product', None) or '通用产品'
    prompt_text = prompt_templates.FRIEND_ACCEPTED_PROMPT.format(persona=persona, product=product, emoji_rule=emoji_rule(allow_emoji))
    return f"{prompt_text}\n\n---\n微信系统提示：{message}\n请根据此状态发送你的首句破冰欢迎语。"

def filter_materials(materials: list, config: Optional[IndustryProfile]) -> list:
    if not materials or not isinstance(materials, list):
        return materials
    if not config:
        return materials
    
    mode = getattr(config, "material_send_mode", "all")
    limit = getattr(config, "material_send_limit", 3)
    try:
        limit = int(limit)
    except Exception:
        limit = 3

    if mode == "random_1":
        return [random.choice(materials)] if materials else []
    elif mode == "random_limit":
        shuffled = list(materials)
        random.shuffle(shuffled)
        return shuffled[:limit]
    elif mode == "all":
        if limit > 0 and len(materials) > limit:
            return materials[:limit]
    return materials
