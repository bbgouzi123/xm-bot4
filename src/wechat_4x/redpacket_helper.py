import logging
import asyncio
import threading

logger = logging.getLogger("WeChat4xRedPacketHelper")

def try_trigger_redpacket(scanner, loop, name, session_id, content, is_group, reply_cfg):
    is_redpacket = False
    is_transfer = False
    content_lower = content.lower()
    if any(x in content or x in content_lower for x in ("[微信红包]", "[红包]", "<wcpayinfo>", "<des><![CDATA[微信红包]]>", "微信红包")):
        if "你领取了" not in content and "已拆" not in content:
            is_redpacket = True
    elif ("[转账]" in content or "微信转账" in content or "待你收款" in content) and "已收款" not in content and "已收" not in content:
        is_transfer = True
    
    if (is_redpacket or is_transfer) and not is_group:
        redpacket_enabled = bool(reply_cfg.get("auto_redpacket_friend_enabled", False))
        if redpacket_enabled:
            logger.info(f"[WCDB双引擎] 🧧 检测到新红包或转账 [{name}]，自动抢/收开关已开启")
            if loop and loop.is_running():
                from src.utils.uia_task_runner import run_in_uia_thread
                asyncio.run_coroutine_threadsafe(
                    run_in_uia_thread(scanner.driver.claim_redpacket, session_id, is_group, __timeout_sec=45.0),
                    loop
                )
            else:
                threading.Thread(
                    target=scanner.driver.claim_redpacket,
                    args=(session_id, is_group),
                    daemon=True
                ).start()
