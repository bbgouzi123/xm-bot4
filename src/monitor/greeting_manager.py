import logging
import re
import random
import time
import asyncio
from typing import Dict

from src.utils.material_utils import resolve_and_download_material
from .greeting_helper import (
    check_user_interrupted,
    generate_greeting,
    dispatch_message,
    send_single_greeting_sync,
)

logger = logging.getLogger(__name__)


class GreetingManager:
    """
    负责新客户添加通过后的链式破冰问候消息的生成与下发引擎。
    内置高敏感的“用户回复中断机制”判定。
    """
    def __init__(self, driver, ai_service=None):
        self.driver = driver
        self.ai_service = ai_service
        self._active_tasks: Dict[str, asyncio.Task] = {}
        self._is_running = True

    async def generate_greeting(self, nickname: str, template: str) -> str:
        """兼容老版本：变量替换与AI生成插值判定"""
        return generate_greeting(nickname, template, self.ai_service)

    def send_greeting_sync(self, nickname: str, template: str, wxid: str = None) -> bool:
        """
        兼容原同步调用接口。
        1. 若 template 命中话术组 ID，则启动异步链式破冰话术流程。
        2. 若为普通话术文本，则走单次同步发送逻辑。
        """
        if not template:
            return False

        from src.utils.db_manager import WeChatDBManager
        db = WeChatDBManager()
        script_groups = getattr(db, "_script_groups", [])
        
        matched_group = None
        for sg in script_groups:
            if sg.get("id") == template:
                matched_group = sg
                break
                
        if matched_group:
            target_wxid = wxid or nickname
            if target_wxid in self._active_tasks:
                self._active_tasks[target_wxid].cancel()
                
            try:
                loop = asyncio.get_event_loop()
            except Exception:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
            task = loop.create_task(self.start_greeting_chain(target_wxid, nickname, matched_group))
            self._active_tasks[target_wxid] = task
            logger.info(f"[GreetingEngine] 成功为新好友 {nickname}({target_wxid}) 启动异步链式问候语任务")
            return True
            
        return self._send_single_greeting_sync(nickname, template, wxid)

    async def start_greeting_chain(self, wxid: str, nickname: str, script_group: dict):
        """异步链式破冰执行流"""
        verify_message = ""
        try:
            from src.utils.db_manager import WeChatDBManager
            db = WeChatDBManager()
            for f in getattr(db, "_friend_queue", []):
                if f.get("wxid") == wxid or f.get("nickname") == nickname:
                    verify_message = f.get("verify_message", "")
                    break
        except Exception:
            pass

        nodes = script_group.get("greetings", [])
        if not nodes:
            return

        start_time_str = time.strftime("%Y-%m-%d %H:%M:%S")
        logger.info(f"[GreetingEngine] 开始为好友 {nickname}({wxid}) 执行话术链 '{script_group.get('name')}'，基准时间: {start_time_str}")

        loop = asyncio.get_event_loop()
        cur_wxid = getattr(self.driver, "bot_wxid", "") or "main"

        for idx, node in enumerate(nodes):
            node_id = node.get("id", f"node_{idx}")
            delay = node.get("delay", 2)
            node_type = node.get("type", "text")
            content_tpl = node.get("content", "")

            # 1. 拟人延迟等待与中断检测（分段睡眠）
            elapsed = 0.0
            while elapsed < delay:
                if not self._is_running:
                    return
                if self._check_user_interrupted(wxid, start_time_str, verify_message):
                    logger.info(f"[GreetingEngine] 检测到用户 {nickname}({wxid}) 主动发言，链式破冰安全中断")
                    return
                await asyncio.sleep(0.5)
                elapsed += 0.5

            if self._check_user_interrupted(wxid, start_time_str, verify_message):
                logger.info(f"[GreetingEngine] 在节点发送前检测到用户 {nickname}({wxid}) 已发言，链式破冰安全中断")
                return

            logger.info(f"[GreetingEngine] 开始执行节点: {node_id} ({node_type})")

            try:
                # 3.1 AI 润色和对话生成节点
                if node_type == "ai_chat" or "[AI]" in content_tpl or "{ai:" in content_tpl:
                    msg = await self._generate_ai_node_content(nickname, node, wxid)
                else:
                    msg = content_tpl.replace("{name}", nickname).replace("{nickname}", nickname)

                # 3.2 物料预备与下载
                downloaded_paths = []
                if node_type == "voice":
                    if msg.strip():
                        from src.orchestrator.ui_bus import ui_bus, UICommand, UICommandKind, UICommandPriority, UICommandStatus
                        voice_mode = node.get("voice_mode", "favorite")
                        voice_id = node.get("voice_id", "S_xiaomei")
                        
                        payload = {
                            "target": nickname,
                            "voice_mode": voice_mode,
                        }
                        if voice_mode == "tts_clone":
                            payload["text"] = msg.strip()
                            payload["voice_id"] = voice_id
                        else:
                            payload["favorite_name"] = msg.strip()
                            
                        voice_cmd = UICommand(
                            wxid=cur_wxid,
                            kind=UICommandKind.SEND_VOICE,
                            payload=payload,
                            priority=UICommandPriority.NORMAL,
                            timeout=60.0
                        )
                        ui_bus.submit(voice_cmd)
                        finished = await loop.run_in_executor(None, ui_bus.await_result, voice_cmd.id, 65.0)
                        if finished.status != UICommandStatus.SUCCESS:
                            logger.error(f"[GreetingEngine] 发送语音节点 {node_id} 失败")
                    msg = ""
                elif node_type == "media":
                    local_path = await loop.run_in_executor(None, resolve_and_download_material, msg)
                    if local_path:
                        downloaded_paths.append(local_path)
                    msg = ""
                else:
                    file_matches = re.findall(r'\[FILE:\s*(.*?)\]', msg)
                    msg = re.sub(r'\[FILE:\s*(.*?)\]', '', msg).strip()
                    for mat in file_matches:
                        local_path = await loop.run_in_executor(None, resolve_and_download_material, mat)
                        if local_path:
                            downloaded_paths.append(local_path)

                    from src.utils.rich_reply_compiler import compile_rich_reply
                    msg, compiled_paths = compile_rich_reply(msg)
                    downloaded_paths.extend(compiled_paths)

                # 3.3 投递
                if msg or downloaded_paths:
                    await self._dispatch_message(cur_wxid, nickname, msg, downloaded_paths)
            except Exception as e:
                logger.error(f"[GreetingEngine] 节点 {node_id} 投递异常: {e}", exc_info=True)

        logger.info(f"[GreetingEngine] 话术链 '{script_group.get('name')}' 已执行完毕")
        self._active_tasks.pop(wxid, None)

    def _check_user_interrupted(self, wxid: str, start_time_str: str, verify_message: str) -> bool:
        return check_user_interrupted(self.driver, wxid, start_time_str, verify_message)

    async def _generate_ai_node_content(self, nickname: str, node: dict, wxid: str) -> str:
        """通过 AI 润色或生成特定节点的消息内容，并集成 PromptRouter 路由 A/B 测试"""
        content_tpl = node.get("content", "")
        if not self.ai_service or not self.ai_service.is_configured():
            return content_tpl.replace("{name}", nickname).replace("{nickname}", nickname)

        agent_id = node.get("agent_id") or ""
        if not agent_id and hasattr(self.ai_service, 'get_agent_id_for_role'):
            agent_id = self.ai_service.get_agent_id_for_role("chat")

        account_id = getattr(self.driver, 'bot_wxid', None) or "default"

        from src.ai.prompt_router import PromptRouter
        from src.crm.industry_config import IndustryConfigManager
        
        _global_config = IndustryConfigManager(account_id="global")
        inst_profile_id = ""
        try:
            from src.api.instance_settings_api import load_instance_settings
            inst_settings = load_instance_settings(account_id)
            inst_profile_id = inst_settings.get("industry_profile_id", "")
        except Exception as e:
            logger.warning(f"[CRM] 破冰时获取微信专属行业ID失败: {e}")

        industry_profile = None
        if inst_profile_id:
            industry_profile = _global_config.get_profile_by_id(inst_profile_id)
        if not industry_profile:
            industry_profile = _global_config.get_active_profile()
        verify_message = "我是新朋友"
        
        fixed_reply, ai_prompt, file_to_send = PromptRouter.route(
            intent="friend_accepted",
            message=verify_message,
            industry_config=industry_profile,
            chat_round=1,
            history_messages=[],
            session_id=wxid,
            account_id=account_id
        )

        if fixed_reply:
            return fixed_reply.replace("{name}", nickname).replace("{nickname}", nickname)

        prompt = ai_prompt if ai_prompt else content_tpl.replace("{nickname}", nickname)
        if not ai_prompt:
            if "{ai:" in prompt:
                ai_matches = re.findall(r'\{ai:(.*?)\}', prompt)
                if ai_matches:
                    prompt = ai_matches[0].strip()
            elif "[AI]" in prompt:
                prompt = f"请作为一位热情的助理，为刚添加微信的好友'{nickname}'写一段简短的破冰欢迎语。可以参考这段说明：{prompt.replace('[AI]', '')}"

        result = await self.ai_service.start_chat(
            agent_id=agent_id,
            message=prompt,
            session_id=f"greet_{wxid}",
            user_name="system",
            session_name="system",
            account_id=account_id
        )
        if result and result.get("success") and result.get("content"):
            return result["content"]
        return content_tpl.replace("{name}", nickname).replace("{nickname}", nickname)

    async def _dispatch_message(self, cur_wxid: str, nickname: str, text: str, downloaded_paths: list) -> bool:
        return await dispatch_message(cur_wxid, nickname, text, downloaded_paths)

    def _send_single_greeting_sync(self, nickname: str, template: str, wxid: str = None) -> bool:
        return send_single_greeting_sync(self.driver, self.ai_service, nickname, template, wxid)

    def __del__(self):
        self._is_running = False
        if hasattr(self, "_active_tasks"):
            for t in list(self._active_tasks.values()):
                try:
                    t.cancel()
                except Exception:
                    pass
