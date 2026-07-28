"""
reply_gateway_guard.py
──────────────────────
通用决策委派网关的前置拦截适配器。
负责：
  1. Level-3 自愈：顾客发来新消息时自动释放超时悬挂任务
  2. 冻结：顾客仍有活跃 Pending 任务则继续阻断 AI 回复
  3. 意图分类：命中高危/高价值词后，异步提交审批 + 发占位话术 + 阻断 AI
单独封装以保持 reply_preconditions.py 不超 300 行有效代码。
"""
import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def run_gateway_guard(
    engine: Any,
    name: str,
    message: str,
    wxid: str,
    account_id: str,
    task_id: str,
    gateway_admin: str,
    is_group: bool,
    skip_fn,          # _skip_and_notify 的引用，避免循环导入
) -> bool:
    """
    执行第四道防线（通用意图分类网关）。
    返回 True 表示本次消息已被网关接管，调用方应 return False 阻断后续 AI 回复。
    返回 False 表示普通消息，允许继续正常的 AI 回复流程。
    """
    try:
        from .intent_classifier import classify_intent, IntentLevel
        from .decision_gateway import submit_decision, release_friend_lock, has_pending_decision

        # Level 3 自愈：顾客发来新消息自动释放超时悬挂任务
        release_friend_lock(wxid or name)

        # 若顾客仍有活跃 Pending 任务，冻结本次 AI 回复
        if has_pending_decision(wxid or name):
            logger.info(f"[决策网关] 顾客 '{name}' 有活跃待审批任务，冻结本次 AI 回复")
            await skip_fn(engine, task_id, name, message, "决策网关：等待管理员审批，暂停本次 AI 回复")
            return True

        # 非群聊 + 绑定了管理员才做意图分类
        if not gateway_admin or is_group:
            return False

        intent = classify_intent(account_id, message, is_group=False)
        if intent.level == IntentLevel.NORMAL:
            return False

        logger.info(
            f"[决策网关] 顾客 '{name}' 意图={intent.level.value}, "
            f"命中词='{intent.matched_keyword}'，异步提交审批"
        )
        asyncio.create_task(submit_decision(
            engine=engine,
            admin_wxid=gateway_admin,
            friend_wxid=wxid or name,
            friend_name=name,
            original_message=message,
            intent_type=intent.level.value,
            request_card_text=intent.build_request_card(name, message),
            placeholder_reply=intent.placeholder_reply,
            fallback_reply=intent.fallback_reply,
            approve_action=None,
            timeout_secs=60.0,
        ))
        # 立即给顾客发占位安抚话术
        try:
            await engine.driver.SendMsg(wxid or name, intent.placeholder_reply)
        except Exception:
            pass
        await skip_fn(engine, task_id, name, message, f"决策网关：意图 [{intent.level.value}] 已提交管理员审批")
        return True

    except Exception as e:
        logger.warning(f"[决策网关] 意图分类网关异常（已降级跳过）: {e}")
        return False
