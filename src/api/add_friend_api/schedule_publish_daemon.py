"""
独立排期自动发布守护协程

与 warmup_idle_daemon_loop 完全解耦：
- warmup 间隔 1.5~2.5h，若排期发送窗口太小会导致大概率错过
- 此守护协程每 60s 检查一次，确保排期在到期后 1 分钟内被执行
"""

import json
import asyncio
import logging

logger = logging.getLogger(__name__)

_schedule_publish_daemon_started = False


async def schedule_auto_publish_daemon_loop(driver):
    """独立的排期自动发布守护协程：每 60 秒检查是否有到期待发的排期。"""
    from src.utils.license_validator import LicenseValidator
    features = LicenseValidator.check_features()
    if not features.get("moments_auto", False):
        logger.debug("[排期发圈守护] 当前 License 不支持自动朋友圈功能，排期发布守护不执行。")
        return

    from .warmup_service import _resolve_media_paths

    logger.info("[排期发圈守护] 独立排期自动发布守护已启动，每 60 秒检查一次到期排期")
    while True:
        try:
            if driver and driver.is_connected():
                from src.crm.moment_planner_service import MomentPlannerService
                from src.crm.account_data import get_active_account
                import src.crm.moment_planner_service.state as mps_state
                from src.crm.moment_planner_service.bootstrap import expire_stale_pending_moments_and_collect_due

                account_id = get_active_account() or 'main'
                planner = MomentPlannerService(account_id)

                stale_count, pending_tasks = expire_stale_pending_moments_and_collect_due()
                if stale_count:
                    planner._sync_schedules_to_cloud()

                driver_wxid = getattr(driver, '_wxid', None) or getattr(driver, 'wxid', None)
                if driver_wxid:
                    # 严格隔离：只保留归属于当前驱动微信ID的排期任务！
                    pending_tasks = [t for t in pending_tasks if (t.get("bot_wxid") or "default") == driver_wxid]
                else:
                    pending_tasks = []

                if pending_tasks:
                    task = pending_tasks[0]
                    task_id = task["id"]

                    # 防重复执行：检查 _executed_ids
                    if task_id in mps_state._executed_ids:
                        await asyncio.sleep(60)
                        continue

                    logger.info(f"[排期发圈守护] 📍 发现到期排期 #{task_id}，正在执行自动发表...")

                    text = task["content_text"]
                    media_raw = task.get("media_urls", [])
                    if isinstance(media_raw, str):
                        try:
                            media_raw = json.loads(media_raw)
                        except Exception:
                            media_raw = []

                    local_paths = _resolve_media_paths(media_raw) if media_raw else None

                    success = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: driver.post_moment(text=text, image_paths=local_paths)
                    )

                    new_status = "published" if success else "failed"
                    with mps_state._schedule_lock:
                        for s in mps_state._schedules:
                            if s["id"] == task_id:
                                s["status"] = new_status
                                if not success:
                                    s["error_msg"] = "UIA 发表失败"
                                break
                        mps_state._executed_ids.add(task_id)
                    planner._sync_schedules_to_cloud()

                    if success:
                        logger.info(f"[排期发圈守护] ✅ 排期 #{task_id} 自动发表成功")
                        try:
                            from src.utils.alert_notifier import alert_notifier
                            asyncio.create_task(alert_notifier.send_user_notification(
                                title="✨ 朋友圈自动发布成功",
                                body=f"排期任务 #{task_id} 自动发表成功：{text[:50]}...",
                                category="task"
                            ))
                        except Exception:
                            pass
                    else:
                        logger.error(f"[排期发圈守护] ❌ 排期 #{task_id} 自动发表失败")
                        try:
                            from src.utils.alert_notifier import alert_notifier
                            asyncio.create_task(alert_notifier.send_user_notification(
                                title="❌ 朋友圈自动发布失败",
                                body=f"排期任务 #{task_id} 自动发表失败，请检查微信状态。文案：{text[:50]}...",
                                category="task"
                            ))
                        except Exception:
                            pass

            await asyncio.sleep(60)
        except Exception as e:
            logger.error(f"[排期发圈守护] 检查循环异常: {e}")
            await asyncio.sleep(60)


def ensure_schedule_publish_daemon_started(driver):
    """启动独立的排期自动发布守护协程（与养号守护独立）"""
    global _schedule_publish_daemon_started
    if _schedule_publish_daemon_started:
        return
    import app.state as app_state
    main_loop = getattr(app_state, 'main_loop', None)
    if main_loop and main_loop.is_running():
        asyncio.run_coroutine_threadsafe(schedule_auto_publish_daemon_loop(driver), main_loop)
        _schedule_publish_daemon_started = True
        logger.info("[排期发圈守护] 已通过 main_loop 启动")
    else:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(schedule_auto_publish_daemon_loop(driver))
            _schedule_publish_daemon_started = True
            logger.info("[排期发圈守护] 已通过 running_loop 启动")
        except RuntimeError:
            try:
                loop = asyncio.get_event_loop()
                loop.create_task(schedule_auto_publish_daemon_loop(driver))
                _schedule_publish_daemon_started = True
            except Exception as e:
                logger.error(f"[排期发圈守护] 启动失败: {e}")
