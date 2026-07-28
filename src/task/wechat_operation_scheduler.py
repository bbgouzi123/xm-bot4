import time
import random
import heapq
import logging
import asyncio
from datetime import datetime, timedelta
from enum import IntEnum
from typing import Callable, Coroutine, Any, List, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

class WeChatPriority(IntEnum):
    BACKGROUND = 1   # 朋友圈点赞/发帖等后台交互
    LOW = 2          # 批量消息群发等容忍度高的延迟任务
    MEDIUM = 3       # SDR 自动跟单雷达等定期触达
    HIGH = 4         # 业务承诺履约（发录屏、发白皮书等）
    IMMEDIATE = 5    # 紧急人工干预插队

class WeChatAction:
    def __init__(
        self,
        action_type: str,
        priority: WeChatPriority,
        execute_fn: Callable[[], Coroutine[Any, Any, Any]],
        target_wxid: str = "",
        expires_in_seconds: int = 600,
        require_uia: bool = True
    ):
        self.action_id = f"act_{int(time.time())}_{random.randint(1000, 9999)}"
        self.action_type = action_type
        self.priority = priority
        self.execute_fn = execute_fn
        self.target_wxid = target_wxid
        self.created_at = datetime.now()
        self.expires_at = self.created_at + timedelta(seconds=expires_in_seconds)
        self.require_uia = require_uia
        self.done_event = asyncio.Event()
        self.result = {"success": False, "error_msg": ""}

    def __lt__(self, other: "WeChatAction") -> bool:
        # 相同优先级时，按创建时间先来后到排序
        return self.created_at < other.created_at


class WeChatUnifiedScheduler:
    _instance: Optional["WeChatUnifiedScheduler"] = None
    _lock = asyncio.Lock()

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(WeChatUnifiedScheduler, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._queue: List[Tuple[Tuple[int, float], WeChatAction]] = []  # min-heap 队列
        self._queue_lock = asyncio.Lock()
        self._running = False
        self._last_action_time = 0.0
        self._initialized = True
        self._loop_task: Optional[asyncio.Task] = None

    async def start(self):
        """拉起调度器常驻监听消费循环"""
        async with self._queue_lock:
            if self._running:
                return
            self._running = True
            self._loop_task = asyncio.create_task(self._scheduler_loop())
            logger.info("[微信统一调度器] 调度引擎已成功拉起，开始接收微信操作流")

    async def stop(self):
        """停止调度器"""
        async with self._queue_lock:
            self._running = False
            if self._loop_task:
                self._loop_task.cancel()
                self._loop_task = None
            logger.info("[微信统一调度器] 调度引擎已安全挂起停止")

    async def submit(self, action: WeChatAction) -> str:
        """投递一个动作到排队队列"""
        # 计算优先级权重：优先级IntEnum越大越优先，所以在最小堆里使用 (6 - priority) 越小越排前
        priority_weight = 6 - int(action.priority)
        created_ts = action.created_at.timestamp()
        
        async with self._queue_lock:
            heapq.heappush(self._queue, ((priority_weight, created_ts), action))
            
        logger.info(
            f"[微信统一调度] 投递新动作: id={action.action_id}, "
            f"type={action.action_type}, priority={action.priority.name}, target={action.target_wxid}"
        )
        return action.action_id

    def get_queue_length(self) -> int:
        """获取当前排队中的动作数量"""
        return len(self._queue)

    async def _scheduler_loop(self):
        """常驻消费循环"""
        while self._running:
            try:
                if not self._queue:
                    await asyncio.sleep(0.5)
                    continue

                # 1. 弹出优先级最高、最早进来的 Action
                async with self._queue_lock:
                    if not self._queue:
                        continue
                    weight, action = heapq.heappop(self._queue)

                # 2. 校验是否超时过期
                if datetime.now() > action.expires_at:
                    logger.warning(
                        f"[微信统一调度] 动作已超时熔断: id={action.action_id}, "
                        f"type={action.action_type}, target={action.target_wxid}"
                    )
                    action.result["success"] = False
                    action.result["error_msg"] = "动作在队列中等待超时熔断"
                    action.done_event.set()
                    continue

                # 3. 如果需要占有 UIA 前台，配合全局物理锁避让
                if action.require_uia:
                    from src.utils.uia_circuit_breaker import is_engine_suspended
                    # 如果微信被暂停或人工接管，则将任务放回队列稍后重试
                    if is_engine_suspended():
                        logger.info(f"[微信统一调度] 物理引擎正处于人工挂起状态，将任务放回队列重排: {action.action_id}")
                        async with self._queue_lock:
                            heapq.heappush(self._queue, (weight, action))
                        await asyncio.sleep(2.0)
                        continue

                # 4. 强制执行拟人呼吸冷却节拍（随机 4.0 到 8.0 秒）
                elapsed = time.time() - self._last_action_time
                required_cooldown = random.uniform(4.0, 8.0)
                if elapsed < required_cooldown:
                    cooldown_sleep = required_cooldown - elapsed
                    logger.debug(f"[微信统一调度] 拟人呼吸冷却，等待 {cooldown_sleep:.2f} 秒后执行下一动作")
                    await asyncio.sleep(cooldown_sleep)

                # 5. 执行操作
                logger.info(f"[微信统一调度] 🚀 开始执行动作: id={action.action_id}, type={action.action_type}")
                try:
                    if action.require_uia:
                        from src.utils.uia_task_runner import uia_maintenance
                        with uia_maintenance(f"action_{action.action_type}"):
                            await action.execute_fn()
                    else:
                        await action.execute_fn()
                    action.result["success"] = True
                    logger.info(f"[微信统一调度] ✅ 动作成功履约: id={action.action_id}")
                except Exception as exec_err:
                    action.result["success"] = False
                    action.result["error_msg"] = str(exec_err)
                    logger.error(f"[微信统一调度] ❌ 动作执行内部异常: id={action.action_id}, err={exec_err}", exc_info=True)
                finally:
                    action.done_event.set()
                
                # 6. 更新最后一次前台操作时间戳
                self._last_action_time = time.time()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[微信统一调度] 调度循环遭遇异常: {e}", exc_info=True)
                await asyncio.sleep(1.0)


_global_wechat_scheduler: Optional[WeChatUnifiedScheduler] = None
_wechat_scheduler_lock = asyncio.Lock()

async def get_wechat_scheduler() -> WeChatUnifiedScheduler:
    """异步获取全局微信动作统一调度器实例，并保证其启动"""
    global _global_wechat_scheduler
    if _global_wechat_scheduler is None:
        async with _wechat_scheduler_lock:
            if _global_wechat_scheduler is None:
                _global_wechat_scheduler = WeChatUnifiedScheduler()
                await _global_wechat_scheduler.start()
    else:
        # 🛡️ 额外容错：若调度器曾被中断停止，则重新激活拉起监听消费循环，防止投递任务永久挂起
        if not _global_wechat_scheduler._running:
            await _global_wechat_scheduler.start()
    return _global_wechat_scheduler
