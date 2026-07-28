import os
import asyncio
import logging
from src.task.mass_sending_helper import download_url_to_temp

logger = logging.getLogger(__name__)

async def download_and_send_media(run_uia_fn, driver, target: str, media_urls: str, check_cancelled_fn) -> None:
    if not media_urls:
        return
    urls = [u.strip() for u in media_urls.split(",") if u.strip()]
    for url in urls:
        if check_cancelled_fn():
            break
        local_p = download_url_to_temp(url)
        if local_p:
            try:
                logger.info(f"[MassSendingCore] 正在向 {target} 发送多媒体文件: {local_p}")
                file_success = await run_uia_fn(driver.SendFiles, target, local_p)
                if not file_success:
                    raise RuntimeError(f"多媒体文件 {local_p} 发送失败")
                await asyncio.sleep(1.0)
            finally:
                if os.path.exists(local_p):
                    try:
                        os.remove(local_p)
                    except Exception:
                        pass

async def execute_script_group(run_uia_fn, driver, target: str, script_group_id: str, check_cancelled_fn, db) -> None:
    sgs = db.get_all_script_groups()
    script_group = next((sg for sg in sgs if sg.get("id") == script_group_id), None)
    if not script_group:
        raise RuntimeError("未找到指定的话术组")

    nodes = script_group.get("greetings", [])
    for node_idx, node in enumerate(nodes):
        if check_cancelled_fn():
            raise RuntimeError("任务状态已改变为暂停或取消")
        
        node_type = node.get("type", "text")
        node_content = node.get("content", "")
        node_delay = node.get("delay", 2)
        
        if node_idx > 0 and node_delay > 0:
            await asyncio.sleep(node_delay)
            
        if node_type == "text" and node_content.strip():
            from src.utils.rich_reply_compiler import compile_rich_reply
            msg, compiled_paths = compile_rich_reply(node_content)
            if msg.strip():
                ok_text = await run_uia_fn(driver.send_message, target, msg)
                if not ok_text:
                    raise RuntimeError("话术组文本发送失败")
            if compiled_paths:
                for path in compiled_paths:
                    await asyncio.sleep(1.0)
                    ok_file = await run_uia_fn(driver.SendFiles, target, path)
                    if not ok_file:
                        raise RuntimeError("话术组文件发送失败")
        elif node_type == "voice" and node_content.strip():
            voice_mode = node.get("voice_mode", "favorite")
            if voice_mode == "tts_clone":
                voice_id = node.get("voice_id", "S_xiaomei")
                ok_voice = await run_uia_fn(driver.send_voice_by_tts_clone, target, node_content, voice_id)
            else:
                ok_voice = await run_uia_fn(driver.send_voice_by_favorite, target, node_content)
            if not ok_voice:
                raise RuntimeError("话术组语音发送失败")
        elif node_type == "media" and node_content.strip():
            local_p = download_url_to_temp(node_content)
            if local_p:
                try:
                    ok_file = await run_uia_fn(driver.SendFiles, target, local_p)
                    if not ok_file:
                        raise RuntimeError("话术组多媒体发送失败")
                finally:
                    if os.path.exists(local_p):
                        try:
                            os.remove(local_p)
                        except Exception:
                            pass
