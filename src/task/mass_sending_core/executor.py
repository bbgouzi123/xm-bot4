import asyncio
import logging
import random
from datetime import datetime
from .helpers import download_and_send_media, execute_script_group

logger = logging.getLogger(__name__)

class MassSendingExecutorMixin:
    async def _execute_job(self, job_id: str):
        logger.info(f"[MassSendingCore] 开始执行群发任务 {job_id}...")
        
        if not self.driver:
            try:
                from app.state import account_manager
                self.driver = account_manager.primary_driver
            except Exception as drv_ex:
                logger.warning(f"[MassSendingCore] 自动获取微信驱动失败: {drv_ex}")

        self._db.update_mass_send_job(job_id, {"status": "processing"})
        
        try:
            from src.api.task_api import _active_tasks
            from src.utils.status_overlay import status_overlay
            all_items = self._db.get_mass_send_queues(job_id)
            total = len(all_items)
            sent_count = len([x for x in all_items if x["status"] in ("sent", "failed")])
            error_count = len([x for x in all_items if x["status"] == "failed"])
            
            _active_tasks[job_id] = {
                "status": "running",
                "total": total,
                "current": sent_count,
                "errors": error_count,
                "runs_detected": 0
            }
            status_overlay.update("准备群发", f"已准备 (进度: {sent_count}/{total})")
        except Exception as overlay_ex:
            logger.debug(f"[MassSendingCore] 初始化HUD/缓存状态失败: {overlay_ex}")

        min_delay = 8
        max_delay = 25
        
        items = self._db.get_mass_send_queues(job_id)
        pending_items = [x for x in items if x["status"] == "pending"]
        
        for idx, item in enumerate(pending_items):
            job = next((j for j in self._db.get_mass_send_jobs() if j["id"] == job_id), None)
            if not job or job.get("status") in ("cancelled", "completed"):
                break
                
            while job.get("status") == "paused":
                await asyncio.sleep(1)
                job = next((j for j in self._db.get_mass_send_jobs() if j["id"] == job_id), None)
                if not job or job.get("status") in ("cancelled", "completed"):
                    break
            
            if not job or job.get("status") in ("cancelled", "completed"):
                break

            target = item["friend_wxid"]
            content = job["content"]
            media_urls = job.get("media_urls", "")
            script_group_id = job.get("script_group_id")
            
            success = False
            err_msg = ""
            
            try:
                if self.driver and self.driver.is_connected():
                    from src.task.wechat_operation_scheduler import get_wechat_scheduler, WeChatAction, WeChatPriority
                    scheduler = await get_wechat_scheduler()

                    def check_cancelled():
                        _check = next((j for j in self._db.get_mass_send_jobs() if j["id"] == job_id), None)
                        return not _check or _check.get("status") in ("cancelled", "completed", "paused")

                    async def do_mass_send_to_target():
                        if script_group_id:
                            await execute_script_group(self._run_uia, self.driver, target, script_group_id, check_cancelled, self._db)
                        else:
                            if content and content.strip():
                                ok_msg = await self._run_uia(self.driver.send_message, target, content)
                                if not ok_msg:
                                    raise RuntimeError("UIA 发送普通文本消息返回 False")
                                
                            if media_urls:
                                await download_and_send_media(self._run_uia, self.driver, target, media_urls, check_cancelled)

                    action = WeChatAction(
                        action_type="mass_send",
                        priority=WeChatPriority.LOW,
                        execute_fn=do_mass_send_to_target,
                        target_wxid=target
                    )
                    await scheduler.submit(action)
                    await action.done_event.wait()
                    success = action.result["success"]
                    if not success:
                        err_msg = action.result["error_msg"]
                else:
                    err_msg = "微信驱动未连接"
            except Exception as e:
                logger.error(f"[MassSendingCore] 发送给 {target} 发生异常: {e}")
                err_msg = str(e)

            status = "sent" if success else "failed"
            self._db.update_mass_send_queue_item(item["id"], {
                "status": status,
                "sent_at": datetime.now().isoformat(),
                "error_msg": err_msg if not success else None
            })

            try:
                from src.api.task_api import _active_tasks
                from src.utils.status_overlay import status_overlay
                all_items = self._db.get_mass_send_queues(job_id)
                total = len(all_items)
                sent_count = len([x for x in all_items if x["status"] in ("sent", "failed")])
                error_count = len([x for x in all_items if x["status"] == "failed"])
                
                _active_tasks[job_id] = {
                    "status": "running",
                    "total": total,
                    "current": sent_count,
                    "errors": error_count,
                    "runs_detected": 0
                }
                status_overlay.update("群发中", f"进度: {sent_count}/{total} (失败: {error_count})", target)
            except Exception as overlay_ex:
                logger.debug(f"[MassSendingCore] 更新HUD/缓存状态失败: {overlay_ex}")

            try:
                from src.utils.websocket_manager import ws_manager
                asyncio.create_task(ws_manager.broadcast_json({
                    "type": "task_progress",
                    "task_id": job_id,
                    "progress": int(sent_count / total * 100) if total > 0 else 0,
                    "detail": f"已完成向 {target} 的群发投递"
                }))
            except Exception:
                pass

            from src.utils.stop_signal import stop_signal
            delay = random.uniform(min_delay, max_delay)
            logger.info(f"[MassSendingCore] 拟人防封安全静默延迟 {delay:.1f} 秒...")
            elapsed = 0.0
            while elapsed < delay:
                if stop_signal.is_stopped:
                    logger.info("[MassSendingCore] 检测到 ESC 停止信号，立即中断群发延迟")
                    self._db.update_mass_send_job(job_id, {"status": "cancelled"})
                    stop_signal.reset()
                    return
                step = min(0.5, delay - elapsed)
                await asyncio.sleep(step)
                elapsed += step
                _check_job = next((j for j in self._db.get_mass_send_jobs() if j["id"] == job_id), None)
                if not _check_job or _check_job.get("status") in ("cancelled", "completed"):
                    return

            pass

        job = next((j for j in self._db.get_mass_send_jobs() if j["id"] == job_id), None)
        final_status = "completed"
        if job and job.get("status") == "cancelled":
            final_status = "cancelled"
            
        self._db.update_mass_send_job(job_id, {"status": final_status})
        logger.info(f"[MassSendingCore] 群发任务 {job_id} 执行结束，最终状态: {final_status}")
        
        try:
            from src.api.task_api import _active_tasks
            from src.utils.status_overlay import status_overlay
            all_items = self._db.get_mass_send_queues(job_id)
            total = len(all_items)
            sent_count = len([x for x in all_items if x["status"] in ("sent", "failed")])
            error_count = len([x for x in all_items if x["status"] == "failed"])
            
            _active_tasks[job_id] = {
                "status": "completed",
                "total": total,
                "current": sent_count,
                "errors": error_count,
                "runs_detected": 0
            }
            if final_status == "completed":
                status_overlay.update("群发完成", f"已触达 {sent_count - error_count}/{total} 人")
            else:
                status_overlay.update("群发中断", f"已触达: {sent_count}/{total}")
        except Exception as overlay_ex:
            logger.debug(f"[MassSendingCore] 完成更新HUD/缓存状态失败: {overlay_ex}")

        try:
            from src.utils.websocket_manager import ws_manager
            asyncio.create_task(ws_manager.broadcast_json({
                "type": "task_progress",
                "task_id": job_id,
                "progress": 100 if final_status == "completed" else int(sent_count / total * 100) if total > 0 else 0,
                "detail": f"群发任务执行结束，状态: {final_status}"
            }))
        except Exception:
            pass

        if final_status == "completed":
            try:
                queues = self._db.get_mass_send_queues(job_id)
                total = len(queues)
                succeeded = len([x for x in queues if x["status"] == "sent"])
                from src.utils.alert_notifier import alert_notifier
                asyncio.create_task(alert_notifier.send_user_notification(
                    title="✅ 群发任务已完成",
                    body=f"群发任务已完成，共申请 {total} 人，成功 {succeeded} 人",
                    category="task"
                ))
            except Exception as ex:
                logger.error(f"[MassSendingCore] 发送群发完成通知异常: {ex}")
