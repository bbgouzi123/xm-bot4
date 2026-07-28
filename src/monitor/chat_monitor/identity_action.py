"""
回复成功后的身份引导动作执行器（物理打标 + 拉群降级）
从 reply_workflow.py 拆分，满足 300 行单文件限额。
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def execute_post_reply_identity_action(engine: Any, session_name: str, account_id: str, action: dict):
    from src.utils.uia_lock import UIATaskPriority
    from src.utils.uia_task_runner import run_uia_with_timeout
    from src.uia.group_helper import invite_friend_to_group

    tag_name = action.get("tag_name")
    group_name = action.get("group_name")
    join_method = action.get("join_method", "qrcode")
    qrcode_path = action.get("qrcode_path", "")
    tpl_fail = action.get("invite_fail_reply", "")

    # 将物理打标与物理拉群整合为一个原子的同步物理操作，用 UIA 物理排他锁全局保护
    def _run_physical_sequence():
        from src.utils.uia_task_runner import run_uia_task
        from src.crm.account_data import get_account_settings
        settings = get_account_settings(account_id)
        auto_tag_enabled = settings.get("reply", {}).get("auto_tag_enabled", True)

        with run_uia_task(f"身份引导物理打标及拉群→{session_name}", priority=UIATaskPriority.NORMAL, use_physical_lock=True):
            # 1. 物理同步标签到微信客户端
            if tag_name and auto_tag_enabled:
                logger.info(f"[身份引导] 准备物理同步标签【{tag_name}】到微信客户端")
                engine.driver.apply_remark_and_tags_from_chat(session_name, None, [tag_name])
                logger.info(f"[身份引导] 微信客户端物理打标完成")
            
            # 2. 如果是拉群模式，执行物理拉群
            if group_name and join_method == "invite":
                logger.info(f"[身份引导] 准备拉好友 {session_name} 入群 {group_name}")
                return invite_friend_to_group(engine.driver, group_name, session_name)
            return True

    success = False
    try:
        success = await run_uia_with_timeout(_run_physical_sequence, 45.0)
    except Exception as e:
        logger.error(f"[身份引导] 物理打标与拉群执行异常: {e}", exc_info=True)

    if group_name and join_method == "invite" and not success:
        logger.warning(f"[身份引导] 拉好友 {session_name} 入群 {group_name} 失败，执行降级发送")
        fallback_reply = ""
        downloaded_paths = []
        from .identity_flow import resolve_qrcode_path
        real_qrcode_path = resolve_qrcode_path(qrcode_path)
        if real_qrcode_path:
            fallback_reply = f"专属交流群【{group_name}】邀请拉取受限，已为您发送专属交流群二维码，您可以扫码加入哦~"
            downloaded_paths = [real_qrcode_path]
        elif tpl_fail.strip():
            fallback_reply = tpl_fail.replace("{tag_name}", tag_name or "").replace("{group_name}", group_name)

        if fallback_reply.strip() or downloaded_paths:
            try:
                from .reply_helper import dispatch_reply_messages
                await dispatch_reply_messages(
                    engine.driver, session_name, [fallback_reply] if fallback_reply else [], downloaded_paths, False, False
                )
            except Exception as send_ex:
                logger.error(f"[身份引导] 发送失败降级消息异常: {send_ex}")
