import asyncio
import logging
import random
import time
from src.friend import friend_queue
from .helpers import (
    _get_wait_time,
    _generate_remark,
    _merge_tags,
    _generate_verify_message,
    _sync_to_crm,
    _sync_cloud,
    _wait_for_pending_replies_if_any,
    _validate_mobile_if_enabled,
    _trigger_risk_alert_if_frequent,
    _trigger_exception_alert
)

logger = logging.getLogger(__name__)

from .task_state_manager import (
    _task_state,
    save_task_state_to_db,
    try_restore_task_state,
    _get_effective_driver,
    init_driver
)

async def _perform_warmup_actions():
    from .warmup_service import perform_warmup_actions
    await perform_warmup_actions(_driver, _task_state)

async def _run_add_friend_loop():
    config = _task_state["config"]
    max_per_day = config.get("max_friends_per_day", 15)
    interval_cfg = config.get("interval_minutes", 3.0)
    batch_size = config.get("batch_size", 3)
    active_warmup = config.get("active_warmup", False)
    added_since_warmup, warmup_threshold, last_idle_warmup_time = 0, random.randint(5, 8), 0

    logger.info(f"[一键加人] 任务循环启动 config={config}")

    while _task_state["running"]:
        if _task_state["paused"]:
            logger.info("[一键加人] 任务处于挂起/暂停状态，等待 5 秒后重试...")
            await asyncio.sleep(5)
            continue
        
        drv = _get_effective_driver()
        logger.info(f"[一键加人] 当前活动微信驱动: {drv}, 是否已连接微信: {drv.is_connected() if drv else False}")
        
        await _wait_for_pending_replies_if_any(drv, _task_state)
        if not _task_state["running"]:
            logger.info("[一键加人] 任务已被停止")
            break
        if _task_state["paused"]:
            logger.info("[一键加人] 任务由于消息流优先礼让已挂起")
            continue

        # 实时加载企业最新的硬性每日加人上限进行风控熔断卡点
        effective_max = max_per_day
        try:
            from src.api.config_api.base_config import _load_configs
            global_configs = _load_configs() or {}
            enterprise_limit = global_configs.get("add_friend_daily_limit")
            if enterprise_limit is not None and isinstance(enterprise_limit, int) and enterprise_limit > 0:
                effective_max = min(max_per_day, enterprise_limit)
        except Exception as ex:
            logger.warning(f"[一键加人] 实时加载企业限额配置异常: {ex}")

        today_count = friend_queue.get_today_count()
        logger.info(f"[一键加人] 今日已添加: {today_count} 人, 本地/企业上限: {effective_max} 人")
        if today_count >= effective_max:
            if active_warmup:
                now = time.time()
                if now - last_idle_warmup_time >= 7200:
                    logger.info("[一键托管] 今日主动加人已达上限，进入静默空闲养号模式...")
                    await _perform_warmup_actions()
                    last_idle_warmup_time = now
            logger.info("[一键加人] 今日添加人数已达上限，任务进入安全休眠状态 (300秒后重新评估)...")
            await asyncio.sleep(300)
            continue

        import_batch_id_filter = config.get("import_batch_id", "")
        logger.info(f"[一键加人] 查询待执行队列中... 批次筛选: {import_batch_id_filter}")
        pending = friend_queue.get_pending(
            limit=batch_size,
            include_failed=config.get("retry_failed", False),
            include_unknown=config.get("retry_unknown", False),
            skip_processing=config.get("skip_processing", True),
            industry_profile_id=config.get("industry_profile_id", ""),
            tag=config.get("tag_filter", ""),
            import_batch_id=import_batch_id_filter,
        )
        # 【修复】批次筛选降级：若按指定批次 ID 筛选后队列为空，但全局队列有待处理记录，
        # 则自动降级为忽略批次 ID 过滤（兼容历史数据和批次 ID 不匹配的旧记录）
        if not pending and import_batch_id_filter:
            global_stats = friend_queue.get_queue_stats()
            global_pending = global_stats.get("pending", 0)
            if global_pending > 0:
                logger.warning(
                    f"[一键加人] 按批次 '{import_batch_id_filter}' 筛选结果为空，"
                    f"但全局队列有 {global_pending} 条待处理记录。"
                    f"自动降级为忽略批次过滤，兼容历史数据..."
                )
                pending = friend_queue.get_pending(
                    limit=batch_size,
                    include_failed=config.get("retry_failed", False),
                    include_unknown=config.get("retry_unknown", False),
                    skip_processing=config.get("skip_processing", True),
                    industry_profile_id=config.get("industry_profile_id", ""),
                    tag=config.get("tag_filter", ""),
                    import_batch_id="",  # 忽略批次过滤
                )
        if not pending:
            logger.info("[一键加人] 待添加队列为空，无待执行号码。任务自动结束")
            _task_state["running"] = False
            try:
                processed = _task_state["progress"]["processed"]
                succeeded = _task_state["progress"]["succeeded"]
                from src.utils.alert_notifier import alert_notifier
                asyncio.create_task(alert_notifier.send_user_notification(
                    title="✅ 加人任务已完成",
                    body=f"加人任务已完成，共申请 {processed} 人，成功 {succeeded} 人",
                    category="task"
                ))
            except Exception as ex:
                logger.error(f"发送加人任务完成通知失败: {ex}")
            save_task_state_to_db()
            break

        logger.info(f"[一键加人] 发现 {len(pending)} 条待执行数据: {[p.get('phone') or p.get('wechat_id') for p in pending]}")
        for item in pending:
            if not _task_state["running"] or _task_state["paused"]: break
            drv = _get_effective_driver()
            await _wait_for_pending_replies_if_any(drv, _task_state)
            if not _task_state["running"] or _task_state["paused"]: break
            await _process_item(item, config)
            if not _task_state["running"] or _task_state["paused"]: break
            if active_warmup:
                added_since_warmup += 1
                if added_since_warmup >= warmup_threshold:
                    logger.info(f"[一键托管] 已连续执行 {added_since_warmup} 次加粉动作，切入养号冷静期...")
                    await _perform_warmup_actions()
                    added_since_warmup = 0
                    warmup_threshold = random.randint(5, 8)
        
        if not _task_state["running"] or _task_state["paused"]:
            continue

        # 检测是否还有待执行号码。若队列无更多待执行数据，则提前结束任务，避免执行长达数分钟的冷却期挂起
        next_pending = friend_queue.get_pending(
            limit=1,
            include_failed=config.get("retry_failed", False),
            include_unknown=config.get("retry_unknown", False),
            skip_processing=config.get("skip_processing", True),
            industry_profile_id=config.get("industry_profile_id", ""),
            tag=config.get("tag_filter", ""),
            import_batch_id=import_batch_id_filter,
        )
        if not next_pending and import_batch_id_filter:
            global_stats = friend_queue.get_queue_stats()
            if global_stats.get("pending", 0) > 0:
                next_pending = friend_queue.get_pending(
                    limit=1,
                    include_failed=config.get("retry_failed", False),
                    include_unknown=config.get("retry_unknown", False),
                    skip_processing=config.get("skip_processing", True),
                    industry_profile_id=config.get("industry_profile_id", ""),
                    tag=config.get("tag_filter", ""),
                    import_batch_id="",
                )
        if not next_pending:
            logger.info("[一键加人] 批次处理完毕，检测到无更多待执行号码。任务自动结束")
            _task_state["running"] = False
            try:
                processed = _task_state["progress"]["processed"]
                succeeded = _task_state["progress"]["succeeded"]
                from src.utils.alert_notifier import alert_notifier
                asyncio.create_task(alert_notifier.send_user_notification(
                    title="✅ 加人任务已完成",
                    body=f"加人任务已完成，共申请 {processed} 人，成功 {succeeded} 人",
                    category="task"
                ))
            except Exception as ex:
                logger.error(f"发送加人任务完成通知失败: {ex}")
            save_task_state_to_db()
            break

        wait_time = _get_wait_time(interval_cfg)
        logger.info(f"[一键加人] 批次处理完毕，进入冷静等待期，时长: {wait_time} 秒...")
        await asyncio.sleep(wait_time)

async def _process_item(item: dict, config: dict):
    queue_id = item["id"]
    search_id = item.get("phone", "") or item.get("wechat_id", "")
    if not search_id:
        logger.info(f"[一键加人] 数据项 ID={queue_id} 无有效号码，标记为失败")
        friend_queue.update_status(queue_id, "failed", error_msg="无有效号码")
        return

    logger.info(f"[一键加人] 准备处理数据项: {search_id} (ID={queue_id})")

    # 前置手机号/空号过滤防封筛选器
    if not await _validate_mobile_if_enabled(item, config, queue_id, search_id, _task_state):
        return

    logger.info(f"[一键加人] 正在更新队列状态为 processing, ID={queue_id}")
    friend_queue.update_status(queue_id, "processing")
    drv = _get_effective_driver()
    try:
        if not drv or not drv.is_connected():
            logger.warning(f"[一键加人] ⚠️ 无法执行加人: 微信驱动未就绪或未连接 (drv={drv})。任务挂起/暂停，记录重置为 pending")
            _task_state["paused"] = True
            friend_queue.update_status(queue_id, "pending")
            return
        
        logger.info(f"[一键加人] 调用 UIA 引擎执行微信操作，查找添加 WXID: {search_id}")
        from src.uia.add_friend import AddFriendEngine
        engine = AddFriendEngine(drv)
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: engine.add_new_friend(
                wxid=search_id,
                verify_message=_generate_verify_message(item, config),
                remark=_generate_remark(item, config) or None,
                tags=_merge_tags(item, config) or None,
            )
        )
        status = result.get("status", "failed")
        logger.info(f"[一键加人] UIA 微信操作执行完毕，结果状态: {status}, 返回信息: {result.get('message', '')}")
        friend_queue.update_status(queue_id, status, result.get("nickname", ""), result.get("message", ""))
        if status in ("submitted", "added", "already"): _sync_to_crm(item, result)
        _task_state["progress"]["processed"] += 1
        if result.get("success"):
            _task_state["progress"]["succeeded"] += 1
            friend_queue.increment_today_count()
            _sync_cloud()
        else:
            _task_state["progress"]["failed"] += 1
            _trigger_risk_alert_if_frequent(result.get("message", ""), drv, _task_state)
        friend_queue.add_log(queue_id, search_id, item.get("company_name", ""), "add_friend", status, result.get("message", ""))
        save_task_state_to_db()
    except Exception as e:
        try:
            from src.utils.stop_signal import stop_signal
            from src.uia.input_guard import UIAInterruptError
            
            is_esc_interrupt = (
                isinstance(e, UIAInterruptError)
                or stop_signal.is_stopped
                or "ESC" in str(e)
                or "中断" in str(e)
                or "Interrupt" in type(e).__name__
            )
        except Exception:
            is_esc_interrupt = False

        if is_esc_interrupt:
            logger.info(f"[一键加人] 检测到用户按下 ESC 中断任务，正在将加粉功能安全挂起，并重置数据项 ID={queue_id} 为 pending 状态")
            _task_state["paused"] = True
            friend_queue.update_status(queue_id, "pending")
            friend_queue.add_log(
                queue_id, search_id, item.get("company_name", ""), "add_friend", "pending", "用户按下 ESC 键，加粉任务安全挂起"
            )
            save_task_state_to_db()
            try:
                stop_signal.reset()
            except Exception:
                pass
        else:
            logger.error(f"[一键加人] 执行微信操作时发生异常崩溃: {e}", exc_info=True)
            friend_queue.update_status(queue_id, "failed", error_msg=str(e))
            _task_state["progress"]["failed"] += 1
            _trigger_exception_alert(e, drv, _task_state)
            save_task_state_to_db()
