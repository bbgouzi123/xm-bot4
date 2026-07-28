"""Coze AI 服务：start_chat / generate_comment / upload_file"""
import asyncio
import time
import json
import logging
import socket
from typing import Optional
import httpx
from .base import AIServiceBase

logger = logging.getLogger(__name__)
_COZE_HTTP_TIMEOUT = httpx.Timeout(connect=30.0, read=120.0, write=60.0, pool=30.0)

def _coze_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.RequestError, socket.error, OSError)): return True
    cause = getattr(exc, "__cause__", None)
    return _coze_retryable(cause) if cause is not None else False

class CozeService(AIServiceBase):
    """Coze V3 AI 服务，支持多智能体角色路由"""
    BASE_URL = "https://api.coze.cn/v3"
    ROLE_CHAT, ROLE_MOMENT_IMAGE, ROLE_MOMENT_VIDEO = "chat", "moment_image", "moment_video"

    def __init__(self, token: str = "", agent_id: str = ""):
        super().__init__(token=token, platform="coze")
        self.agent_id = agent_id
        self._agent_map = {}
        self._headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def update_config(self, config: dict):
        self.token = config.get("token", self.token)
        self.agent_id = config.get("agentId", self.agent_id)
        self._headers["Authorization"] = f"Bearer {self.token}"

    def is_configured(self) -> bool:
        return bool(self.token and (self.agent_id or self._agent_map.get("chat")))

    def register_agent(self, role: str, bot_id: str):
        self._agent_map[role] = bot_id
        print(f"[Coze] 注册智能体角色: {role} -> {bot_id[:8]}...")

    def get_agent_id_for_role(self, role: str) -> str:
        return self._agent_map.get(role, self.agent_id)

    def get_all_roles(self) -> dict:
        return {"default": self.agent_id, **self._agent_map}

    async def start_chat(
        self, agent_id: str = "", message: str = "", session_id: str = "", user_name: str = "",
        session_name: str = "", account_id: str = "", cache_session: bool = True,
        friend_tags: list = None, history_messages: list = None, file_ids: list = None,
    ) -> dict:
        start_time = time.time()
        bot_id = agent_id or self.agent_id
        if not bot_id or str(bot_id) == "0":
            bot_id = self.get_agent_id_for_role("chat")
        if not self.token:
            return {"success": False, "error": "未配置 Coze Token"}
        if not bot_id or str(bot_id) == "0":
            return {"success": False, "error": "未配置有效的 Coze Agent ID (Bot ID)"}

        max_retries = 4
        for attempt in range(max_retries):
            try:
                url = f"{self.BASE_URL}/chat"
                import hashlib
                if cache_session:
                    raw_id = f"{account_id}_{session_id}" if account_id and session_id else f"user_{int(time.time() * 1000)}"
                    conv_id = self._get_conversation_id(session_id) if session_id else None
                else:
                    # 🛡️ 隔离模式（cache_session=False）：强制生成随时间戳变化的一次性 user_id，使得 Coze 每次均作为全新独立会话启动
                    # 这既能绕过 Coze API 关于 auto_save_history=False 必须使用 stream 模式的强行阻断校验，
                    # 又能完全阻断云端历史上下文在下一次被重复加载堆叠，确保以微信本地最新 10-20 条真实对话为干净的 Context。
                    raw_id = f"{account_id}_{session_id}_{int(time.time() * 1000)}"
                    conv_id = None

                isolated_user_id = "u_" + hashlib.md5(raw_id.encode('utf-8')).hexdigest()[:24]

                # 🌟 如果 Coze 云端已有会话 ID，只追加最新的一条 message，避免历史重复堆叠；
                # 仅当首次对话（conv_id 为 None）时，才带入本地历史以在云端创建并继承上下文。
                additional_messages = []
                if not conv_id and history_messages:
                    for m in history_messages:
                        msg_item = dict(m)
                        if "content_type" not in msg_item:
                            msg_item["content_type"] = "text"
                        additional_messages.append(msg_item)

                # 规范化消息：分离 PromptRouter 指令和用户发言，并保留已格式化的文本上下文记忆
                final_message = message
                if "---" in message:
                    parts = message.rsplit("---", 1)
                    system_instr = parts[0].strip()
                    user_msg_part = parts[1].strip()

                    if system_instr:
                        final_message = f"[System Instructions & Context]\n{system_instr}\n\n[User Message]\n{user_msg_part}"
                    else:
                        final_message = user_msg_part

                if file_ids:
                    parts = [{"type": "text", "text": final_message}] if final_message else []
                    parts.extend([{"type": "image", "file_id": fid} for fid in file_ids])
                    additional_messages.append({
                        "role": "user", "content_type": "object_string",
                        "content": json.dumps(parts, ensure_ascii=False),
                    })
                else:
                    additional_messages.append({"role": "user", "content_type": "text", "content": final_message})

                request_data = {
                    "bot_id": bot_id, "user_id": isolated_user_id, "stream": False,
                    "auto_save_history": True,
                    "additional_messages": additional_messages,
                }
                if conv_id: 
                    url = f"{url}?conversation_id={conv_id}"

                if friend_tags or account_id:
                    request_data["parameters"] = {
                        **({"friend_tags": ",".join(friend_tags) if isinstance(friend_tags, list) else str(friend_tags)} if friend_tags else {}),
                        **({"account_id": account_id} if account_id else {})
                    }

                # 🌟 粒度化调试日志：记录完整 Payload、智能体 ID、URL 以及 API 报文结构，以备智能性审计
                logger.info(
                    f"[Coze Payload Debug] === 发送给 Coze API 的载荷 ===\n"
                    f"请求 URL: {url}\n"
                    f"会话缓存 ID: {conv_id} | 分立用户 ID: {isolated_user_id}\n"
                    f"完整 JSON 载荷:\n{json.dumps(request_data, ensure_ascii=False, indent=2)}\n"
                    f"========================================="
                )

                async with httpx.AsyncClient(timeout=120) as client:
                    resp = await client.post(url, headers=self._headers, json=request_data)
                    if resp.status_code != 200:
                        return {"success": False, "error": f"Coze 服务 HTTP 异常 {resp.status_code}", "elapsed": int((time.time() - start_time) * 1000)}
                    try:
                        data = resp.json()
                    except Exception as json_err:
                        return {"success": False, "error": f"解析 Coze 响应失败: {json_err}", "elapsed": int((time.time() - start_time) * 1000)}

                if data.get("code") != 0:
                    error_msg = data.get("msg", "Unknown Error")
                    if "4002" in str(data.get("code", "")) or "指定的会话不存在" in error_msg:
                        self._clear_conversation_cache(session_id)
                        if attempt < max_retries - 1: continue
                    return {"success": False, "error": error_msg, "elapsed": int((time.time() - start_time) * 1000)}

                chat_data = data.get("data", {})
                conversation_id = chat_data.get("conversation_id", "") or data.get("conversation_id", chat_data.get("conv_id", ""))
                chat_id = chat_data.get("id", "")
                if not chat_id or not conversation_id:
                    return {"success": False, "error": "未返回 chat_id 或 conversation_id", "elapsed": int((time.time() - start_time) * 1000)}

                result = await self._wait_for_completion(conversation_id, chat_id)
                if not result.get("success"): return result

                reply_result = await self._get_reply(conversation_id, chat_id)
                if not reply_result.get("success"): return reply_result

                if cache_session and session_id and conversation_id:
                    self._update_conversation(session_id, conversation_id)

                return {"success": True, "content": self.clean_reply(reply_result.get("content", "")), "elapsed": int((time.time() - start_time) * 1000)}
            except Exception as e:
                error_msg = str(e)
                logger.error(f"Coze 对话异常 (第{attempt + 1}次): {error_msg}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(min(2 * (2 ** attempt), 10))
                else:
                    return {"success": False, "error": error_msg, "elapsed": int((time.time() - start_time) * 1000)}
        return {"success": False, "error": "重试次数已耗尽"}

    async def generate_comment(
        self, content: str, agent_id: str = "", session_id: str = "",
        user_name: str = "", session_name: str = "", account_id: str = "",
    ) -> dict:
        profile_str = ""
        try:
            from src.crm.profile_manager import ProfileManager
            wxid = session_id[7:] if session_id.startswith('moment_') else session_id
            profile = ProfileManager(account_id or "main").get_profile(wxid) or ProfileManager(account_id or "main").get_profile(user_name)
            if profile and profile.get_tag_summary():
                profile_str = f"【客户 CRM 画像】：{profile.get_tag_summary()}\n"
        except Exception as e:
            logger.error(f"提取 CRM 画像生成评论失败: {e}")

        prompt = (f"你是一名高级销售顾问（幽默、情商高、擅长拉近关系），{profile_str}"
                  f"你的微信好友 {user_name} 刚刚发了一条朋友圈：\n“{content[:200]}”\n\n"
                  f"请根据上述画像和朋友圈内容，生成一条非常巧妙、自然、像真人的评论（控制在20字内）。不要废话，要能提供情绪价值或借机产生话题。")
        return await self.start_chat(agent_id=agent_id, message=prompt, session_id=f"moment_{session_id}", user_name=user_name, session_name=session_name, account_id=account_id, cache_session=False)

    async def upload_file(self, file_path: str, user_id: str = "") -> dict:
        import os
        if not os.path.exists(file_path):
            return {"success": False, "error": f"文件不存在: {file_path}"}
        try:
            url = f"{self.BASE_URL.replace('/v3', '/v1')}/files/upload"
            async with httpx.AsyncClient(timeout=60) as client:
                with open(file_path, "rb") as f:
                    resp = await client.post(url, headers={"Authorization": f"Bearer {self.token}"}, files={"file": (os.path.basename(file_path), f)})
                if resp.status_code != 200:
                    return {"success": False, "error": f"上传文件 HTTP 异常: {resp.status_code}"}
                data = resp.json()
            if data.get("code") != 0:
                return {"success": False, "error": data.get("msg", "上传失败")}
            return {"success": True, "file_id": data.get("data", {}).get("id", ""), "file_name": data.get("data", {}).get("file_name", os.path.basename(file_path))}
        except Exception as e:
            return {"success": False, "error": f"文件上传异常: {e}"}

    async def describe_image(self, file_path: str) -> Optional[str]:
        """使用 Coze Bot 进行图片 Vision 分析（实现委托给 media_helper.coze_describe_image）"""
        from .media_helper import coze_describe_image
        return await coze_describe_image(self, file_path)

    async def _wait_for_completion(self, conversation_id: str, chat_id: str) -> dict:
        url, query_params = f"{self.BASE_URL}/chat/retrieve", {"conversation_id": conversation_id, "chat_id": chat_id}
        try:
            async with httpx.AsyncClient(timeout=_COZE_HTTP_TIMEOUT) as client:
                for i in range(300):
                    try:
                        resp = await client.get(url, headers=self._headers, params=query_params)
                        if resp.status_code != 200:
                            await asyncio.sleep(1)
                            continue
                        data = resp.json()
                        if data.get("code") == 0:
                            status = data.get("data", {}).get("status", "")
                            if status == "completed": return {"success": True}
                            if status in ("failed", "requires_action"):
                                last_err = data.get("data", {}).get("last_error", {})
                                err_msg = last_err.get("msg", "") if isinstance(last_err, dict) else str(last_err)
                                return {"success": False, "error": f"对话状态: {status} ({err_msg})"}
                    except Exception as e:
                        logger.warning(f"检查状态异常: {type(e).__name__} - {e}")
                    
                    # 🌟 自适应等待：前 3 秒以更短的 0.5s 轮询提升灵敏度，后续 1s 轮询
                    sleep_time = 0.5 if i < 6 else 1.0
                    await asyncio.sleep(sleep_time)
        except Exception as outer_e:
            logger.error(f"[Coze] 状态轮询客户端初始化异常: {outer_e}")
            return {"success": False, "error": f"轮询异常: {outer_e}"}
        return {"success": False, "error": "Coze 回复超时 (300s)"}

    async def _get_reply(self, conversation_id: str, chat_id: str) -> dict:
        url, params, max_attempts = f"{self.BASE_URL}/chat/message/list", {"conversation_id": conversation_id, "chat_id": chat_id}, 4
        last_err: Optional[str] = None
        try:
            async with httpx.AsyncClient(timeout=_COZE_HTTP_TIMEOUT) as client:
                for attempt in range(max_attempts):
                    try:
                        resp = await client.get(url, headers=self._headers, params=params)
                        if resp.status_code != 200:
                            if attempt < max_attempts - 1:
                                await asyncio.sleep(1)
                                continue
                            return {"success": False, "error": f"获取回复 HTTP 异常 {resp.status_code}"}
                        data = resp.json()
                        if data.get("code") != 0:
                            return {"success": False, "error": data.get("msg", "获取消息失败")}
                        for msg in reversed(data.get("data", [])):
                            if msg.get("type") == "answer" and msg.get("role") == "assistant" and msg.get("content"):
                                content_val = msg["content"]
                                if msg.get("content_type") == "object_string" or (content_val.strip().startswith("[") and content_val.strip().endswith("]")):
                                    try:
                                        obj_list = json.loads(content_val)
                                        if isinstance(obj_list, list):
                                            # 严格校验是否为 Coze 专用的分片消息结构（所有元素均为字典且带有 type 键，且 type 属于 Coze 支持的媒体/文本类别）
                                            is_coze_obj = all(isinstance(item, dict) and "type" in item and item.get("type") in ("text", "image", "audio", "video", "file") for item in obj_list)
                                            if is_coze_obj:
                                                text_parts = []
                                                for item in obj_list:
                                                    if isinstance(item, dict) and item.get("type") == "text":
                                                        text_parts.append(item.get("text", ""))
                                                content_val = "".join(text_parts)
                                    except Exception as json_ex:
                                        logger.warning(f"[Coze] 解析 object_string 失败: {json_ex}, 原始内容: {content_val}")
                                return {"success": True, "content": content_val}
                        return {"success": False, "error": "未找到 AI 回复"}
                    except Exception as e:
                        last_err = f"{type(e).__name__}: {e}"
                        if _coze_retryable(e) and attempt < max_attempts - 1:
                            await asyncio.sleep(min(1.5 * (2 ** attempt), 8.0))
                            continue
                        logger.error(f"_get_reply 异常: {last_err}")
                        return {"success": False, "error": f"获取回复异常: {last_err}"}
        except Exception as outer_e:
            return {"success": False, "error": f"获取回复客户端异常: {outer_e}"}
        return {"success": False, "error": f"获取回复异常: {last_err or 'unknown'}"}

    async def generate_image(self, prompt: str) -> Optional[str]:
        """根据提示词调用图像生成 Bot，返回生成的图片 URL"""
        bot_id = self.get_agent_id_for_role("moment_image")
        if not bot_id or str(bot_id) == "0":
            bot_id = self.agent_id
        if not bot_id or str(bot_id) == "0":
            logger.error("[Coze 生图] 未配置有效的画图 Bot ID")
            return None
        
        logger.info(f"[Coze 生图] 启动生图工作流. Bot ID: {bot_id} | 提示词: {prompt}")
        
        try:
            res = await self.start_chat(
                agent_id=bot_id,
                message=prompt,
                cache_session=False
            )
            if not res or not res.get("success"):
                logger.error(f"[Coze 生图] 对话接口返回错误: {res.get('error', '未知错误')}")
                return None
            
            content = res.get("content", "")
            if not content:
                logger.error("[Coze 生图] 对话接口返回空内容")
                return None
            
            # 从返回的内容中提取图片 URL
            import re
            # 1. 匹配 Markdown 格式的图片: ![...]((url))
            m = re.search(r'!\[.*?\]\((https?://\S+?)\)', content)
            if m:
                img_url = m.group(1)
                logger.info(f"[Coze 生图] 成功从 Markdown 提取图片 URL: {img_url}")
                return img_url
            
            # 2. 匹配任何包含图片后缀或符合 coze 存储网关格式的 URL
            m = re.search(r'(https?://\S+?(?:coze|bytedance|volcengine|aliyuncs|qpic)\S+)', content)
            if m:
                img_url = m.group(1)
                logger.info(f"[Coze 生图] 成功提取到匹配图片域名的 URL: {img_url}")
                return img_url

            # 3. 兜底匹配普通 http/https 链接
            m = re.search(r'(https?://\S+)', content)
            if m:
                img_url = m.group(1)
                logger.info(f"[Coze 生图] 兜底提取普通 URL: {img_url}")
                return img_url
                
            logger.warning(f"[Coze 生图] 未能从内容中解析出图片链接. 原内容: {content[:100]}")
            return None
        except Exception as e:
            logger.error(f"[Coze 生图] 生图异常: {e}", exc_info=True)
            return None