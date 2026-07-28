"""
群聊助理 Prompt 构建模块

负责为群聊场景生成纯助理身份的 Prompt，彻底与销售 Prompt 隔离。
机器人在群聊中被 @ 时，应以中立助手身份基于群聊历史回答问题，
绝对禁止套用销售员话术或推销任何产品。
"""
import re
import logging

logger = logging.getLogger(__name__)


def get_bot_identity_info(actual_message: str, is_at: bool = False) -> tuple[str, list[str], str]:
    """
    获取机器人挂机账号的身份信息。
    返回 (主昵称, 衍生简称列表, 当前被叫 of 对应名称/被@的名称)
    """
    primary_name = "助理"
    aliases = []
    called_name = ""
    
    try:
        from src.crm.account_data import get_active_nickname
        nickname = get_active_nickname()
        if nickname and nickname != "default":
            primary_name = nickname
    except Exception:
        pass
        
    import re
    
    # 1. 尝试清洗主昵称（去除数字后缀）
    clean_primary = re.sub(r"\d+$", "", primary_name).strip()
    if clean_primary:
        primary_name = clean_primary
        
    # 2. 生成衍生简称
    chinese_chars = re.findall(r"[\u4e00-\u9fa5]", primary_name)
    if len(chinese_chars) >= 2:
        first = chinese_chars[0]
        last = chinese_chars[-1]
        common_forms = [
            f"小{first}", f"老{first}", f"{first}总", f"{first}哥", f"{first}姐", f"{first}经理",
            f"小{last}", f"{last}{last}"
        ]
        for form in common_forms:
            if form not in aliases:
                aliases.append(form)
    elif len(chinese_chars) == 1:
        char = chinese_chars[0]
        common_forms = [f"小{char}", f"老{char}", f"{char}总", f"{char}{char}"]
        for form in common_forms:
            if form not in aliases:
                aliases.append(form)
                
    # 3. 提取当前消息中被 @ 的称呼，并验证是否与机器人相关
    at_match = re.search(r"@([^\s\u2005\u200b]+)", actual_message)
    if at_match:
        extracted_name = at_match.group(1).strip()
        # 清理可能带有的微信号数字后缀
        extracted_name = re.sub(r"\d+$", "", extracted_name).strip()
        
        # 100% 确定有没有 @ 到挂机机器人：
        # 如果 is_at 为 True，说明底层判定当前消息确实是@到机器人。我们直接、无条件信任此被@的称呼！
        if is_at:
            called_name = extracted_name
        else:
            # 只有在 is_at 为 False（例如免艾特聊天中），我们才进行安全校验，防范认领别人的名字
            is_self_at = False
            if extracted_name == primary_name or extracted_name in aliases:
                is_self_at = True
            elif (extracted_name and extracted_name in primary_name) or (primary_name and primary_name in extracted_name):
                is_self_at = True
            elif extracted_name.lower() in ("所有人", "all", "assistant", "助理"):
                is_self_at = True
                
            if is_self_at:
                called_name = extracted_name
        
    return primary_name, aliases, called_name


def build_group_chat_assistant_prompt(
    actual_message: str,
    user_name: str,
    group_name: str,
    context_msgs: list,
    is_at: bool = False,
    is_physical_at: bool = True,
) -> str:
    """
    为群聊场景构建纯助理 Prompt。

    核心设计原则：
    - 角色：群聊中立助手，不是销售员、不推销任何产品
    - 行为：基于群聊历史帮群员解答问题、总结内容、提供客观建议
    - 约束：绝对禁止询问行业/账号数量/引导购买任何产品
    """
    primary_name, aliases, called_name = get_bot_identity_info(actual_message, is_at=is_at)

    # 构造身份描述
    identity_desc = f"你的名字是「{primary_name}」。你就是群聊「{group_name}」中的「{primary_name}」本人。你的微信昵称是「{primary_name}」。"
    if called_name and called_name != primary_name:
        identity_desc += f"\n在当前的消息中，对方直接以「{called_name}」来称呼你或 @ 你。"

    alias_list = []
    if called_name:
        alias_list.append(called_name)
    for a in aliases:
        if a not in alias_list:
            alias_list.append(a)
    if primary_name not in alias_list:
        alias_list.append(primary_name)

    alias_str = "、".join(f"「{a}」" for a in alias_list)
    identity_desc += f"\n群成员可能会用 {alias_str} 等名字或简称来称呼你。当他们使用这些称呼中的任意一个，或者在消息中提到/ @ 它们时，这都代表他们在直接和你对话。"

    # 格式化群聊历史
    valid_context = []
    if context_msgs:
        import time
        
        def _parse_ts(time_str: str) -> float:
            try:
                for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%H:%M:%S', '%H:%M'):
                    try:
                        return time.mktime(time.strptime(time_str, fmt))
                    except ValueError:
                        continue
            except Exception:
                pass
            return 0.0

        reversed_msgs = list(reversed(context_msgs))
        for i, m in enumerate(reversed_msgs):
            valid_context.append(m)
            if i < len(reversed_msgs) - 1:
                t_curr = _parse_ts(m.get("time", ""))
                t_prev = _parse_ts(reversed_msgs[i + 1].get("time", ""))
                if t_curr > 0 and t_prev > 0 and abs(t_curr - t_prev) > 14400.0:
                    break
        valid_context.reverse()
    else:
        valid_context = []

    history_lines = []
    if valid_context:
        for m in valid_context[-30:]:  # 最多取最近30条群聊记录
            role = m.get("role", "user")
            content = m.get("content", "").strip()
            sender = m.get("sender", "")
            ts = m.get("time", "")
            if not content:
                continue

            if role == "assistant":
                label = "🤖 机器人"
            elif sender:
                label = f"👤 {sender}"
            else:
                label = "群员"
            time_prefix = f"[{ts[:16]}] " if ts and len(ts) >= 16 else ""
            history_lines.append(f"{time_prefix}{label}：{content}")

    # 清理用户消息中的 @ 提及（如 "@机器人昵称 帮我总结..." → "帮我总结..."）
    clean_msg = re.sub(r"@\S+\s*", "", actual_message).strip()
    
    is_pure_at = not clean_msg
    if is_pure_at:
        clean_msg = "（纯 @ 招呼，无提问内容）"
        guidance = (
            "群成员仅仅 @ 了你，没有提出具体问题。"
            "请直接、礼貌地回复问候（如：‘您好！在的，有什么我可以帮您？’），"
            "绝对不要去主动总结、解释或提及上方的群聊历史记录！"
        )
    else:
        # 🌟 若历史为空且 actual_message 包含多段聚合内容（以"。"分隔且超过1段），
        # 说明缓冲区已合并了多条连续消息，将其补充进历史块作为临时上下文
        if not history_lines and "。" in clean_msg:
            parts = [p.strip() for p in clean_msg.split("。") if p.strip()]
            if len(parts) >= 2:
                # 最后一段是触发 @ 的消息，其余视为上文补充
                for p in parts[:-1]:
                    history_lines.append(f"👤 {user_name}：{p}")
                clean_msg = parts[-1]

    has_history = bool(history_lines)
    history_block = "\n".join(history_lines) if history_lines else "（暂无群聊记录）"

    # 根据是否有历史和是否为纯 @ 给出不同的指引，避免空历史时输出"没有记录"这种无用回复
    if not is_pure_at:
        if has_history:
            guidance = (
                "请根据上方群聊内容，直接、准确地回答该群成员的问题。"
                "回复简洁友好，不超过200字。"
            )
        else:
            guidance = (
                "当前暂时没有加载到群聊历史记录（系统刚刚启动中），"
                "请根据群成员问题中的关键词，给出实用、客观的通用建议。"
                "绝对不要说'没有历史记录'或要求对方重新描述问题。"
                "如果问题中有指示词如'这种问题'，请根据常见相关场景合理推断并给出有价值的回答。"
                "回复不超过200字。"
            )

    at_guidance = ""
    if not is_physical_at:
        at_guidance = "\n- 【群聊免 @ 对话期】：当前对话属于热度内的免 @ 连贯对话。你绝对不要在回复的开头或正文里输出“@用户名”或“@对方”。请以自然的微信用词直接回应，如同真人在群聊中连贯聊天接话一样。"

    prompt = f"""{identity_desc}
你的职责是帮助群成员解答问题、理解群内讨论内容，并提供客观中立的信息 and 建议。

## 重要身份约束（必须严格遵守）
- 必须使用第一人称（如“我”、对方当前称呼你的名字）直接进行对答，绝对禁止以第三人称说“{primary_name}上线看到消息后应该就会回应啦”这类傻话，因为你就是{primary_name}（被叫作{called_name or primary_name}）本人，你当前正处于在线且已读状态并直接在发送回复！
- 绝对禁止以“留在群里帮大家处理问题”、“我是群助手/机器人/AI/系统”或“需要在此守着/值班”为由推脱或解释不看书/不干别的事。你的行为举止必须像一个真正的、有自己真实生活的普通人类群友，可以找借口说“今晚还在外面呢”、“晚点再看”、“还没定呢，等会儿翻翻看”等自然的人类社交托词。真人出去玩或在外面吃饭等完全可以在手机上同时在群里随手聊天，这很自然。
- 你是群聊助手，不是销售员，不代表任何商业产品
- 绝对禁止：询问对方"做什么行业"、"管几个号"、推荐或销售 any 软件系统
- 回复使用自然的中文，语气亲切友好，像真人在群里聊天一样。{at_guidance}

## 当前群聊记录（最近30条）
{history_block}

## 群成员 {user_name} 的问题
{clean_msg}

{guidance}"""

    return prompt
