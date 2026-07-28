"""
自动跟单守护进程 (已升级为细粒度持久化独立 Job 调度)
"""
import asyncio
import logging
import random
import os
import json
import threading
from datetime import datetime

from src.utils.db_manager import WeChatDBManager
from src.utils.websocket_manager import ws_manager

logger = logging.getLogger(__name__)

# ==================== 会话挂起锁 ====================
_active_follow_locks = set()
_lock_mutex = threading.Lock()

def is_session_locked(target: str) -> bool:
    """检查特定会话是否因SDR跟单中被锁定"""
    with _lock_mutex:
        return target in _active_follow_locks

def lock_session(target: str):
    """锁定会话以防止并发打断"""
    with _lock_mutex:
        _active_follow_locks.add(target)
        logger.info(f"[AutoFollowSDR] 会话挂起锁已启用: {target}")

def unlock_session(target: str):
    """释放会话锁"""
    with _lock_mutex:
        _active_follow_locks.discard(target)
        logger.info(f"[AutoFollowSDR] 会话挂起锁已释放: {target}")


# ==================== 执行日志落盘 ====================
def log_sdr_execution(task_id: str, target: str, friend_name: str, status: str, detail: str):
    """将跟单任务的触达执行日志格式化写入本地 JSONL 文件"""
    os.makedirs("data/sdr_logs", exist_ok=True)
    log_file = "data/sdr_logs/sdr_execute.jsonl"
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "task_id": task_id,
        "target": target,
        "friend_name": friend_name,
        "status": status,
        "detail": detail
    }
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.error(f"[AutoFollowSDR] 写入本地跟单日志失败: {e}")


# 全局微信驱动代理引用与获取器
_driver = None

def init_driver(driver):
    global _driver
    _driver = driver

def get_driver():
    return _driver


async def auto_follow_scan_job():
    """APScheduler 触发的 SDR 自动跟单扫描注册 Job"""
    db = WeChatDBManager()
    try:
        from src.crm.account_data import get_active_account, get_account_settings
        account_id = get_active_account()
        settings = get_account_settings(account_id)
        if not settings.get("reply", {}).get("auto_follow", False):
            logger.info("[AutoFollowSDR] 自动跟单全局开关未开启，跳过扫描注册 Job")
            return

        tasks = db.get_auto_follow_tasks()
        active_tasks = [t for t in tasks if t.get("status", "active") == "active"]
        
        # 🌟 收集当前所有理论上合法的 Job ID（任务活跃且目标尚未跟满天数）
        valid_job_ids = set()
        for task in active_tasks:
            task_id = task.get("task_id")
            targets = task.get("targets", [])
            execution_state = task.get("execution_state") or {}
            follow_days = int(task.get("follow_days", 7))
            for target in targets:
                t_state = execution_state.get(target) or {}
                follow_count = t_state.get("follow_count", 0)
                if follow_count < follow_days:
                    valid_job_ids.add(f"sdr_{task_id}_{target}")

        sched = get_scheduler()
        existing_jobs = {job.id for job in sched.get_jobs()}
        
        # 🌟 物理清理已经从任务或目标列表中被删除/暂停的孤立后台作业
        try:
            for jid in list(existing_jobs):
                if jid.startswith("sdr_") and jid not in valid_job_ids:
                    sched.remove_job(jid)
                    logger.info(f"[AutoFollowSDR] 巡检清理孤立/已被用户删除的跟单后台作业: {jid}")
                    existing_jobs.remove(jid)
        except Exception as e_clean:
            logger.error(f"[AutoFollowSDR] 巡检清理孤儿 Job 异常: {e_clean}")

        if not active_tasks:
            return

        for task in active_tasks:
            task_id = task.get("task_id")
            targets = task.get("targets", [])
            execution_state = task.get("execution_state") or {}
            follow_days = int(task.get("follow_days", 7))
            follow_frequency = task.get("follow_frequency", "daily")

            for target in targets:
                t_state = execution_state.get(target) or {}
                follow_count = t_state.get("follow_count", 0)
                if follow_count >= follow_days:
                    continue

                job_id = f"sdr_{task_id}_{target}"
                if job_id not in existing_jobs:
                    kwargs = {'seconds': 60}
                    if follow_frequency in ("daily", "front3_then_interval2"):
                        kwargs = {'days': 1}
                    elif follow_frequency == "hourly":
                        kwargs = {'hours': 1}
                    elif "minute" in follow_frequency:
                        try:
                            mins = int(follow_frequency.split("_")[1])
                            kwargs = {'minutes': mins}
                        except:
                            kwargs = {'days': 1}

                    sched.add_job(
                        "src.task.auto_follow_daemon:single_friend_follow_job_wrapper",
                        'interval',
                        **kwargs,
                        id=job_id,
                        name=f"SDR跟单-{task_id}-{target}",
                        args=[task_id, target],
                        misfire_grace_time=3600,
                        replace_existing=True
                    )
                    logger.info(f"[AutoFollowSDR] 注册持久化 Job 成功: {job_id}")
    except Exception as e:
        logger.error(f"[AutoFollowSDR] 扫描跟单任务失败: {e}")


from apscheduler.schedulers.asyncio import AsyncIOScheduler

def get_scheduler() -> AsyncIOScheduler:
    """获取全局持久化调度器单例"""
    from src.task.scheduler import get_global_scheduler
    return get_global_scheduler()


async def run_auto_follow_scan_job_async():
    try:
        await auto_follow_scan_job()
    except Exception as e:
        logger.error(f"[AutoFollowSDR] 执行异步跟单扫描失败: {e}")


def auto_follow_scan_wrapper():
    """同步包裹入口，安全防 Pickle 反序列化崩溃。"""
    coro = run_auto_follow_scan_job_async()
    try:
        loop = _get_main_loop()
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, loop)
        else:
            asyncio.run(coro)
    except Exception as e:
        logger.error(f"[AutoFollowSDR] 调度巡检异常: {e}")


def single_friend_follow_job_wrapper(task_id: str, target: str):
    """单好友 SDR 定时跟单作业同步包裹，防 Pickle 反序列化崩溃"""
    from src.task.auto_follow_helper import execute_single_follow
    coro = execute_single_follow(task_id, target)
    try:
        loop = _get_main_loop()
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, loop)
        else:
            asyncio.run(coro)
    except Exception as e:
        logger.error(f"[AutoFollowSDR] 执行单好友跟单异常: {e}")


from typing import Optional

def _get_main_loop() -> Optional[asyncio.AbstractEventLoop]:
    try:
        import app.state as app_state
        if hasattr(app_state, "main_loop") and app_state.main_loop:
            return app_state.main_loop
    except Exception:
        pass
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None

# 启动全局单例心跳控制
_auto_follow_daemon_started = False

def ensure_daemon_started():
    global _auto_follow_daemon_started
    loop = _get_main_loop()
    if not loop or not loop.is_running():
        logger.warning("[AutoFollowSDR] 当前无运行中的 event loop，推迟启动跟单守护进程")
        return

    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if current_loop != loop:
        loop.call_soon_threadsafe(ensure_daemon_started)
        return

    if not _auto_follow_daemon_started:
        try:
            sched = get_scheduler()
            if not sched.running:
                sched.start()
                logger.info("[AutoFollowSDR] APScheduler 调度引擎启动")
                
            job_id = "auto_follow_scanner"
            if not sched.get_job(job_id):
                sched.add_job(
                    "src.task.auto_follow_daemon:auto_follow_scan_wrapper",
                    'interval',
                    seconds=60,
                    id=job_id,
                    name='SDR自动跟单持久化巡检守护',
                    misfire_grace_time=3600,
                    replace_existing=True
                )
            _auto_follow_daemon_started = True
            
            from src.task.scheduler import process_existing_tasks
            process_existing_tasks()
        except Exception as e:
            logger.error(f"[AutoFollowSDR] 启动持久化调度器异常: {e}")



# 注册到全局管理器注册表
try:
    from src.task.scheduler import GlobalManagerRegistry
    class AutoFollowManagerAdapter:
        def shutdown(self):
            try:
                sched = get_scheduler()
                if sched.running:
                    sched.shutdown(wait=False)
                    logger.info("[AutoFollowSDR] 已通过 GlobalManagerRegistry 停止调度器")
            except Exception as e:
                logger.error(f"[AutoFollowSDR] 停止调度器异常: {e}")
    GlobalManagerRegistry().register("auto_follow", AutoFollowManagerAdapter())
except Exception as e:
    logger.error(f"[AutoFollowSDR] 注册到 GlobalManagerRegistry 失败: {e}")


def batch_switch_follow_agent(agent_id: str, task_ids: list = None) -> int:
    """批量切换活跃的 SDR 任务的智能体 ID，返回修改成功个数"""
    db = WeChatDBManager()
    try:
        tasks = db.get_auto_follow_tasks()
        count = 0
        for task in tasks:
            tid = task.get("task_id")
            if task_ids is None or tid in task_ids:
                db.update_auto_follow_task(tid, {"agent_id": agent_id})
                count += 1
        logger.info(f"[AutoFollowSDR] 批量切换跟单智能体成功，共更新了 {count} 个任务的智能体 ID 为 {agent_id}")
        return count
    except Exception as err:
        logger.error(f"[AutoFollowSDR] 批量切换跟单智能体失败: {err}")
        return 0
