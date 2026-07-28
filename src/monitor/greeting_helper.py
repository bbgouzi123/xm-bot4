import logging
import re
import os
import random
import time
import asyncio
from src.utils.material_utils import resolve_and_download_material

logger = logging.getLogger(__name__)

def check_user_interrupted(driver, wxid: str, start_time_str: str, verify_message: str) -> bool:
    """检查用户是否在我们执行破冰链期间主动发消息"""
    try:
        from src.utils.chat_history import ChatHistoryManager
        account_id = getattr(driver, 'bot_wxid', None) or "default"
        history_mgr = ChatHistoryManager(account_id)
        
        history = history_mgr.load_history(wxid)
        if not history:
            return False

        for msg in reversed(history):
            msg_time = msg.get("time_str", "")
            if msg_time < start_time_str:
                continue
            if msg.get("role") == "user":
                content = msg.get("content", "").strip()
                if verify_message and content == verify_message.strip():
                    continue
                return True
    except Exception as e:
        logger.debug(f"[GreetingEngine] 中断检测异常: {e}")
    return False

def generate_greeting(nickname: str, template: str, ai_service=None) -> str:
    """变量替换与 AI 生成插值判定"""
    if not template:
        return ""
    msg = template.replace("{name}", nickname).replace("{nickname}", nickname)
    if "{ai:" in msg and ai_service and ai_service.is_configured():
        msg = re.sub(r'\{ai:(.*?)\}', r'', msg)
    return msg

async def dispatch_message(cur_wxid: str, nickname: str, text: str, downloaded_paths: list) -> bool:
    """将准备完毕的消息或物料投递至微信客户端"""
    from src.orchestrator.ui_bus import ui_bus, UICommand, UICommandKind, UICommandPriority, UICommandStatus
    loop = asyncio.get_event_loop()
    reply_segments = [s.strip() for s in re.split(r'\n{2,}', text) if s.strip()] if text else []
    success = True

    for idx, seg in enumerate(reply_segments):
        if idx > 0:
            await asyncio.sleep(max(1.0, 0.8 + len(seg) * 0.08 + random.uniform(0.2, 0.6)))

        cmd = UICommand(wxid=cur_wxid, kind=UICommandKind.SEND_MESSAGE, payload={"target": nickname, "text": seg}, priority=UICommandPriority.NORMAL, timeout=40.0)
        ui_bus.submit(cmd)
        finished = await loop.run_in_executor(None, ui_bus.await_result, cmd.id, 45.0)
        if finished.status != UICommandStatus.SUCCESS:
            success = False

    if success and downloaded_paths:
        for path in downloaded_paths:
            await asyncio.sleep(1.5)
            file_cmd = UICommand(wxid=cur_wxid, kind=UICommandKind.SEND_FILE, payload={"target": nickname, "file_path": path}, priority=UICommandPriority.NORMAL, timeout=50.0)
            ui_bus.submit(file_cmd)
            finished = await loop.run_in_executor(None, ui_bus.await_result, file_cmd.id, 55.0)
            if finished.status != UICommandStatus.SUCCESS:
                success = False
    return success

def send_single_greeting_sync(driver, ai_service, nickname: str, template: str, wxid: str = None) -> bool:
    """原单次问候语同步发送的业务逻辑"""
    if not template:
        return False
    msg = template.replace("{name}", nickname).replace("{nickname}", nickname)
    if "[AI]" in template and ai_service and ai_service.is_configured():
         try:
             loop = asyncio.get_event_loop()
         except Exception:
             loop = asyncio.new_event_loop()
             asyncio.set_event_loop(loop)
         clean_template = template.replace("[AI]", "").replace("{nickname}", nickname)
         prompt = f"请作为一位热情的助理，为刚添加微信的好友'{nickname}'写一段简短的破冰欢迎语。可以参考这段说明：{clean_template}"
         result = loop.run_until_complete(
             ai_service.start_chat(message=prompt, session_id=f"greet_{nickname}", user_name="system", session_name="system")
         )
         if result and result.get("success") and result.get("content"):
             msg = result["content"]

    file_matches = re.findall(r'\[FILE:\s*(.*?)\]', msg)
    msg = re.sub(r'\[FILE:\s*(.*?)\]', '', msg).strip()
    downloaded_paths = []
    for mat in file_matches:
        local_path = resolve_and_download_material(mat)
        if local_path:
            downloaded_paths.append(local_path)

    from src.utils.rich_reply_compiler import compile_rich_reply
    msg, compiled_paths = compile_rich_reply(msg)
    downloaded_paths.extend(compiled_paths)

    reply_segments = [s.strip() for s in re.split(r'\n{2,}', msg) if s.strip()]
    if not reply_segments:
        reply_segments = [msg] if msg.strip() else []

    from src.orchestrator.ui_bus import ui_bus, UICommand, UICommandKind, UICommandPriority, UICommandStatus
    cur_wxid = wxid or getattr(driver, "bot_wxid", "") or "main"
    
    try:
        loop = asyncio.get_event_loop()
    except Exception:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
    success = True
    for idx, seg in enumerate(reply_segments):
        if idx > 0:
            time.sleep(max(1.0, 0.8 + len(seg) * 0.08 + random.uniform(0.2, 0.6)))
        cmd = UICommand(wxid=cur_wxid, kind=UICommandKind.SEND_MESSAGE, payload={"target": nickname, "text": seg}, priority=UICommandPriority.NORMAL, timeout=40.0)
        ui_bus.submit(cmd)
        finished = loop.run_until_complete(loop.run_in_executor(None, ui_bus.await_result, cmd.id, 45.0))
        if finished.status != UICommandStatus.SUCCESS:
            success = False

    if success and downloaded_paths:
        for path in downloaded_paths:
            time.sleep(1.5)
            file_cmd = UICommand(wxid=cur_wxid, kind=UICommandKind.SEND_FILE, payload={"target": nickname, "file_path": path}, priority=UICommandPriority.NORMAL, timeout=50.0)
            ui_bus.submit(file_cmd)
            finished = loop.run_until_complete(loop.run_in_executor(None, ui_bus.await_result, file_cmd.id, 55.0))
            if finished.status != UICommandStatus.SUCCESS:
                success = False
    return success
