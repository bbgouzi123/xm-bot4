import logging
import random
import uuid
import os
import asyncio
import urllib.request
import tempfile
from datetime import datetime
from typing import List, Optional

from src.utils.db_manager import WeChatDBManager
from src.utils.websocket_manager import ws_manager

logger = logging.getLogger(__name__)


async def _run_uia(func, *args):
    """在 asyncio 内挂载 Windows COM 代理线程执行 UIA 操作"""
    def _wrapper():
        try:
            import comtypes
            comtypes.CoInitialize()
        except:
            pass
        try:
            return func(*args)
        finally:
            try:
                import gc
                gc.collect()
            except:
                pass

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _wrapper)


from src.task.auto_follow_rule import (
    should_follow_up_now,
    get_friend_display_name,
    download_media_if_url,
    generate_sdr_reply
)


async def execute_single_follow(task_id: str, target: str):
    """执行单个好友的 SDR 跟单触达操作"""
    from src.utils.uia_task_runner import is_engine_suspended, is_session_fused
    if is_engine_suspended() or is_session_fused(target):
        logger.warning(f"[AutoFollowSDR] 会话已熔断或全局引擎挂起，跳过此次跟进触达: {target}")
        return

    from src.crm.account_data import get_active_account, get_account_settings
    account_id = get_active_account()
    settings = get_account_settings(account_id)
    if not settings.get("reply", {}).get("auto_follow", False):
        logger.info(f"[AutoFollowSDR] 自动跟单全局开关未开启，跳过对 {target} 的跟进触达")
        return

    from src.task.auto_follow_daemon import (
        get_driver, lock_session, unlock_session, log_sdr_execution
    )
    db = WeChatDBManager()
    task = db.get_auto_follow_task(task_id)
    if not task or task.get("status", "active") != "active" or target not in task.get("targets", []):
        try:
            from src.task.auto_follow_daemon import get_scheduler
            job_id = f"sdr_{task_id}_{target}"
            get_scheduler().remove_job(job_id)
            logger.info(f"[AutoFollowSDR] 任务失效、暂停或目标被移除，已主动从调度器清理 Job: {job_id}")
        except Exception as e_rm:
            logger.debug(f"[AutoFollowSDR] 自动清理失效 Job 异常: {e_rm}")
        return
        
    current_time = datetime.now()
    current_str_time = current_time.strftime("%H:%M")
    today_date = current_time.strftime("%Y-%m-%d")
    
    # 检查防扰时间锁并执行下一次发送时间的自适应平移
    start_h = task.get("time_range_start", "09:00")
    end_h = task.get("time_range_end", "20:00")
    if not (start_h <= current_str_time <= end_h):
        try:
            from src.task.auto_follow_daemon import get_scheduler
            from datetime import timedelta, time as dt_time
            sched = get_scheduler()
            job_id = f"sdr_{task_id}_{target}"
            
            start_hour, start_minute = map(int, start_h.split(":"))
            if current_str_time > end_h:
                target_date = current_time + timedelta(days=1)
            else:
                target_date = current_time
            
            random_offset = random.randint(1, 10)
            next_run = datetime.combine(
                target_date.date(),
                dt_time(hour=start_hour, minute=start_minute)
            ) + timedelta(minutes=random_offset)
            
            job = sched.get_job(job_id)
            if job:
                job.modify(next_run_time=next_run)
                logger.info(f"[AutoFollowSDR] 会话 {target} 不在防扰区间内，已平移 Job 至 {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
        except Exception as ex:
            logger.error(f"[AutoFollowSDR] 自适应平移防打扰跟单时间异常: {ex}")
        return

    driver = get_driver()
    if not driver or not driver.is_connected():
        return

    execution_state = task.get("execution_state") or {}
    t_state = execution_state.get(target) or {}
    if not isinstance(t_state, dict):
        t_state = {}

    follow_days = int(task.get("follow_days", 7))
    follow_count = t_state.get("follow_count", 0)
    last_follow_time = t_state.get("last_follow_time", "")

    if follow_count >= follow_days:
        try:
            from src.task.auto_follow_daemon import get_scheduler
            get_scheduler().remove_job(f"sdr_{task_id}_{target}")
        except:
            pass
        return

    if last_follow_time.startswith(today_date):
        return

    max_daily = int(task.get("max_daily", 50))
    sent_today_count = sum(
        1 for st in execution_state.values()
        if isinstance(st, dict) and st.get("last_follow_time", "").startswith(today_date)
    )
    if sent_today_count >= max_daily:
        return

    follow_frequency = task.get("follow_frequency", "daily")
    if not should_follow_up_now(last_follow_time, follow_frequency, follow_count):
        return

    friend_name = get_friend_display_name(target)
    logger.info(f"[AutoFollowSDR] 任务 {task_id} 正在触达客户: {friend_name} ({target})")

    from src.api.task_api import _active_tasks
    if task_id not in _active_tasks:
        _active_tasks[task_id] = {
            "status": "running",
            "total": len(task.get("targets", [])) * follow_days,
            "current": sum(s.get("follow_count", 0) for s in execution_state.values() if isinstance(s, dict)),
            "errors": 0,
            "runs_detected": 0
        }
    _active_tasks[task_id]["runs_detected"] = _active_tasks[task_id].get("runs_detected", 0) + 1

    # 启用会话挂起锁
    lock_session(target)
    try:
        from src.task.wechat_operation_scheduler import get_wechat_scheduler, WeChatAction, WeChatPriority
        scheduler = await get_wechat_scheduler()

        # 🌟 [UIA弹窗+HUD] SDR操作必须在 uia_lock 上下文中执行，向用户明确展示正在进行的自动化任务
        # 这样用户/开发人员看到微信界面跳动时，HUD和弹窗能立即告知原因，不会感到莫名其妙
        from src.uia.input_guard import uia_lock
        from src.uia.uia_ws_notify import notify_frontend

        async def do_follow_action():
            replies = await generate_sdr_reply(task, target, friend_name, task.get("fallback_text", ""))
            
            all_success = True
            sent_parts = []
            for part in replies:
                if not part:
                    continue
                local_media_path = download_media_if_url(part)
                if local_media_path:
                    success = await _run_uia(driver.SendFiles, friend_name, local_media_path)
                    try:
                        os.remove(local_media_path)
                    except:
                        pass
                else:
                    success = await _run_uia(driver.send_message, friend_name, part)
                if not success:
                    all_success = False
                else:
                    sent_parts.append(part)
                await asyncio.sleep(2)
            return all_success, sent_parts

        action_success = False
        sent_parts = []

        async def run_action():
            nonlocal action_success, sent_parts
            # 在 uia_lock 异步上下文中执行，锁定键鼠并通知 HUD/弹窗
            # 使用 async_guard 版本，支持在锁定期间 await 异步发送操作
            follow_day_display = follow_count + 1
            lock_msg = f"SDR自动跟单中 · 正在向「{friend_name}」发送第 {follow_day_display}/{follow_days} 天跟进消息"
            async with uia_lock.async_guard(lock_msg, hwnd=getattr(driver, 'hwnd', None)):
                notify_frontend("status_update", lock_msg)
                action_success_inner, sent_parts_inner = await do_follow_action()
            action_success = action_success_inner
            sent_parts = sent_parts_inner
            if not action_success:
                raise RuntimeError("UIA 消息或文件发送返回失败")

        action = WeChatAction(
            action_type="sdr_follow",
            priority=WeChatPriority.MEDIUM,
            execute_fn=run_action,
            target_wxid=friend_name
        )

        await scheduler.submit(action)
        await action.done_event.wait()
        all_success = action.result["success"]

        if all_success:
            from src.utils.uia_task_runner import report_uia_success
            report_uia_success(target)
            t_state["last_follow_time"] = datetime.now().isoformat()
            t_state["follow_count"] = follow_count + 1
            execution_state[target] = t_state
            db.update_auto_follow_task(task_id, {"execution_state": execution_state})

            # 判断跟单是否完全结束并投递事件
            if t_state["follow_count"] >= follow_days:
                try:
                    from src.api.customer_api.adapter_factory import submit_event
                    from src.crm.account_data import get_active_account
                    submit_event("follow_completed", {
                        "account_id": get_active_account() or "main",
                        "task_id": task_id,
                        "target_wxid": target,
                        "nickname": friend_name,
                        "follow_count": t_state["follow_count"],
                        "timestamp": int(datetime.now().timestamp())
                    })
                except Exception as ce:
                    logger.error(f"[客户API] 投递跟单完成事件异常: {ce}")

            log_sdr_execution(task_id, target, friend_name, "success", f"成功发送跟进消息：{'; '.join(sent_parts)}")

            try:
                from src.utils.chat_history import ChatHistoryManager
                from src.crm.account_data import get_active_account
                account_id = get_active_account() or "main"
                history_mgr = ChatHistoryManager(account_id)
                for part in sent_parts:
                    if part:
                        history_mgr.add_message(
                            session_id=target,
                            session_name=friend_name,
                            role="assistant",
                            content=part
                        )
            except Exception as history_err:
                logger.error(f"[AutoFollowSDR] 写入历史失败: {history_err}")

            _active_tasks[task_id]["current"] += 1
            await ws_manager.broadcast_json({
                "type": "task_progress",
                "task_id": task_id,
                "progress": min(100, int((_active_tasks[task_id]["current"] / _active_tasks[task_id]["total"]) * 100)) if _active_tasks[task_id]["total"] > 0 else 0,
                "detail": f"🎯 [SDR触达] 已为客户 {friend_name} 发送第 {t_state['follow_count']} 天的跟进内容！"
            })
            await asyncio.sleep(random.randint(10, 20))
        else:
            from src.utils.uia_task_runner import report_uia_failure
            report_uia_failure(target)
            _active_tasks[task_id]["errors"] = _active_tasks[task_id].get("errors", 0) + 1
            # 🐛 Bug修复：失败时同样记录今日尝试时间戳，防止当天同一会话无限重试死循环
            # 原逻辑：只有成功才写 last_follow_time，失败不写 → 今天的防重保护失效 → 无限触发
            t_state["last_follow_time"] = datetime.now().isoformat()
            execution_state[target] = t_state
            db.update_auto_follow_task(task_id, {"execution_state": execution_state})
            log_sdr_execution(task_id, target, friend_name, "failed", f"UIA 消息或文件发送返回失败（成功发送：{'; '.join(sent_parts)}）")
    except Exception as task_err:
        logger.error(f"[AutoFollowSDR] 触达客户 {friend_name} 异常: {task_err}")
        from src.utils.uia_task_runner import report_uia_failure
        report_uia_failure(target)
        _active_tasks[task_id]["errors"] = _active_tasks[task_id].get("errors", 0) + 1
        log_sdr_execution(task_id, target, friend_name, "failed", f"执行异常: {str(task_err)}")
    finally:
        unlock_session(target)


def enroll_sdr_on_first_icebreak(name: str, intent: str, account_id: str):
    if intent != "friend_accepted":
        return
    try:
        from src.crm.account_settings_store import get_account_settings
        settings = get_account_settings(account_id)
        if not settings.get("reply", {}).get("auto_follow", False):
            logger.info(f"[SDR] 自动跟单默认不开启，跳过为新客户 {name} 自动开启跟单流程")
            return

        from src.utils.db_manager import WeChatDBManager
        from src.utils.contacts_cache import contacts_cache
        db = WeChatDBManager()
        wxid = next((f.get('wxid') for f in contacts_cache.get_friends(account_id) if f.get('name') == name), "") or name
        if not any(t.get("status") == "active" and wxid in (t.get("targets") or []) for t in db.get_auto_follow_tasks()):
            db.add_auto_follow_task({
                "task_id": f"afl_auto_{uuid.uuid4().hex[:8]}",
                "targets": [wxid],
                "follow_days": 7,
                "follow_frequency": "front3_then_interval2",
                "time_range_start": "09:00", "time_range_end": "20:00",
                "follow_scenario": "产品咨询跟单", "use_ai": True,
                "fallback_text": "您好！请问您目前主要关注我们系统的哪些自动化功能呢？",
                "max_daily": 50, "status": "active",
                "created_at": datetime.now().isoformat(),
            })
            logger.info(f"[SDR] 自动为新客户 {name}({wxid}) 开启 SDR 流程")
    except Exception as e:
        logger.error(f"[SDR] 自动挂载任务失败: {e}")
