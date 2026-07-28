import logging
import hashlib
import time
import uiautomation as uia
from typing import Any

logger = logging.getLogger("WeChatInterceptor")

# 缓存已处理的违规消息指纹，防止重复触发撤回动作
_recalled_fingerprints = set()

def audit_and_intercept_message(driver: Any, item: Any, parsed: dict, session_name: str) -> bool:
    """
    审计并拦截单条出方向（自己发送的）消息。
    如果是违规名片或敏感词，执行物理撤回并上报审计平台。
    
    Returns:
        bool: True 代表触发了撤回拦截，False 代表正常放行。
    """
    global _recalled_fingerprints
    
    is_self = parsed.get("isSelf", False)
    if not is_self:
        return False
        
    msg_type = parsed.get("type", "")
    content = parsed.get("content", "")
    if not content:
        return False
        
    # 系统级、打招呼或本身已撤回的消息，跳过检测
    if msg_type in ("system", "greet", "recall"):
        return False

    is_violation = False
    reason = ""

    # 1. 拦截个人名片发送
    if msg_type == "card" or "[名片]" in content or "[个人名片]" in content or "个人名片" in content:
        is_violation = True
        reason = "违规发送个人名片"
    
    # 2. 拦截飞单/收款/私聊导流敏感词
    elif msg_type == "text":
        # 合并全局违禁词与行业默认敏感词库
        forbidden_words = []
        try:
            from src.utils.config_cache import config_cache
            forbidden_words = config_cache.get("forbidden_words", [])
        except Exception:
            pass
            
        default_fly_words = [
            "转我", "发我红包", "支付宝", "私下转账", "加我个人", 
            "加我私人", "加我私号", "加个号", "换个微信", "扫我", 
            "扫码", "加我另一个"
        ]
        all_forbidden_words = set(forbidden_words + default_fly_words)
        
        for word in all_forbidden_words:
            if word and word in content:
                is_violation = True
                reason = f"违规发送飞单敏感词「{word}」"
                break

    if not is_violation:
        return False

    # 生成物理定位指纹，防止同一个会话中的同一消息重复尝试撤回
    try:
        rect = item.BoundingRectangle
        fp_str = f"{session_name}:{msg_type}:{content}:{rect.left}:{rect.top}"
        fp = hashlib.md5(fp_str.encode()).hexdigest()
    except Exception:
        # 兜底指纹
        fp = hashlib.md5(f"{session_name}:{content}".encode()).hexdigest()

    if fp in _recalled_fingerprints:
        return True

    _recalled_fingerprints.add(fp)
    logger.warning(f"[飞单拦截] 🚨 检测到员工违规发送行为: {reason}! 会话: {session_name}, 内容: {content}")

    # 执行物理撤回
    try:
        from src.uia.input_guard import uia_lock
        with uia_lock("正在执行安全撤回违规消息"):
            from src.uia.messager_media import right_click_menu_item
            success = right_click_menu_item(item, ["撤回", "Recall", "Withdraw"])
            if success:
                logger.info(f"[飞单拦截] ✅ 成功物理撤回违规消息: {content}")
                
                # 异步上报至企业审计日志
                try:
                    from src.utils.cloud_sync import get_cloud_client
                    client = get_cloud_client()
                    if client:
                        client.report_audit_event(
                            action="intercept_fly_order",
                            target=session_name,
                            detail={"reason": reason, "content": content, "status": "recalled"},
                            risk_level="critical"
                        )
                except Exception as audit_ex:
                    logger.error(f"[飞单拦截] 上报审计事件失败: {audit_ex}")
                return True
            else:
                logger.error(f"[飞单拦截] ❌ 撤回违规消息失败 (可能已超出2分钟可撤回时限)")
    except Exception as e:
        logger.error(f"[飞单拦截] 执行撤回异常: {e}")

    return False
