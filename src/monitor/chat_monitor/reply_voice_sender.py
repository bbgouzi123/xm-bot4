import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

async def dispatch_voice_reply_if_enabled(
    engine: Any, name: str, reply_segments: list, downloaded_paths: list,
    voice_enabled: bool, voice_id: str, is_group: bool, wxid: str
) -> tuple[bool, bool, str]:
    """如果启用了克隆音色且符合条件，发送克隆音色语音，返回 (handled, success, error_msg)"""
    if is_group or not voice_enabled or not voice_id or not reply_segments:
        return False, False, ""

    logger.info(f"[工作流] 已开启克隆音色自动回复。音色 ID: {voice_id}")
    success = True
    error_msg = ""
    
    from src.utils.uia_task_runner import run_uia_with_timeout
    from src.utils.status_overlay import status_overlay
    from src.utils.stop_signal import stop_signal
    
    for idx, seg in enumerate(reply_segments):
        if stop_signal.is_stopped:
            success = False
            error_msg = "检测到全局停止信号"
            break
        
        status_overlay.update("发送中", f"克隆音色合成语音发送中 (第 {idx + 1}/{len(reply_segments)} 段)...", name)
        ok = await run_uia_with_timeout(
            engine.driver.send_voice_by_tts_clone, 80.0, name, seg, voice_id, wxid
        )
        if not ok:
            success = False
            error_msg = f"克隆音色语音发送失败 (第 {idx + 1} 段)"
            break
        
        if idx < len(reply_segments) - 1:
            await asyncio.sleep(1.0)
    
    if success and downloaded_paths:
        status_overlay.update("发送中", "正在直发多媒体附件...", name)
        for f_idx, path in enumerate(downloaded_paths):
            try:
                await run_uia_with_timeout(engine.driver.SendFiles, 30.0, name, path, wxid)
            except Exception as file_send_err:
                logger.error(f"[ReplyHelper] 直发附件失败: {file_send_err}")
                success = False
                error_msg = "发送附件失败"
                break

    return True, success, error_msg
