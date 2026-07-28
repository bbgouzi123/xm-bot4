import logging
import os
import time
import tempfile
import asyncio
from typing import Any, List

logger = logging.getLogger(__name__)

async def capture_and_send_screen(driver: Any, name: str) -> bool:
    """截取当前 Windows 屏幕并物理发送给微信好友"""
    try:
        import mss
        from PIL import Image
        p = os.path.join(tempfile.gettempdir(), f'xm_live_snap_{int(time.time())}.jpg')
        
        def _snap():
            with mss.mss() as sct:
                m = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                Image.frombytes("RGB", sct.grab(m).size, sct.grab(m).bgra, "raw", "BGRX").save(p, quality=85)
                
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _snap)
        from src.utils.uia_task_runner import run_uia_with_timeout
        success = await run_uia_with_timeout(driver.SendFiles, 30.0, name, p)
        
        async def _rm(path):
            await asyncio.sleep(3)
            try:
                os.remove(path)
            except Exception:
                pass
        asyncio.create_task(_rm(p))
        return success
    except Exception as e:
        logger.error(f"[ReplyHelper] 截图发送失败: {e}")
        return False


def cleanup_temp_files(paths: List[str]):
    """延迟 60 秒异步清理临时下载物料，防止堆积占满磁盘"""
    async def _cleanup():
        await asyncio.sleep(60.0)
        for p in paths:
            try:
                if "xm_bot4_materials" in p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass
    asyncio.create_task(_cleanup())


async def handle_live_record_action(driver: Any, name: str, bus_used: bool, downloaded_paths: list) -> bool:
    """实时录屏保护模式核心联动"""
    loop = asyncio.get_event_loop()
    try:
        from src.uia.privacy_shield import get_privacy_shield
        from src.utils.material_utils import record_screen_gif
        shield = get_privacy_shield()
        was_enabled, was_record = shield.enabled, shield._record_mode
        if was_enabled:
            shield.disable()
        shield.set_record_mode(True)
        record_path = await loop.run_in_executor(None, record_screen_gif, 10, 4, 0.5)
        shield.set_record_mode(was_record)
        if was_enabled:
            shield.enable(getattr(driver, "hwnd", None))
            
        if record_path and os.path.exists(record_path):
            downloaded_paths.append(record_path)
            await asyncio.sleep(1.0)
            if bus_used:
                from src.orchestrator.ui_bus import ui_bus, UICommand, UICommandKind, UICommandPriority
                cmd = UICommand(
                    wxid=getattr(driver, "_wxid", "") or "",
                    kind=UICommandKind.SEND_FILE,
                    payload={"target": name, "file_path": record_path},
                    priority=UICommandPriority.NORMAL,
                    timeout=50.0
                )
                ui_bus.submit(cmd)
                # 修复点：正确加上 await，避免 lost future 导致事件循环崩溃
                await loop.run_in_executor(None, ui_bus.await_result, cmd.id, 55.0)
            else:
                from src.utils.uia_task_runner import run_uia_with_timeout
                await run_uia_with_timeout(driver.SendFiles, 30.0, name, record_path)
            return True
    except Exception as e:
        logger.error(f"[ReplyHelper] 实时录像联动流程异常: {e}", exc_info=True)
    return False


async def download_reply_materials(file_to_send: Any, downloaded_paths: list) -> bool:
    """提取需要发送的物料并异步下载到本地临时目录，返回是否开启实时录屏"""
    is_live_record = False
    if not file_to_send:
        return is_live_record

    if file_to_send == "__live_record__":
        is_live_record = True
    else:
        from src.utils.material_utils import resolve_and_download_material
        raw_files = []
        if isinstance(file_to_send, list):
            raw_files = file_to_send
        elif isinstance(file_to_send, str) and file_to_send.strip():
            if ',' in file_to_send:
                raw_files = [f.strip() for f in file_to_send.split(',') if f.strip()]
            elif ';' in file_to_send:
                raw_files = [f.strip() for f in file_to_send.split(';') if f.strip()]
            else:
                raw_files = [file_to_send.strip()]
        
        loop = asyncio.get_event_loop()
        for f_path in raw_files:
            loc_path = await loop.run_in_executor(None, resolve_and_download_material, f_path)
            if loc_path:
                downloaded_paths.append(loc_path)
    return is_live_record


def deduplicate_materials(downloaded_paths: list) -> list:
    """物理物料内容级防重去重，基于 MD5 和物理路径去重，杜绝任何原因导致的同图/同文件连发"""
    import os, hashlib, time
    unique_paths = []
    seen_md5s = set()
    has_error_file = False
    for path in downloaded_paths:
        if not path or not os.path.exists(path):
            continue
        if path in unique_paths:
            continue
        
        file_md5 = None
        # 针对 Windows 刚写入文件可能存在句柄未释放、锁占用导致的 PermissionError 增加 3 次退避重试
        for retry in range(3):
            try:
                hasher = hashlib.md5()
                with open(path, 'rb') as f:
                    buf = f.read(65536)
                    while len(buf) > 0:
                        hasher.update(buf)
                        buf = f.read(65536)
                file_md5 = hasher.hexdigest()
                break
            except (PermissionError, OSError) as e:
                if retry < 2:
                    time.sleep(0.05)
                else:
                    logger.warning(f"[工作流] 物理物料 MD5 读取受阻 (重试失败): {path}, error={e}")
            except Exception as hash_ex:
                logger.warning(f"[工作流] 物理物料 MD5 校验异常: {hash_ex}")
                break
                
        if file_md5:
            if file_md5 in seen_md5s:
                logger.info(f"[工作流] 过滤重复物料文件内容 (MD5={file_md5}): {path}")
                continue
            seen_md5s.add(file_md5)
            unique_paths.append(path)
        else:
            # 异常保底：若校验失败，为防同类受阻文件连发，最多放行 1 个此类文件
            if not has_error_file:
                logger.info(f"[工作流] 物理物料校验失败，仅放行首个异常文件作为保底: {path}")
                has_error_file = True
                unique_paths.append(path)
            else:
                logger.warning(f"[工作流] 物理物料校验受阻且已存在放行件，已安全拦截重复嫌疑文件: {path}")
    return unique_paths


async def resolve_and_filter_workflow_materials(
    file_to_send: Any,
    downloaded_paths: list,
    reply: str,
    account_id: str
) -> bool:
    """
    智能解析大模型承诺、下载物料、去重并最终应用行业流控过滤策略。
    返回是否为实时录像。
    """
    _MATERIAL_SEND_TRIGGERS = [
        "发资料", "发白皮书", "发文档", "把资料发给", "把文档发给", "发送文档",
        "发送文件", "发给你", "资料发给", "手册发给", "发给您", "资料发给您",
        "发一份", "发份资料", "给您发", "给你发",
    ]
    _VIDEO_SEND_TRIGGERS = [
        "录制", "实时录", "演示视频", "操作视频", "实操视频", "录个视频", "视频发给你", "录屏",
    ]
    _reply_lower = (reply or "").lower()
    _ai_promised_material = any(kw in _reply_lower for kw in _MATERIAL_SEND_TRIGGERS)
    _ai_promised_video = any(kw in _reply_lower for kw in _VIDEO_SEND_TRIGGERS)

    _effective_file_to_send = None
    if file_to_send == "__live_record__" and _ai_promised_video:
        _effective_file_to_send = file_to_send
    elif file_to_send and file_to_send != "__live_record__" and _ai_promised_material:
        _effective_file_to_send = file_to_send

    is_live_record = await download_reply_materials(_effective_file_to_send, downloaded_paths)

    # 1. 物理物料去重
    temp_paths = deduplicate_materials(downloaded_paths)
    downloaded_paths.clear()
    downloaded_paths.extend(temp_paths)

    # 2. 行业物料流控过滤
    industry_profile = None
    try:
        from src.crm.industry_config import IndustryConfigManager as _ICM
        _global_config = _ICM(account_id="global")
        inst_profile_id = ""
        try:
            from src.api.instance_settings_api import load_instance_settings
            inst_settings = load_instance_settings(account_id)
            inst_profile_id = inst_settings.get("industry_profile_id", "")
        except Exception:
            pass
        if inst_profile_id:
            industry_profile = _global_config.get_profile_by_id(inst_profile_id)
        if not industry_profile:
            industry_profile = _global_config.get_active_profile()
    except Exception as prof_err:
        logger.error(f"[工作流] 限制物料下发时加载行业配置异常: {prof_err}")

    from src.ai.prompt_builder_helpers import filter_materials
    filtered = filter_materials(downloaded_paths, industry_profile)
    downloaded_paths.clear()
    downloaded_paths.extend(filtered)

    return is_live_record
