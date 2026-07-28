import logging
import re
from typing import Any
from .group_mention_resolver import do_resolve_mention_sender_uia

logger = logging.getLogger(__name__)

async def resolve_group_mention_sender(
    engine: Any, name: str, is_group: bool, is_at: bool, account_id: str,
    default_user_name: str, message: str, wxid: str = None
) -> tuple[str, str, bool]:
    """
    如果在群聊中有人@我（或@所有人且开启响应），跳转到@位置，获取准确的发送人姓名，并回填给工作流。
    同时剥离消息开头的 @ 提及前缀，避免 AI 误判。
    """
    if not is_group or not is_at:
        return default_user_name, message, False

    # 0. 整理我们自己的昵称和配置名称，用于后续剥离 @ 提及前缀
    from src.api.config_api import _load_configs
    configs = _load_configs() or {}
    bot_wxid = account_id or getattr(engine.driver, 'bot_wxid', None) or getattr(engine.driver, '_wxid', None) or 'default'
    nicknames_to_check = [n for n in [
        getattr(engine.driver, '_nickname', ''),
        bot_wxid,
        configs.get("bot_name", "")
    ] if n]

    # 🌟 将自己在该群的群名片（群昵称）也加入匹配，确保识别群友对群名片的 @
    try:
        from src.utils.contacts_cache import contacts_cache
        members = contacts_cache.get_group_members(bot_wxid, name) or []
        for m in members:
            if m.get("wxid") == bot_wxid:
                group_card = (m.get("display_name") or m.get("nickname") or "").strip()
                if group_card and group_card not in nicknames_to_check:
                    nicknames_to_check.append(group_card)
                    logger.info(f"[Mention] 成功获取并添加群聊 '{name}' 中的自我群名片: '{group_card}'")
                break
    except Exception as e_card:
        logger.debug(f"[Mention] 获取群名片异常: {e_card}")

    # 🌟 @所有人 始终响应回复，不受 respond_to_all_mentions 开关限制
    respond_to_all = True

    # Check if the last message itself contains a mention to self
    last_msg_has_at_self = False
    last_msg_clean = message.replace('\u2005', ' ').replace('\u200b', '').strip()
    for n in nicknames_to_check:
        if n and re.search(rf'@[\s\u2005]*{re.escape(n)}', last_msg_clean, re.IGNORECASE):
            last_msg_has_at_self = True
            break

    # Check if the last message itself contains a mention to everyone
    is_at_all = False
    for all_tag in ("所有人", "all", "All"):
        if re.search(rf'@[\s\u2005]*{re.escape(all_tag)}', last_msg_clean, re.IGNORECASE):
            is_at_all = True
            break

    if not last_msg_has_at_self and not is_at_all:
        logger.info(f"[Mention] 检测为免 @ 热度追问消息，无需物理滚动查找，直接使用当前发送人: '{default_user_name}'")
        return default_user_name, message, False

    final_sender = default_user_name if last_msg_has_at_self else ""
    final_msg = message

    try:
        from src.utils.uia_task_runner import run_uia_with_timeout
        # 执行抽离后的物理查找 UIA 任务
        res = await run_uia_with_timeout(
            do_resolve_mention_sender_uia, 20.0,
            engine, name, account_id, nicknames_to_check, respond_to_all, message, wxid
        )
        if res:
            resolved_sender = res.get("sender_name")
            resolved_msg = res.get("message")
            resolved_at_all = res.get("is_at_all", False)
            
            if resolved_at_all:
                is_at_all = True
                final_sender = ""
            else:
                if resolved_sender:
                    final_sender = resolved_sender
                else:
                    final_sender = default_user_name if last_msg_has_at_self else ""
            final_msg = resolved_msg if resolved_msg else message
    except Exception as ex:
        logger.error(f"[Mention] resolve_group_mention_sender exception: {ex}")

    if is_at_all:
        final_sender = ""

    # 🌟 智能提及前缀过滤：清洗掉消息开头的 "@机器人昵称" 前缀以及可能存在的各种微信空白符号
    if final_msg:
        sorted_nicks = sorted(nicknames_to_check, key=len, reverse=True)
        for nick in sorted_nicks:
            pattern = rf"^@[\s\u2005\xa0]*{re.escape(nick)}[\s\u2005\xa0]*"
            cleaned, count = re.subn(pattern, "", final_msg, flags=re.IGNORECASE)
            if count > 0:
                final_msg = cleaned.strip()
                break

    logger.info(f"[Mention] Resolved group mention: sender={final_sender}, msg={final_msg[:30]}, is_at_all={is_at_all}")
    return final_sender, final_msg, is_at_all
