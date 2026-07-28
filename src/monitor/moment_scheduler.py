"""
朋友圈排期调度器 — 自动发送引擎

在独立的 asyncio Task 中运行，每 60 秒巡检一次 moment_schedules 表，
找到已到时间且状态为 pending 的排期，通过全局 UIA 互斥锁安全地调用发圈组件。

任务优先级策略：
- 朋友圈发送使用 LOW 优先级
- 如果此刻有聊天自动回复（NORMAL优先级）正在执行，调度器会等待其完成
- 如果获取锁超时（30秒），本轮跳过，下次巡检再重试
- 用户手动操作（HIGH优先级）同理，发圈会让路

集成方式：
    from src.monitor.moment_scheduler import MomentScheduler
    scheduler = MomentScheduler(driver)
    await scheduler.start()
"""
import asyncio
import logging
import time

from src.utils.uia_lock import uia_lock, UIATaskPriority
from src.utils.rest_time import is_rest_time
from src.utils.daily_counter import DailyCounter
from src.utils.user_activity import is_user_active
from src.utils.uia_task_runner import (
    is_uia_maintenance_active,
    get_uia_maintenance_reason,
)

logger = logging.getLogger(__name__)

# 全局日计数器
_moment_daily_counter = DailyCounter()


class MomentScheduler:
    """朋友圈排期自动发送调度器"""

    def __init__(self, wechat_driver, check_interval: int = 60):
        """
        Args:
            wechat_driver: WeChatDriver 实例
            check_interval: 巡检间隔（秒），默认 60 秒
        """
        self.driver = wechat_driver
        self._check_interval = check_interval
        self._running = False
        self._task = None
        self._stats = {
            "checked": 0,
            "published": 0,
            "failed": 0,
            "skipped_lock": 0,
            "skipped_rest": 0,
            "skipped_maintenance": 0,
        }

    async def start(self):
        """启动调度器"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(f"[朋友圈调度] ✅ 已启动（每 {self._check_interval} 秒巡检）")

    async def stop(self):
        """停止调度器"""
        if not self._running:
            return
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[朋友圈调度] 已停止")

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "stats": self._stats.copy(),
            "uia_lock": uia_lock.get_status(),
        }

    async def _loop(self):
        """主巡检循环"""
        # 启动后等待 30 秒再开始首次巡检（给系统初始化留时间）
        await asyncio.sleep(30)

        while self._running:
            try:
                await self._check_and_execute()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[朋友圈调度] 巡检异常: {e}")
            await asyncio.sleep(self._check_interval)

    async def _check_and_execute(self):
        """核心巡检：查找到期任务并执行"""
        self._stats["checked"] += 1

        # 【防封守卫】休息时间不发圈
        account_id = getattr(self.driver, '_wxid', None) or 'main'
        if is_rest_time("moment_post", account_id):
            self._stats["skipped_rest"] += 1
            return

        # 【防封守卫】每日发圈次数限制
        if not _moment_daily_counter.can_do("moment_post", account_id):
            logger.info("[朋友圈调度] 今日发圈次数已达上限")
            return

        # 微信未连接则跳过
        if not self.driver.is_connected():
            return

        if is_uia_maintenance_active():
            self._stats["skipped_maintenance"] += 1
            reason = get_uia_maintenance_reason() or "UIA维护窗口"
            logger.info(f"[朋友圈调度] 维护窗口中，跳过本轮巡检: {reason}")
            return

        # 从数据库查找到期的 pending 任务
        from src.crm.account_data import get_active_account
        from src.crm.moment_planner_service import (
            MomentPlannerService,
            expire_stale_pending_moments_and_collect_due,
        )

        active_account = get_active_account()
        planner = MomentPlannerService(active_account)

        import src.crm.moment_planner_service as mps

        try:
            stale_count, pending_tasks = expire_stale_pending_moments_and_collect_due()
        except Exception as e:
            logger.error(f"[朋友圈调度] 查询待执行任务失败: {e}")
            return

        driver_wxid = getattr(self.driver, '_wxid', None) or getattr(self.driver, 'wxid', None)
        if not driver_wxid:
            logger.warning("[朋友圈调度] 驱动微信ID为空，跳过本轮巡检")
            return

        # 严格隔离：只保留归属于当前驱动微信ID的排期任务！
        pending_tasks = [t for t in pending_tasks if (t.get("bot_wxid") or "default") == driver_wxid]

        if stale_count:
            planner._sync_schedules_to_cloud()
            logger.info(f"[朋友圈调度] ⏭️ 已将 {stale_count} 条过期排期标记为失败（不再补发）")

        if not pending_tasks:
            return

        if is_user_active(check_caret=True):
            logger.info(f"[朋友圈调度] 用户正在使用键鼠或输入框，本轮跳过发圈 (wxid: {driver_wxid})")
            return

        # 每轮最多执行 1 条，与日历「到点即发」对齐，避免积压_burst
        pending_tasks = pending_tasks[:1]

        logger.info(f"[朋友圈调度] 🎯 发现 1 条归属于 {driver_wxid} 的到期排期（投递窗口内），准备执行...")

        import json

        for task in pending_tasks:
            if not self._running:
                break

            task_id = task["id"]
            text = task["content_text"]

            media_raw = task.get("media_urls", [])
            if isinstance(media_raw, str):
                try:
                    media_urls = json.loads(media_raw)
                except Exception:
                    media_urls = []
            else:
                media_urls = media_raw

            logger.info(f"[朋友圈调度] 🚀 准备排期#{task_id}: \"{text[:30]}...\"")

            try:
                bus_success = await self._submit_via_uibus(
                    task_id, text, media_urls, account_id,
                )
            except Exception as e:
                logger.warning(f"[朋友圈调度] UIBus 提交异常，回落旧路径: {e}")
                bus_success = None

            if bus_success is None:
                # UIBus 不可用：回落到原 uia_lock 持锁路径，保底兼容
                if not uia_lock.try_acquire(
                    task_name=f"朋友圈排期#{task_id}",
                    priority=UIATaskPriority.LOW,
                ):
                    current_owner = uia_lock.current_task
                    logger.info(
                        f"[朋友圈调度] ⏳ UIA 忙（{current_owner}），"
                        f"排期#{task_id} 延后执行"
                    )
                    self._stats["skipped_lock"] += 1
                    continue
                try:
                    success = await self._execute_post(text, media_urls)
                except Exception as e:
                    logger.error(f"[朋友圈调度] 排期#{task_id} 执行异常: {e}")
                    success = False
                finally:
                    uia_lock.release()
            else:
                success = bool(bus_success)

            # 更新内存和同步后端状态（与原来一致）
            mps._executed_ids.add(task_id)
            with mps._schedule_lock:
                for s in mps._schedules:
                    if s["id"] == task_id:
                        if success:
                            s["status"] = 'published'
                            self._stats["published"] += 1
                            _moment_daily_counter.increment(
                                "moment_post", account_id,
                            )
                            logger.info(f"[朋友圈调度] ✅ 排期#{task_id} 发送成功！")
                        else:
                            s["status"] = 'failed'
                            self._stats["failed"] += 1
                            logger.info(f"[朋友圈调度] ❌ 排期#{task_id} 发送失败")
                        break
            try:
                planner._sync_schedules_to_cloud()
            except Exception as e:
                logger.warning(f"[朋友圈调度] 同步服务失败: {e}")

            # UIBus 已有账号级节流器做拟人间隔；这里仍兜底 sleep 5s，
            # 避免单轮连发（按 pending_tasks[:1] 切片，实际基本不会走这里）。
            if self._running:
                await asyncio.sleep(5)

    async def _submit_via_uibus(
        self,
        task_id: int,
        text: str,
        media_urls: list,
        account_id: str,
    ):
        """把本轮发圈提交到 UIBus；成功/失败返回 bool，UIBus 不可用返回 None。

        UIBus 模式下：
        - 命令在 worker 线程串行执行，worker 自动持 ``uia_lock``
        - 账号级节流器按 ``moment_post`` 维度做日配额熔断和拟人间隔
        - 前端驾驶舱能看到本条任务的排队/执行/完成事件
        """
        try:
            from src.orchestrator import (
                ui_bus, UICommand, UICommandKind, UICommandPriority,
            )
        except Exception as e:
            logger.debug(f"[朋友圈调度] UIBus 不可用: {e}")
            return None

        def _exec_sync(_cmd) -> bool:
            return self._run_post_sync(text, media_urls)

        cmd = UICommand(
            wxid=account_id or "",
            kind=UICommandKind.PUBLISH_MOMENT,
            payload={
                "fn": _exec_sync,
                "text": text,
                "image_paths": media_urls,
                "task_id": task_id,
                "source": "moment_scheduler",
            },
            priority=UICommandPriority.LOW,
            timeout=180.0,
        )
        try:
            cmd_id = ui_bus.submit(cmd)
        except Exception as e:
            logger.warning(f"[朋友圈调度] UIBus.submit 失败: {e}")
            return None

        loop = asyncio.get_event_loop()
        # await_result 会阻塞，塞进线程池等；上限给到 200s（handler 180s + buffer）
        try:
            result_cmd = await loop.run_in_executor(
                None, ui_bus.await_result, cmd_id, 200.0,
            )
        except Exception as e:
            logger.warning(f"[朋友圈调度] 等待 UIBus 结果异常: {e}")
            return None

        status = getattr(result_cmd.status, "value", str(result_cmd.status))
        if status == "success":
            return bool(result_cmd.result)
        if status == "canceled":
            logger.info(f"[朋友圈调度] 排期#{task_id} 已被取消")
            return False
        logger.warning(
            f"[朋友圈调度] UIBus 发圈#{task_id} status={status} "
            f"err={result_cmd.error}"
        )
        return False

    def _run_post_sync(self, text: str, media_urls: list) -> bool:
        """同步版发圈引擎（供 UIBus handler 在 worker 线程里直接调）。"""
        try:
            from src.monitor.moment_post import MomentPost
            poster = MomentPost(self.driver)
            local_paths = self._resolve_media_paths(media_urls) if media_urls else []
            if local_paths:
                result = poster.publish_with_images(text, local_paths)
            else:
                result = poster.publish_text(text)
            return bool(result and result.get("success", False))
        except Exception as e:
            logger.error(f"[朋友圈调度] 发圈物理引擎异常: {e}")
            return False

    async def _execute_post(self, text: str, media_urls: list) -> bool:
        """调用发圈组件执行实际发送
        
        此方法在已持有 UIA 锁的情况下被调用。
        """
        try:
            from src.monitor.moment_post import MomentPost
            poster = MomentPost(self.driver)

            # 解析图片路径（网络URL需要下载到本地临时文件）
            local_paths = self._resolve_media_paths(media_urls) if media_urls else []

            from src.utils.uia_task_runner import run_uia_with_timeout
            if local_paths:
                result = await run_uia_with_timeout(
                    poster.publish_with_images, 120.0, text, local_paths
                )
            else:
                result = await run_uia_with_timeout(
                    poster.publish_text, 90.0, text
                )

            return result and result.get("success", False)

        except Exception as e:
            logger.error(f"[朋友圈调度] 发圈物理引擎异常: {e}")
            return False


    @staticmethod
    def _resolve_media_paths(urls: list) -> list:
        """将各种格式的图片 URL 统一解析为本地文件路径（委托到 moment_media 模块）"""
        from src.monitor.moment_media import resolve_media_paths
        return resolve_media_paths(urls)
