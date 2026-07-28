import logging
import urllib.request
import tempfile
import os
import uuid
import asyncio

logger = logging.getLogger(__name__)

def download_url_to_temp(url: str) -> str:
    """
    自研可靠的多媒体文件下载器，将 URL 素材保存到系统临时文件夹并返回物理路径。
    """
    if url and os.path.exists(url):
        return url
    from src.utils.rich_reply_compiler import determine_url_type_and_ext
    try:
        url_type, ext = determine_url_type_and_ext(url)
        if not ext:
            if ".png" in url.lower(): ext = ".png"
            elif ".jpg" in url.lower() or ".jpeg" in url.lower(): ext = ".jpg"
            elif ".pdf" in url.lower(): ext = ".pdf"
            elif ".docx" in url.lower(): ext = ".docx"
            elif ".xlsx" in url.lower(): ext = ".xlsx"
            elif ".pptx" in url.lower(): ext = ".pptx"
            else: ext = ".dat"
            
        temp_dir = os.path.join(tempfile.gettempdir(), "xm_bot4_materials")
        os.makedirs(temp_dir, exist_ok=True)
        local_path = os.path.join(temp_dir, f"mass_media_{uuid.uuid4().hex[:12]}{ext}")
        
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response, open(local_path, 'wb') as out_file:
            out_file.write(response.read())
        return local_path
    except Exception as ex:
        logger.error(f"[MassSendingHelper] 下载多媒体链接失败 ({url}): {ex}")
        return ""

def trigger_mass_send_job(job_id: str):
    """
    供 APScheduler 调度器反射调用的顶层全局触发函数 (防 Cython 序列化反弹)。
    """
    from src.task.mass_sending_core import MassSendingCore
    logger.info(f"[MassSendingHelper] 定时群发任务被触发: job_id={job_id}")
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
    core = MassSendingCore()
    if loop.is_running():
        loop.create_task(core._execute_job(job_id))
    else:
        loop.run_until_complete(core._execute_job(job_id))

def cancel_task(db_manager, task_id: str) -> bool:
    success = db_manager.update_mass_send_job(task_id, {"status": "cancelled"})
    if success:
        logger.warning(f"[MassSendingHelper] 任务 {task_id} 已由外部物理熔断终止")
    return success

def cancel_all(db_manager) -> int:
    count = 0
    jobs = db_manager.get_mass_send_jobs()
    for job in jobs:
        if job.get("status") in ("pending", "processing", "paused"):
            cancel_task(db_manager, job["id"])
            count += 1
    return count

def pause_task(db_manager, task_id: str) -> bool:
    success = db_manager.update_mass_send_job(task_id, {"status": "paused"})
    if success:
        logger.info(f"[MassSendingHelper] 任务 {task_id} 已挂起暂停")
    return success

def resume_task(db_manager, task_id: str) -> bool:
    success = db_manager.update_mass_send_job(task_id, {"status": "processing"})
    if success:
        logger.info(f"[MassSendingHelper] 任务 {task_id} 已恢复执行状态")
    return success

def resume_all_pending_jobs(core_instance):
    """自检恢复未完结群发任务"""
    try:
        import app.state as app_state
        loop = getattr(app_state, "main_loop", None)
    except Exception:
        loop = None

    if not loop:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

    if not loop or not loop.is_running():
        logger.warning("[MassSendingHelper] 当前无运行中的 event loop，推迟恢复群发任务")
        return

    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if current_loop != loop:
        loop.call_soon_threadsafe(lambda: resume_all_pending_jobs(core_instance))
        return

    try:
        jobs = core_instance._db.get_mass_send_jobs()
        running_count = 0
        for job in jobs:
            if job.get("status") in ("pending", "processing"):
                job_id = job.get("id")
                loop.create_task(core_instance._execute_job(job_id))
                running_count += 1
        if running_count > 0:
            logger.info(f"[MassSendingHelper] 断点自检完成，已成功恢复 {running_count} 个未完成的群发排期任务")
    except Exception as e:
        logger.error(f"[MassSendingHelper] 自动恢复挂起任务异常: {e}")

def register_manager_adapter():
    try:
        from src.task.scheduler import GlobalManagerRegistry
        from src.task.mass_sending_core import MassSendingCore
        class MassSendingManagerAdapter:
            def shutdown(self):
                try:
                    core = MassSendingCore()
                    cancelled_count = core.cancel_all()
                    if cancelled_count > 0:
                        logger.info(f"[MassSendingHelper] 已熔断停止了 {cancelled_count} 个进行中的群发任务")
                except Exception as e:
                    logger.error(f"[MassSendingHelper] 熔断任务异常: {e}")
        GlobalManagerRegistry().register("mass_sending", MassSendingManagerAdapter())
    except Exception as e:
        logger.error(f"[MassSendingHelper] 注册到 GlobalManagerRegistry 失败: {e}")
