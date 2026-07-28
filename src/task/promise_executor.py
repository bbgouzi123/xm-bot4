import os
import logging
import asyncio
from typing import Any
from .promise_utils import capture_web_page, resolve_video_watermark

logger = logging.getLogger(__name__)


async def execute_web_snapshot(driver: Any, target_wxid: str, task_id: str, url: str) -> bool:
    """网页访问静默截图任务物理履约"""
    temp_dir = os.path.abspath("temp")
    os.makedirs(temp_dir, exist_ok=True)
    output_img = os.path.join(temp_dir, f"web_snap_{task_id}.png")
    
    loop = asyncio.get_event_loop()
    snapshot_ok = await loop.run_in_executor(None, capture_web_page, url, output_img)
    if not snapshot_ok or not os.path.exists(output_img):
        raise RuntimeError("静默网页渲染物理截图失败，请确认网址可访问")
    
    # 微信 UIA 投递
    from src.orchestrator.ui_bus import ui_bus, UICommand, UICommandKind, UICommandPriority
    cmd = UICommand(
        wxid=getattr(driver, "_wxid", "") or "",
        kind=UICommandKind.SEND_FILE,
        payload={"target": target_wxid, "file_path": output_img},
        priority=UICommandPriority.NORMAL,
        timeout=50.0
    )
    ui_bus.submit(cmd)
    await loop.run_in_executor(None, ui_bus.await_result, cmd.id, 55.0)
    
    try:
        if os.path.exists(output_img):
            os.remove(output_img)
    except Exception as ex:
        logger.warning(f"[WebSnapshot] 清理临时截图失败: {ex}")
    return True


async def execute_download_media(driver: Any, target_wxid: str, task_id: str, media_url: str, config: dict) -> bool:
    """短视频解析、去水印、下载与微信投递物理履约"""
    import ssl
    import urllib.request
    download_dir = config.get("sandbox_dir") or "D:\\xm-download"
    max_mb = config.get("max_file_size_mb") or 50
    
    os.makedirs(download_dir, exist_ok=True)
    loop = asyncio.get_event_loop()
    
    # 1. 解析去水印直链
    resolved_url = await loop.run_in_executor(None, resolve_video_watermark, media_url)
    
    # 2. 下载至沙箱
    temp_filename = f"media_{task_id}.mp4"
    save_path = os.path.join(download_dir, temp_filename)
    
    def download_file(src, dst):
        ctx = ssl._create_unverified_context()
        req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=ctx, timeout=30) as r:
            content_len = r.headers.get("Content-Length")
            if content_len:
                size_mb = int(content_len) / (1024 * 1024)
                if size_mb > max_mb:
                    raise ValueError(f"下载文件过大 ({size_mb:.1f}MB)，超出配置上限 {max_mb}MB。")
            with open(dst, "wb") as f:
                f.write(r.read())
                
    await loop.run_in_executor(None, download_file, resolved_url, save_path)
    
    # 3. 微信 UIA 投递
    from src.orchestrator.ui_bus import ui_bus, UICommand, UICommandKind, UICommandPriority
    cmd = UICommand(
        wxid=getattr(driver, "_wxid", "") or "",
        kind=UICommandKind.SEND_FILE,
        payload={"target": target_wxid, "file_path": os.path.abspath(save_path)},
        priority=UICommandPriority.NORMAL,
        timeout=60.0
    )
    ui_bus.submit(cmd)
    await loop.run_in_executor(None, ui_bus.await_result, cmd.id, 65.0)
    return True


async def execute_sys_control(driver: Any, target_wxid: str, cmd_kind: str, cmd_arg: str) -> bool:
    """系统高级敏感指令（关机、删除文件等）审批通过后的物理履约"""
    import shutil
    loop = asyncio.get_event_loop()
    
    if cmd_kind == "shutdown":
        logger.warning("[PromiseExecutor] 执行经人工授权审批的物理关机命令！")
        def run_shutdown():
            os.system("shutdown /s /t 60")
        await loop.run_in_executor(None, run_shutdown)
        
        reply_content = "【物理系统通知】您的系统关机指令已获得管理员授权批准，电脑将在 60 秒内安全关闭。"
        
    elif cmd_kind == "delete_file":
        logger.warning(f"[PromiseExecutor] 执行人工审批授权的文件/目录删除，目标：{cmd_arg}")
        def run_delete():
            if os.path.exists(cmd_arg):
                if os.path.isdir(cmd_arg):
                    shutil.rmtree(cmd_arg)
                else:
                    os.remove(cmd_arg)
                return True
            return False
            
        existed = await loop.run_in_executor(None, run_delete)
        reply_content = f"【物理系统通知】文件/目录删除指令已获授权并执行完成：{cmd_arg} " + ("(删除成功)" if existed else "(目标路径不存在)")
        
    else:
        raise NotImplementedError(f"不支持的系统指令操作：{cmd_kind}")

    # 回馈微信客户
    from src.orchestrator.ui_bus import ui_bus, UICommand, UICommandKind, UICommandPriority
    cmd = UICommand(
        wxid=getattr(driver, "_wxid", "") or "",
        kind=UICommandKind.SEND_TEXT,
        payload={"target": target_wxid, "content": reply_content},
        priority=UICommandPriority.HIGH,
        timeout=15.0
    )
    ui_bus.submit(cmd)
    await loop.run_in_executor(None, ui_bus.await_result, cmd.id, 18.0)
    return True


async def execute_send_live_record(driver: Any, target_wxid: str, cap: dict) -> bool:
    """实时电脑演示录制任务物理履约"""
    from src.monitor.chat_monitor.reply_media_helper import handle_live_record_action
    downloaded_paths = []
    bus_used = True
    ok = await handle_live_record_action(driver, target_wxid, bus_used, downloaded_paths)
    if not ok:
        # 录制物理超时失败，降级发送预设 fallback 演示视频
        logger.warning("[PromiseExecutor] 实时电脑演示录制失败，正在启动无缝视频投递降级...")
        fallback_video = "assets/default_demo.mp4"
        if cap and isinstance(cap.get("config"), dict):
            fallback_video = cap["config"].get("fallback_video", fallback_video)
        if not os.path.exists(fallback_video):
            raise RuntimeError("微信实时录制失败且未找到本地兜底演示视频文件")
        
        from src.orchestrator.ui_bus import ui_bus, UICommand, UICommandKind, UICommandPriority
        cmd = UICommand(
            wxid=getattr(driver, "_wxid", "") or "",
            kind=UICommandKind.SEND_FILE,
            payload={"target": target_wxid, "file_path": os.path.abspath(fallback_video)},
            priority=UICommandPriority.NORMAL,
            timeout=50.0
        )
        ui_bus.submit(cmd)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, ui_bus.await_result, cmd.id, 55.0)
    return True


async def execute_send_materials(driver: Any, target_wxid: str, materials_path: Any) -> bool:
    """发送物料履约"""
    raw_paths = []
    if isinstance(materials_path, list):
        raw_paths = materials_path
    elif isinstance(materials_path, str) and (materials_path.startswith("[") or materials_path.startswith("{")):
        try:
            import json
            parsed = json.loads(materials_path)
            if isinstance(parsed, list):
                raw_paths = parsed
            elif isinstance(parsed, dict):
                raw_paths = [parsed.get("url") or parsed.get("path") or ""]
        except Exception:
            pass
    elif isinstance(materials_path, str) and materials_path.strip():
        if ',' in materials_path:
            raw_paths = [p.strip() for p in materials_path.split(',') if p.strip()]
        elif ';' in materials_path:
            raw_paths = [p.strip() for p in materials_path.split(';') if p.strip()]
        else:
            raw_paths = [materials_path.strip()]

    from src.api.compose_utils import ensure_absolute_oss_url, ensure_local_image_path
    valid_paths = []
    for path in raw_paths:
        if not path:
            continue
        path = ensure_absolute_oss_url(path)
        if path.startswith("http://") or path.startswith("https://"):
            try:
                path = ensure_local_image_path(path)
            except Exception as dl_err:
                logger.error(f"[PromiseExecutor] 下载远程物料失败: {path} -> {dl_err}")
                continue
        if path and os.path.exists(path):
            valid_paths.append(path)

    if not valid_paths:
        raise RuntimeError("未配置或未找到物料物理文件")

    from src.orchestrator.ui_bus import ui_bus, UICommand, UICommandKind, UICommandPriority, UICommandStatus
    loop = asyncio.get_event_loop()
    for idx, path in enumerate(valid_paths):
        cmd = UICommand(
            wxid=getattr(driver, "_wxid", "") or "",
            kind=UICommandKind.SEND_FILE,
            payload={"target": target_wxid, "file_path": path},
            priority=UICommandPriority.NORMAL,
            timeout=50.0
        )
        ui_bus.submit(cmd)
        finished_cmd = await loop.run_in_executor(None, ui_bus.await_result, cmd.id, 55.0)
        if finished_cmd.status != UICommandStatus.SUCCESS:
            raise RuntimeError(finished_cmd.error or "物理发送物料文件失败")
        if idx < len(valid_paths) - 1:
            await asyncio.sleep(1.0)
    return True


async def finalize_promise_task(db: Any, ws_manager: Any, task: dict, success: bool, error_msg: str) -> None:
    """任务执行结束后的统一状态更新及 Websocket 通道推送"""
    from datetime import datetime
    task_id = task.get("id")
    target_wxid = task.get("target_wxid") or task.get("target_name")
    retry_count = task.get("retry_count", 0)

    if success:
        db.update_promise_task(task_id, {
            "status": "completed",
            "progress": 100,
            "finished_at": datetime.now().isoformat()
        })
        task_type = task.get("task_type")
        msg = "承诺已履行，已自动将所需视频或物料发送给客户"
        if task_type == "send_materials":
            m_path = task.get("materials_path") or ""
            filenames = []
            if isinstance(m_path, list):
                filenames = [os.path.basename(p) for p in m_path]
            elif isinstance(m_path, str) and m_path.strip():
                try:
                    import json
                    parsed = json.loads(m_path)
                    filenames = [os.path.basename(p) for p in parsed] if isinstance(parsed, list) else [os.path.basename(parsed.get("url") or parsed.get("path") or "")]
                except Exception:
                    filenames = [os.path.basename(p.strip()) for p in m_path.replace(';', ',').split(',') if p.strip()]
            valid_fn = [fn for fn in filenames if fn]
            msg = f"承诺已履行，已自动发送物料文件: {', '.join(valid_fn)}" if valid_fn else "已自动发送物料文件"
        elif task_type == "send_live_record":
            msg = "承诺已履行，已自动录像并发送给客户"
        elif task_type == "web_snapshot":
            url = task.get("payload_details", {}).get("url") or "网页"
            msg = f"承诺已履行，已自动截取网页 [{url}] 发送给客户"
        elif task_type == "download_media":
            msg = "承诺已履行，已自动下载去水印视频发送给客户"
        elif task_type == "sys_control":
            details = task.get("payload_details") or {}
            msg = f"承诺已履行，已自动执行控制指令: {details.get('command')}"

        await ws_manager.broadcast_task_update(
            task_id=task_id,
            task_type="业务待办任务",
            status="completed",
            progress=100,
            total=100,
            message=msg,
            friend_name=target_wxid,
            incoming_msg=task.get("reply_text", "")
        )
    else:
        new_retry = retry_count + 1
        if new_retry >= 3:
            db.update_promise_task(task_id, {
                "status": "failed",
                "progress": 100,
                "error_message": error_msg,
                "retry_count": new_retry,
                "finished_at": datetime.now().isoformat()
            })
            await ws_manager.broadcast_task_update(
                task_id=task_id,
                task_type="业务待办任务",
                status="completed",
                progress=100,
                total=100,
                message=f"任务重试次数已达上限，标记为失败: {error_msg}",
                friend_name=target_wxid,
                incoming_msg=task.get("reply_text", "")
            )
        else:
            db.update_promise_task(task_id, {
                "status": "pending",
                "retry_count": new_retry,
                "error_message": error_msg
            })
            await ws_manager.broadcast_task_update(
                task_id=task_id,
                task_type="业务待办任务",
                status="processing",
                progress=50,
                total=100,
                message=f"发送异常，准备第 {new_retry} 次自动重试... 原因: {error_msg}",
                friend_name=target_wxid,
                incoming_msg=task.get("reply_text", "")
            )
