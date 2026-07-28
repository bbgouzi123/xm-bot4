import logging
import threading
import os
from typing import List, Dict, Optional, Any
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from sqlalchemy import event
from sqlalchemy.engine import Engine

from src.utils.db_manager import WeChatDBManager

logger = logging.getLogger(__name__)

# ==================== SQLite WAL 优化 ====================
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
    except Exception as e:
        logger.warning(f"[Scheduler] 设置 SQLite PRAGMA 失败: {e}")
    finally:
        cursor.close()

# ==================== 全局持久化调度器单例 ====================
_global_scheduler: Optional[AsyncIOScheduler] = None
_scheduler_lock = threading.Lock()

def get_global_scheduler() -> AsyncIOScheduler:
    """获取全局持久化 APScheduler 调度器单例"""
    global _global_scheduler
    if _global_scheduler is None:
        with _scheduler_lock:
            if _global_scheduler is None:
                # 统一的持久化数据库文件，放在 data/scheduler_v3.db
                os.makedirs("data", exist_ok=True)
                db_path = os.path.abspath("data/scheduler_v3.db")
                jobstores = {'default': SQLAlchemyJobStore(url=f'sqlite:///{db_path}', engine_options={'connect_args': {'timeout': 30}})}
                job_defaults = {'coalesce': True, 'max_instances': 1}
                _global_scheduler = AsyncIOScheduler(jobstores=jobstores, job_defaults=job_defaults, timezone='Asia/Shanghai')


                # [容错] 主动清理 Cython/PyInstaller 打包后无法反序列化的旧 Job
                # 避免启动时 LookupError: cython_function_or_method 的 Traceback 噪音
                _cleanup_broken_jobs(db_path)
                
                # 监听调度器事件以做任务清理
                from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED
                def _handle_job_event(event):
                    if event.code == EVENT_JOB_MISSED:
                        logger.warning(f"[Scheduler] 任务错过触发时间: {event.job_id}")
                    elif event.code == EVENT_JOB_ERROR:
                        logger.error(f"[Scheduler] 任务运行失败: {event.job_id}, exception: {event.exception}")
                _global_scheduler.add_listener(_handle_job_event, EVENT_JOB_ERROR | EVENT_JOB_MISSED)
                
    return _global_scheduler


def _cleanup_broken_jobs(db_path: str):
    """启动前清理持久化数据库中无法反序列化的损坏 Job（如 Cython 编译后函数引用变化），
    或者含有 legacy src.uia.task_runner 引用（防止后台 COM 线程冲突）的 Job
    """
    try:
        import sqlite3
        import pickle
        from apscheduler.job import Job
        conn = sqlite3.connect(db_path, timeout=10)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM apscheduler_jobs")
        rows = cursor.fetchall()
        broken_ids = []
        for (job_id,) in rows:
            try:
                cursor.execute("SELECT job_state FROM apscheduler_jobs WHERE id=?", (job_id,))
                state_row = cursor.fetchone()
                if state_row:
                    raw_bytes = state_row[0]
                    # 检查是否包含老旧的 task_runner 引用
                    is_legacy = b"uia.task_runner" in raw_bytes or b"src.uia.task_runner" in raw_bytes
                    
                    state = pickle.loads(raw_bytes)
                    # 模拟完整反序列化与函数反射，防止 Cython 模块加载失败
                    job = Job.__new__(Job)
                    job.__setstate__(state)
                    
                    # 进一步在解析后的对象里寻找 legacy 函数引用
                    func_ref = state.get('func')
                    if func_ref and ("task_runner" in str(func_ref) or "uia_task_runner" in str(func_ref)):
                        is_legacy = True
                        
                    if is_legacy:
                        broken_ids.append(job_id)
            except Exception:
                broken_ids.append(job_id)
        if broken_ids:
            for jid in broken_ids:
                cursor.execute("DELETE FROM apscheduler_jobs WHERE id=?", (jid,))
            conn.commit()
            logger.info(f"[Scheduler] 已清理 {len(broken_ids)} 个无法恢复/过时的旧 Job: {broken_ids}")
        conn.close()
    except Exception as e:
        logger.debug(f"[Scheduler] 清理损坏 Job 时异常（可忽略）: {e}")


# ==================== 现存任务自检恢复 ====================
def process_existing_tasks():
    """开机自检：扫描调度器中已完成或已过时的一次性任务进行清理，并确保循环任务就绪，同时清除不活跃的跟单僵尸 Job"""
    sched = get_global_scheduler()
    if not sched.running:
        logger.warning("[Scheduler] 调度器未运行，跳过现存任务自检")
        return
    
    try:
        jobs = sched.get_jobs()
        logger.info(f"[Scheduler] 开机扫描，共发现 {len(jobs)} 个现存任务")
        
        # 💡 [SDR跟单幽灵 Job 清洗] 从本地 DB 加载所有活跃的 sdr 任务，其余全数物理注销
        db = WeChatDBManager()
        active_sdr_job_ids = set()
        try:
            tasks = db.get_auto_follow_tasks()
            for t in tasks:
                if t.get("status", "active") == "active":
                    task_id = t.get("task_id")
                    targets = t.get("targets", [])
                    exec_state = t.get("execution_state") or {}
                    follow_days = int(t.get("follow_days", 7))
                    for target in targets:
                        t_state = exec_state.get(target) or {}
                        follow_count = t_state.get("follow_count", 0)
                        if follow_count < follow_days:
                            active_sdr_job_ids.add(f"sdr_{task_id}_{target}")
        except Exception as db_ex:
            logger.error(f"[Scheduler] 开机自检获取活跃跟单任务列表失败: {db_ex}")

        from apscheduler.triggers.date import DateTrigger
        for job in jobs:
            # 1. 清理已过时的一次性任务
            if isinstance(job.trigger, DateTrigger) and job.next_run_time is None:
                logger.info(f"[Scheduler] 清理已执行完成的一次性任务: {job.id}")
                try:
                    job.remove()
                except Exception as ex:
                    logger.error(f"[Scheduler] 移除任务 {job.id} 失败: {ex}")
            # 2. 清理已经失效/被标记停用/完成的 SDR 僵尸作业
            elif job.id.startswith("sdr_") and job.id != "auto_follow_scanner":
                if job.id not in active_sdr_job_ids:
                    logger.info(f"[Scheduler] 🧼 清理已失效或不在活跃范围内的跟单 Job: {job.id}")
                    try:
                        job.remove()
                    except Exception as ex:
                        logger.error(f"[Scheduler] 移除失效跟单 Job {job.id} 失败: {ex}")
    except Exception as e:
        logger.error(f"[Scheduler] 处理现存任务自检发生异常: {e}")


# ==================== 全局管理器注册表 ====================
class GlobalManagerRegistry:
    """全局任务/后台管理器注册表，用于统一生命周期控制"""
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super().__new__(cls)
                    cls._instance._managers = {}
        return cls._instance
        
    def register(self, name: str, manager_instance: Any):
        self._managers[name] = manager_instance
        logger.info(f"[GlobalManagerRegistry] 注册管理器: {name}")
        
    def get(self, name: str) -> Optional[Any]:
        return self._managers.get(name)
        
    def shutdown_all(self):
        logger.info("[GlobalManagerRegistry] 正在关闭所有注册的管理器...")
        for name, mgr in list(self._managers.items()):
            try:
                if hasattr(mgr, 'shutdown'):
                    mgr.shutdown()
                elif hasattr(mgr, 'cancel_all'):
                    mgr.cancel_all()
                logger.info(f"[GlobalManagerRegistry] 已成功关闭管理器: {name}")
            except Exception as e:
                logger.error(f"[GlobalManagerRegistry] 关闭管理器 {name} 异常: {e}")


# ==================== 统一任务调度信息聚合器 (保持向后兼容) ====================
class UnifiedScheduler:
    """统一任务调度信息聚合器 (Phase 10-C)"""
    
    def __init__(self):
        self._db = WeChatDBManager()

    def get_all_tasks(self) -> List[Dict]:
        """汇总查询所有自动跟单和安全群发的任务及进度日志"""
        tasks = []
        
        # 1. 自动跟单任务
        try:
            for t in self._db.get_auto_follow_tasks():
                exec_state = t.get("execution_state") or {}
                total = len(t.get("targets", [])) * int(t.get("follow_days", 7))
                current = sum(s.get("follow_count", 0) for s in exec_state.values() if isinstance(s, dict))
                tasks.append({
                    "task_id": t.get("task_id"),
                    "type": "auto_follow",
                    "name": f"SDR自动跟单 - 周期 {t.get('follow_days')} 天",
                    "status": t.get("status", "active"),
                    "total": total,
                    "current": current,
                    "created_at": t.get("created_at")
                })
        except Exception as e:
            logger.error(f"[UnifiedScheduler] 收集自动跟单任务状态异常: {e}")

        # 2. 安全群发任务
        try:
            from src.task.mass_sending_core import MassSendingCore
            mass_core = MassSendingCore()
            for j in mass_core.get_all_tasks():
                tasks.append({
                    "task_id": j.get("id"),
                    "type": "mass_send",
                    "name": f"大面积安全群发 ({j.get('total')} 目标)",
                    "status": j.get("status"),
                    "total": j.get("total"),
                    "current": j.get("current"),
                    "created_at": j.get("created_at")
                })
        except Exception as e:
            logger.error(f"[UnifiedScheduler] 收集群发任务状态异常: {e}")

        # 按时间排序
        tasks.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        return tasks

    def get_features_status(self) -> Dict:
        """获取各自动化模块功能是否开启状态"""
        # 1. 检查新朋友监控是否运行
        friend_monitor_running = False
        try:
            # 🌟 优先从多开账号实例管理器中读取当前活跃微信的监控状态
            from app.state import account_manager as am
            from src.crm.account_data import get_active_account
            active_wxid = get_active_account()
            if am and active_wxid:
                inst = am.get_instance_by_wxid(active_wxid)
                if inst and inst.friend_request_monitor and inst.friend_request_monitor.is_running():
                    friend_monitor_running = True
        except Exception:
            pass

        if not friend_monitor_running:
            try:
                from src.api.config_api import state
                if state._friend_request_monitor and state._friend_request_monitor.is_running():
                    friend_monitor_running = True
            except Exception:
                pass


        # 2. 检查自动跟单任务是否处于 active
        auto_follow_running = False
        try:
            auto_follow_running = any(t.get("status") == "active" for t in self._db.get_auto_follow_tasks())
        except Exception:
            pass

        # 3. 检查大批量安全群发任务是否正在处理
        mass_sending_running = False
        try:
            mass_sending_running = any(j.get("status") in ("pending", "processing") for j in self._db.get_mass_send_jobs())
        except Exception:
            pass

        return {
            "friend_request_monitor": "running" if friend_monitor_running else "stopped",
            "auto_follow": "running" if auto_follow_running else "stopped",
            "mass_sending": "running" if mass_sending_running else "stopped"
        }

