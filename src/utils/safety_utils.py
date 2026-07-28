import logging

logger = logging.getLogger("SafetyUtils")

def check_message_safety(target: str, text: str) -> bool:
    """敏感词和黑名单过滤安全校验
    
    返回 True 表示安全放行，False 表示已被拦截阻断。
    """
    if not target or not text:
        return True

    # 1. 违禁词物理过滤
    try:
        from src.utils.config_cache import config_cache
        global_forbidden_words = config_cache.get("forbidden_words", [])
        if global_forbidden_words:
            for word in global_forbidden_words:
                if word and word in text:
                    logger.warning(f"[违禁词拦截] 消息包含全局违禁词「{word}」，已阻断发送！目标: {target}")
                    return False
    except Exception as e:
        logger.error(f"[安全拦截] 违禁词校验异常: {e}")

    # 2. 黑名单物理过滤
    try:
        from src.utils.config_cache import config_cache
        blacklist = config_cache.get("blacklist", [])
        if blacklist:
            for b_item in blacklist:
                b_val = b_item if isinstance(b_item, str) else b_item.get("wxid") or b_item.get("nickname")
                if b_val and (b_val == target or b_val in target):
                    logger.warning(f"[黑名单拦截] 目标 {target} 属于企业黑名单，已阻断发送！")
                    return False
    except Exception as e:
        logger.error(f"[安全拦截] 黑名单校验异常: {e}")

    return True
