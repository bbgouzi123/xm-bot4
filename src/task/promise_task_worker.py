import asyncio
import logging
import os
from typing import Any, List
from datetime import datetime
from src.utils.db_manager import WeChatDBManager
from src.utils.websocket_manager import ws_manager

logger = logging.getLogger(__name__)

# 全局微信驱动代理引用与获取器
_driver = None

def init_driver(driver):
    global _driver
    _driver = driver

def get_driver():
    return _driver

def get_driver_for_account(account_id: str, default_driver: Any) -> Any:
    if not account_id or account_id == "default":
        return default_driver
    try:
        from app.state import account_manager
        for inst in getattr(account_manager, '_instances', {}).values():
            inst_wxid = getattr(inst, 'wxid', None) or getattr(inst, 'bot_wxid', None) or (getattr(inst.driver, '_wxid', None) if getattr(inst, 'driver', None) else None) or (getattr(inst.driver, 'bot_wxid', None) if getattr(inst, 'driver', None) else None)
            if inst_wxid == account_id:
                if getattr(inst, 'driver', None):
                    return inst.driver
        if account_id in getattr(account_manager, '_instances', {}):
            inst = account_manager._instances[account_id]
            if getattr(inst, 'driver', None):
                return inst.driver
    except Exception as e:
        logger.warning(f"[PromiseWorker] 解析账号ID {account_id} 的专属驱动失败: {e}")
    return default_driver

# 启动全局单例心跳控制
_worker_task = None
_worker_running = False

async def start_promise_worker():
    global _worker_task, _worker_running
    if _worker_running:
        return
    _worker_running = True
    _worker_task = asyncio.create_task(_promise_worker_loop())
    logger.info("[PromiseWorker] 业务承诺待办任务池 Worker 启动成功")

async def stop_promise_worker():
    global _worker_running, _worker_task
    _worker_running = False
    if _worker_task:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
        _worker_task = None
    logger.info("[PromiseWorker] 业务承诺待办任务池 Worker 已停止")

async def _promise_worker_loop():
    db = WeChatDBManager()
    # 进程冷启动/崩溃重启时，如果有 processing 的任务，自动重置为 pending 让其可以被重新拉起
    try:
        tasks = db.get_promise_tasks()
        for t in tasks:
            if t.get("status") == "processing":
                db.update_promise_task(t.get("id"), {"status": "pending"})
    except Exception as reset_ex:
        logger.warning(f"[PromiseWorker] 重置卡死任务失败: {reset_ex}")

    while _worker_running:
        try:
            await asyncio.sleep(4.0)  # 每 4 秒轮询一次
            
            driver = get_driver()
            if not driver:
                continue

            tasks = db.get_promise_tasks()
            # 找出所有 pending 状态的任务
            pending_tasks = [t for t in tasks if t.get("status") == "pending"]
            if not pending_tasks:
                continue

            for task in pending_tasks:
                if not _worker_running:
                    break
                await _process_promise_task(db, driver, task)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[PromiseWorker] 轮询心跳异常: {e}", exc_info=True)

async def _process_promise_task(db: WeChatDBManager, driver: Any, task: dict):
    task_id = task.get("id")
    target_wxid = task.get("target_wxid") or task.get("target_name")
    task_type = task.get("task_type")
    retry_count = task.get("retry_count", 0)
    approval_status = task.get("approval_status")

    logger.info(f"[PromiseWorker] 正在执行业务待办任务 {task_id}，类型: {task_type}，目标: {target_wxid}")

    # 动态解析任务所属微信实例的专属 driver
    task_account_id = task.get("account_id")
    actual_driver = get_driver_for_account(task_account_id, driver)
    
    # 1. 自动安全防线：读取履约能力配置
    capabilities = db.get_fulfillment_capabilities()
    cap = next((c for c in capabilities if c.get("key") == task_type), None)
    safety_level = cap.get("safety_level", 1) if cap else 1
    is_enabled = cap.get("enabled", True) if cap else True

    if not is_enabled:
        logger.warning(f"[PromiseWorker] 履约能力已被关闭，无法执行: {task_type}")
        db.update_promise_task(task_id, {"status": "failed", "error_message": f"该项自动履约能力已在控制台中被禁用: {task_type}"})
        return

    # 人机协同授权拦截：高危操作(safety_level >= 3)在获得人工授权前，强制拦截在 pending_approval
    if safety_level >= 3 and approval_status != "approved":
        db.update_promise_task(task_id, {"status": "pending_approval", "error_message": "检测到敏感控制级操作，已拦截，等待管理员授权审批"})
        await ws_manager.broadcast_task_update(
            task_id=task_id, task_type="业务待办任务", status="pending_approval",
            progress=0, total=100, message="高危系统级承诺，已成功安全拦截，等待管理员审批",
            friend_name=target_wxid, incoming_msg=task.get("reply_text", "")
        )
        return

    # 🌟 履约状态前置评估自愈：若系统处于 UIA 维护、引擎挂起或该会话处于人工接管中，
    # 我们暂缓履约，并让其无损维持在 pending 状态，不累计重试失败次数，支持后续自动化自愈执行。
    from src.utils.uia_task_runner import is_uia_maintenance_active, is_engine_suspended
    from src.utils.contacts_cache import contacts_cache
    account_id = getattr(actual_driver, 'bot_wxid', None) or getattr(actual_driver, '_wxid', None) or 'default'
    is_takeover = any(
        f.get("is_takeover", False) 
        for f in contacts_cache.get_friends(account_id) 
        if f.get("name") == target_wxid or f.get("wxid") == target_wxid
    )
    if is_uia_maintenance_active() or is_engine_suspended() or is_takeover:
        logger.info(f"[PromiseWorker] 好友 '{target_wxid}' 任务因系统处于维护/挂起/接管状态被暂缓，保持 pending")
        db.update_promise_task(task_id, {
            "status": "pending",
            "error_message": "系统处于维护模式、接管模式或熔断挂起状态，暂缓履约。"
        })
        await ws_manager.broadcast_task_update(
            task_id=task_id,
            task_type="业务待办任务",
            status="processing",
            progress=10,
            total=100,
            message="微信实例被维护/接管/挂起中，任务已安全挂起，等待恢复后自动履约...",
            friend_name=target_wxid,
            incoming_msg=task.get("reply_text", "")
        )
        return

    # 更新状态为 processing 并进行通知
    db.update_promise_task(task_id, {"status": "processing", "progress": 20})
    await ws_manager.broadcast_task_update(
        task_id=task_id,
        task_type="业务待办任务",
        status="processing",
        progress=20,
        total=100,
        message=f"正在自动执行承诺待办: {task_type}",
        friend_name=target_wxid,
        incoming_msg=task.get("reply_text", "")
    )

    success = False
    error_msg = ""
    
    try:
        # 2. 判断是否满足自动化执行条件
        from src.utils.uia_task_runner import is_uia_maintenance_active, is_engine_suspended
        from src.utils.contacts_cache import contacts_cache
        account_id = getattr(actual_driver, 'bot_wxid', None) or getattr(actual_driver, '_wxid', None) or 'default'
        
        is_takeover = any(
            f.get("is_takeover", False) 
            for f in contacts_cache.get_friends(account_id) 
            if f.get("name") == target_wxid or f.get("wxid") == target_wxid
        )
        
        if is_uia_maintenance_active() or is_engine_suspended() or is_takeover:
            raise RuntimeError("系统处于维护模式、接管模式或熔断挂起状态，暂缓执行。")

        # 3. 执行特定类型任务
        from src.task.wechat_operation_scheduler import get_wechat_scheduler, WeChatAction, WeChatPriority
        scheduler = await get_wechat_scheduler()

        if task_type == "send_live_record":
            async def do_live_record():
                from .promise_executor import execute_send_live_record
                await execute_send_live_record(actual_driver, target_wxid, cap)

            action = WeChatAction(
                action_type="send_live_record",
                priority=WeChatPriority.HIGH,
                execute_fn=do_live_record,
                target_wxid=target_wxid
            )
            await scheduler.submit(action)
            await action.done_event.wait()
            success = action.result["success"]
            if not success:
                raise RuntimeError(action.result["error_msg"] or "录屏发送异常")
                
        elif task_type == "send_materials":
            materials_path = task.get("materials_path")
            async def do_send_materials():
                from .promise_executor import execute_send_materials
                await execute_send_materials(actual_driver, target_wxid, materials_path)

            action = WeChatAction(
                action_type="send_materials",
                priority=WeChatPriority.HIGH,
                execute_fn=do_send_materials,
                target_wxid=target_wxid
            )
            await scheduler.submit(action)
            await action.done_event.wait()
            success = action.result["success"]
            if not success:
                raise RuntimeError(action.result["error_msg"] or "物料发送异常")

        elif task_type == "web_snapshot":
            url = task.get("payload_details", {}).get("url") or "https://www.baidu.com"
            async def do_web_snapshot():
                from .promise_executor import execute_web_snapshot
                await execute_web_snapshot(actual_driver, target_wxid, task_id, url)

            action = WeChatAction(
                action_type="web_snapshot",
                priority=WeChatPriority.HIGH,
                execute_fn=do_web_snapshot,
                target_wxid=target_wxid
            )
            await scheduler.submit(action)
            await action.done_event.wait()
            success = action.result["success"]
            if not success:
                raise RuntimeError(action.result["error_msg"] or "网页截图履约异常")

        elif task_type == "download_media":
            details = task.get("payload_details") or {}
            media_url = details.get("url") or task.get("reply_text", "")
            async def do_download_media():
                from .promise_executor import execute_download_media
                config = cap.get("config") or {} if cap else {}
                await execute_download_media(actual_driver, target_wxid, task_id, media_url, config)

            action = WeChatAction(
                action_type="download_media",
                priority=WeChatPriority.HIGH,
                execute_fn=do_download_media,
                target_wxid=target_wxid
            )
            await scheduler.submit(action)
            await action.done_event.wait()
            success = action.result["success"]
            if not success:
                raise RuntimeError(action.result["error_msg"] or "多媒体嗅探下载履约异常")

        elif task_type == "sys_control":
            details = task.get("payload_details") or {}
            cmd_kind = details.get("command")
            cmd_arg = details.get("argument")
            async def do_sys_control():
                from .promise_executor import execute_sys_control
                await execute_sys_control(actual_driver, target_wxid, cmd_kind, cmd_arg)

            action = WeChatAction(
                action_type="sys_control",
                priority=WeChatPriority.HIGH,
                execute_fn=do_sys_control,
                target_wxid=target_wxid
            )
            await scheduler.submit(action)
            await action.done_event.wait()
            success = action.result["success"]
            if not success:
                raise RuntimeError(action.result["error_msg"] or "高危系统控制执行异常")

        elif cap and cap.get("is_custom"):
            config = cap.get("config") or {}
            cmd_template = config.get("cmd_template", "")
            if not cmd_template:
                raise RuntimeError("未配置自定义能力的命令行模板")
            
            cmd_to_run = cmd_template.replace("{target}", target_wxid).replace("{task_id}", task_id)
            
            async def do_custom_cmd():
                logger.info(f"[PromiseWorker] 正在执行自定义命令行: {cmd_to_run}")
                proc = await asyncio.create_subprocess_shell(
                    cmd_to_run,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await proc.communicate()
                stdout_str = stdout.decode('utf-8', errors='ignore')
                stderr_str = stderr.decode('utf-8', errors='ignore')
                
                logger.info(f"[PromiseWorker] 自定义命令行执行完成，返回码: {proc.returncode}")
                if stdout_str:
                    logger.info(f"[PromiseWorker] 标准输出: {stdout_str}")
                if stderr_str:
                    logger.error(f"[PromiseWorker] 错误输出: {stderr_str}")
                
                if proc.returncode != 0:
                    raise RuntimeError(f"自定义物理指令执行失败，退出码: {proc.returncode}。错误: {stderr_str}")
                
                # 微信反馈输出结果
                msg_feedback = f"【自动履约反馈】\n您请求的自定义任务【{cap.get('name')}】已成功完成！\n执行结果：\n{stdout_str[:300]}"
                from src.orchestrator.ui_bus import ui_bus, UICommand, UICommandKind, UICommandPriority
                cmd = UICommand(
                    wxid=getattr(actual_driver, "_wxid", "") or "",
                    kind=UICommandKind.SEND_TEXT,
                    payload={"target": target_wxid, "text": msg_feedback},
                    priority=UICommandPriority.NORMAL,
                    timeout=20.0
                )
                ui_bus.submit(cmd)
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, ui_bus.await_result, cmd.id, 25.0)

            action = WeChatAction(
                action_type=task_type,
                priority=WeChatPriority.HIGH,
                execute_fn=do_custom_cmd,
                target_wxid=target_wxid
            )
            await scheduler.submit(action)
            await action.done_event.wait()
            success = action.result["success"]
            if not success:
                raise RuntimeError(action.result["error_msg"] or "自定义物理指令执行异常")

        else:
            raise NotImplementedError(f"不支持的待办任务类型: {task_type}")


    except Exception as e:
        error_msg = str(e)
        logger.error(f"[PromiseWorker] 任务 {task_id} 执行出错: {error_msg}")

    # 4. 任务状态处理与持久化落盘
    from .promise_executor import finalize_promise_task
    await finalize_promise_task(db, ws_manager, task, success, error_msg)
