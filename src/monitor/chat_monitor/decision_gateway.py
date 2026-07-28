"""
通用决策委派网关（Universal Decision Gateway）
────────────────────────────────────────────
核心能力：
  1. 异步非阻塞：submit_decision 立即返回，不卡任何会话
  2. 三级超时自愈：60s 无响应 → 降级话术 + WebSocket 广播 + 顾客续聊解锁
  3. 统一管理员回复路由：dispatch_admin_reply 匹配「同意/拒绝/自定义」并执行回调
  4. 顾客新消息自动解锁：若顾客在等待期再发消息，自动撤销旧 Pending 任务
────────────────────────────────────────────
"""
import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────── 数据结构 ────────────────────────────────

@dataclass
class PendingDecision:
    task_id: str
    friend_wxid: str
    friend_name: str
    original_message: str
    intent_type: str
    placeholder_reply: str       # 已发给顾客的占位话术
    fallback_reply: str          # 超时后给顾客的降级话术
    approve_action: Optional[Callable[[], Coroutine]]  # 同意时执行
    reject_action: Optional[Callable[[], Coroutine]]   # 拒绝时执行（可空）
    created_at: float = field(default_factory=time.time)
    timeout_handle: Optional[asyncio.Task] = field(default=None, repr=False)


# { admin_wxid -> list[PendingDecision] }
_PENDING: dict[str, list[PendingDecision]] = {}

# { friend_wxid -> admin_wxid }（快速反向查找，用于顾客新消息解锁）
_FRIEND_TO_ADMIN: dict[str, str] = {}

# 同意关键词
_APPROVE_KEYWORDS = {"同意", "同意加群", "同意加入", "批准", "可以", "ok", "OK", "确认", "通过",
                     "已处理", "继续ai", "继续AI", "同意发", "同意回复"}
# 拒绝关键词
_REJECT_KEYWORDS = {"拒绝", "不同意", "不行", "算了", "取消", "不加", "不发", "不需要"}


# ─────────────────────────────── 内部工具函数 ─────────────────────────────

def _find_pending(admin_wxid: str, *, task_id: str = None, friend_wxid: str = None
                  ) -> Optional[PendingDecision]:
    for p in _PENDING.get(admin_wxid, []):
        if task_id and p.task_id == task_id:
            return p
        if friend_wxid and p.friend_wxid == friend_wxid:
            return p
    return None


def _remove_pending(admin_wxid: str, task_id: str) -> Optional[PendingDecision]:
    bucket = _PENDING.get(admin_wxid, [])
    for i, p in enumerate(bucket):
        if p.task_id == task_id:
            bucket.pop(i)
            _FRIEND_TO_ADMIN.pop(p.friend_wxid, None)
            if p.timeout_handle and not p.timeout_handle.done():
                p.timeout_handle.cancel()
            return p
    return None


async def _notify_websocket(intent_type: str, friend_name: str,
                             message: str, expired: bool = False):
    try:
        from src.utils.websocket_manager import ws_manager
        await ws_manager.broadcast({
            "type": "decision_gateway_alert",
            "intent_type": intent_type,
            "friend_name": friend_name,
            "original_message": message[:300],
            "expired": expired,
            "timestamp": time.time(),
        })
    except Exception as e:
        logger.warning(f"[决策网关] WebSocket 广播失败: {e}")


# ─────────────────────────────── 超时自愈 ────────────────────────────────

async def _timeout_watchdog(engine: Any, admin_wxid: str,
                             task_id: str, timeout_secs: float):
    """60s 后若任务仍在队列中，触发三级自愈降级"""
    try:
        await asyncio.sleep(timeout_secs)
    except asyncio.CancelledError:
        return  # 管理员已处理，定时器被取消

    pending = _remove_pending(admin_wxid, task_id)
    if not pending:
        return  # 已被处理或顾客新消息解锁

    logger.warning(
        f"[决策网关] ⏰ 超时未处理！任务={task_id}, 顾客={pending.friend_name}, "
        f"意图={pending.intent_type}"
    )

    # Level 1：给顾客发降级安抚话术
    try:
        await engine.driver.SendMsg(pending.friend_wxid, pending.fallback_reply)
        logger.info(f"[决策网关] Level1 降级话术已发给顾客 {pending.friend_name}")
    except Exception as e:
        logger.error(f"[决策网关] Level1 降级话术发送失败: {e}")

    # Level 2：WebSocket 广播给 PC 管理端置顶告警
    await _notify_websocket(
        intent_type=pending.intent_type,
        friend_name=pending.friend_name,
        message=pending.original_message,
        expired=True,
    )

    # Level 3 在 release_friend_lock 中实现：顾客下次发消息自动重新进入正常 AI 回复


# ─────────────────────────────── 核心 API ────────────────────────────────

async def submit_decision(
    engine: Any,
    admin_wxid: str,
    friend_wxid: str,
    friend_name: str,
    original_message: str,
    intent_type: str,
    request_card_text: str,
    placeholder_reply: str,
    fallback_reply: str,
    approve_action: Optional[Callable[[], Coroutine]] = None,
    reject_action: Optional[Callable[[], Coroutine]] = None,
    timeout_secs: float = 60.0,
) -> str:
    """
    提交一个决策任务到网关。
    ① 立即给顾客发占位话术
    ② 给管理员发请示卡片
    ③ 启动超时定时器
    返回 task_id
    """
    task_id = f"dg_{friend_wxid}_{int(time.time())}_{uuid.uuid4().hex[:6]}"

    # ① 如果该顾客已有 Pending 任务，先清除（避免重复累积）
    if friend_wxid in _FRIEND_TO_ADMIN:
        old_admin = _FRIEND_TO_ADMIN[friend_wxid]
        old_bucket = _PENDING.get(old_admin, [])
        stale = [p for p in old_bucket if p.friend_wxid == friend_wxid]
        for s in stale:
            _remove_pending(old_admin, s.task_id)
            logger.info(f"[决策网关] 清除旧任务 {s.task_id}（顾客 {friend_name} 发来新触发）")

    # ② 注册新任务
    timeout_task = asyncio.create_task(
        _timeout_watchdog(engine, admin_wxid, task_id, timeout_secs)
    )
    pending = PendingDecision(
        task_id=task_id,
        friend_wxid=friend_wxid,
        friend_name=friend_name,
        original_message=original_message,
        intent_type=intent_type,
        placeholder_reply=placeholder_reply,
        fallback_reply=fallback_reply,
        approve_action=approve_action,
        reject_action=reject_action,
        timeout_handle=timeout_task,
    )
    _PENDING.setdefault(admin_wxid, []).append(pending)
    _FRIEND_TO_ADMIN[friend_wxid] = admin_wxid

    # ③ 给管理员发请示卡片
    try:
        await engine.driver.SendMsg(admin_wxid, request_card_text)
        logger.info(f"[决策网关] 请示已发给管理员 {admin_wxid}，任务={task_id}")
    except Exception as e:
        logger.error(f"[决策网关] 发送请示给管理员失败: {e}")

    # ④ WebSocket 广播（实时更新 PC 管理端待审批徽章）
    await _notify_websocket(intent_type, friend_name, original_message, expired=False)

    return task_id


async def dispatch_admin_reply(engine: Any, admin_wxid: str, reply_text: str) -> bool:
    """
    处理管理员的回复文本，匹配待审批队列中的任务并执行。
    返回 True 表示成功命中并处理了某个 Pending 任务。
    """
    bucket = _PENDING.get(admin_wxid, [])
    if not bucket:
        return False

    reply_stripped = reply_text.strip()

    # 清理 30 分钟以上的过期任务
    now = time.time()
    expired_ids = [p.task_id for p in bucket if now - p.created_at > 1800]
    for tid in expired_ids:
        _remove_pending(admin_wxid, tid)

    # 刷新 bucket 引用
    bucket = _PENDING.get(admin_wxid, [])
    if not bucket:
        return False

    # 优先取最新的一条 Pending（栈顶逻辑，符合直觉）
    latest = bucket[-1]

    is_approve = reply_stripped in _APPROVE_KEYWORDS
    is_reject = reply_stripped in _REJECT_KEYWORDS

    if not (is_approve or is_reject):
        return False

    pending = _remove_pending(admin_wxid, latest.task_id)
    if not pending:
        return False

    if is_approve:
        logger.info(f"[决策网关] ✅ 管理员批准任务={pending.task_id}, 顾客={pending.friend_name}")
        try:
            await engine.driver.SendMsg(admin_wxid, f"✅ 已收到批准，正在为顾客「{pending.friend_name}」执行操作...")
        except Exception:
            pass
        if pending.approve_action:
            try:
                await pending.approve_action()
            except Exception as e:
                logger.error(f"[决策网关] approve_action 执行失败: {e}")
                try:
                    await engine.driver.SendMsg(admin_wxid, f"⚠️ 操作执行失败：{e}")
                except Exception:
                    pass
    else:
        logger.info(f"[决策网关] ❌ 管理员拒绝任务={pending.task_id}, 顾客={pending.friend_name}")
        try:
            await engine.driver.SendMsg(admin_wxid, f"🚫 已记录，将不对顾客「{pending.friend_name}」执行该操作。")
        except Exception:
            pass
        if pending.reject_action:
            try:
                await pending.reject_action()
            except Exception as e:
                logger.error(f"[决策网关] reject_action 执行失败: {e}")

    return True


def release_friend_lock(friend_wxid: str):
    """
    当顾客发来新消息时调用此函数（Level 3 自愈）：
    若该顾客有 Pending 决策且超时已过，自动释放会话锁，
    让新消息重新走完整的 AI 回复流程。
    """
    if friend_wxid not in _FRIEND_TO_ADMIN:
        return
    admin_wxid = _FRIEND_TO_ADMIN[friend_wxid]
    pending = _find_pending(admin_wxid, friend_wxid=friend_wxid)
    if pending:
        now = time.time()
        # 只有超时 30 秒以上的任务才在新消息时自动释放（避免误取消刚提交的任务）
        if now - pending.created_at > 30:
            _remove_pending(admin_wxid, pending.task_id)
            logger.info(
                f"[决策网关] Level3 自愈：顾客 {friend_wxid} 发来新消息，"
                f"释放超时任务 {pending.task_id}"
            )


def has_pending_decision(friend_wxid: str) -> bool:
    """检查某顾客是否有悬挂中的决策任务（用于 reply_preconditions 冻结判断）"""
    if friend_wxid not in _FRIEND_TO_ADMIN:
        return False
    admin_wxid = _FRIEND_TO_ADMIN[friend_wxid]
    return _find_pending(admin_wxid, friend_wxid=friend_wxid) is not None


def get_pending_summary(admin_wxid: str) -> str:
    """返回当前待审批队列的摘要文本（供「数据」口令调用）"""
    bucket = _PENDING.get(admin_wxid, [])
    if not bucket:
        return "当前无待审批任务 ✅"
    lines = [f"📋 当前待审批任务（共 {len(bucket)} 条）："]
    for i, p in enumerate(bucket[-5:], 1):
        wait_secs = int(time.time() - p.created_at)
        lines.append(f"  {i}. [{p.intent_type}] {p.friend_name}（等待 {wait_secs}s）")
    if len(bucket) > 5:
        lines.append(f"  ...（还有 {len(bucket) - 5} 条）")
    return "\n".join(lines)
