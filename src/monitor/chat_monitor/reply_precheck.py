"""
reply_precheck.py — 快速群聊白名单预检模块

在 resolve_group_mention_sender 之前执行轻量级白名单拦截，
防止非白名单群聊持有 _workflow_lock 期间触发耗时的 UIA 物理操作（最长 20s），
阻塞其他加白群聊/好友的正常自动回复流程。
"""
import logging
import re
from typing import Any

from .message_scanner import check_group_in_list

logger = logging.getLogger(__name__)


def quick_group_whitelist_precheck(engine: Any, name: str, wxid: str = None) -> bool:
    """
    🌟 快速群聊白名单预检（轻量级同步函数）

    在 resolve_group_mention_sender 之前调用，用于判断该群聊是否应该处理。
    若不应处理，提前 return 避免浪费全局 _workflow_lock 占用时间（最长可达 20 秒 UIA 操作）。

    只做以下纯内存/缓存检查（不触发任何 DB 同步或 UIA 物理操作）：
      1. 群聊自动回复总开关 (bot_group_auto_start)
      2. 群聊白名单/黑名单（仅查缓存，不执行热修复同步）

    返回 True 表示应该继续处理；False 表示可以提前跳过。
    异常时默认放行（降级到完整 check_reply_preconditions 兜底）。
    """
    try:
        account_id = (
            getattr(engine.driver, 'bot_wxid', None)
            or getattr(engine.driver, '_wxid', None)
            or 'default'
        )

        from src.api.config_api.privacy_shield import _get_reply_config_isolated
        reply_cfg = _get_reply_config_isolated(account_id)

        # 检查群聊自动回复总开关
        if not reply_cfg.get("bot_group_auto_start", False):
            logger.info(f"[快速预检] 群聊自动回复总开关未开启，提前跳过 '{name}'")
            return False

        # 检查白名单/黑名单（仅查内存缓存，不执行热修复 DB 同步）
        group_mode = reply_cfg.get("auto_chat_group_mode", "black")
        raw_groups = reply_cfg.get(
            "auto_chat_group_whitelist" if group_mode == "white" else "auto_chat_group_excludes", []
        ) or []
        group_list = [x for x in (str(x).strip() for x in raw_groups if x) if x and x != "uid_"]

        clean_name = re.sub(r'[（\(]\d+[）\)]$', '', name).strip()

        g_wxid = wxid.strip() if wxid else ""
        if not g_wxid:
            from src.utils.contacts_cache import contacts_cache
            for g in contacts_cache.get_groups(account_id):
                g_w = (g.get("wxid") or "").strip()
                g_n = (g.get("name") or "").strip()
                if g_w and g_w in (name.strip(), clean_name):
                    g_wxid = g_w
                    break
                if g_n in (name.strip(), clean_name):
                    g_wxid = (g.get("wxid") or "").strip()
                    break

        in_list = (
            check_group_in_list(name, g_wxid, group_list, account_id=account_id)
            or check_group_in_list(clean_name, g_wxid, group_list, account_id=account_id)
        )

        if group_mode == "white" and not in_list:
            logger.info(f"[快速预检] 群聊 '{name}' 不在白名单，提前跳过（免 UIA mention 解析）")
            return False
        if group_mode == "black" and in_list:
            logger.info(f"[快速预检] 群聊 '{name}' 在黑名单，提前跳过（免 UIA mention 解析）")
            return False

        return True
    except Exception as e:
        logger.warning(f"[快速预检] 群聊白名单预检异常，降级放行至完整前置检查: {e}")
        return True  # 保守放行，由完整 check_reply_preconditions 兜底
