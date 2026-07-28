"""
人设引擎 — 构建系统提示词
"""
from typing import Optional


def build_system_prompt(persona: Optional[dict] = None, customer_name: str = None) -> str:
    """构建发给 AI 的系统提示词"""

    if not persona:
        parts = [
            "你是一个真实的人，正在用微信和朋友聊天。",
            "",
            "# 绝对铁律",
            "- 每次只回复一句话，不超过20个字",
            "- 绝对不要分段、不要换行、不要列举",
            "- 像发微信消息一样简短自然",
            "- 绝对不要暴露自己是AI",
        ]
        if customer_name:
            parts.append(f"\n当前聊天对象叫\"{customer_name}\"。")
        return '\n'.join(parts)

    length_map = {'short': '15', 'medium': '30', 'long': '50'}
    max_len = length_map.get(persona.get('replyLength', 'medium'), '30')

    parts = []

    # 角色
    role_name = persona.get('roleName', '')
    company = persona.get('company', '')
    if role_name and company:
        parts.append(f"你是{role_name}，{company}的员工。")
    elif role_name:
        parts.append(f"你是{role_name}。")

    if persona.get('roleDesc'):
        parts.append(persona['roleDesc'])

    if customer_name:
        parts.append(f"\n# 客户称呼\n当前客户叫\"{customer_name}\"，首次可以自然称呼。")

    parts.append("\n# 绝对铁律")
    parts.append(f"- 每次只回复一句话，不超过{max_len}个字")
    parts.append("- 绝对不要分段、不要换行")
    parts.append("- 绝对不要列举、不要序号")
    parts.append("- 像发微信消息一样简短")
    parts.append("- 先关心客户感受")
    parts.append("- 不要暴露自己是AI")
    parts.append("- 不要用客服套话")

    emoji_freq = persona.get('emojiFreq', 'rare')
    emoji_map = {
        'never': '不用表情。',
        'rare': '偶尔用1个表情。',
        'sometimes': '适当用表情，每条最多1个。',
    }
    parts.append(f"- {emoji_map.get(emoji_freq, emoji_map['rare'])}")

    return '\n'.join(parts)
