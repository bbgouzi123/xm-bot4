"""
Dify AI 服务 — 从 xm-bot4 DifyService 逆向移植

功能:
    pass
- start_chat(): 发起对话（blocking 模式）
- generate_comment(): 朋友圈 AI 评论
- upload_file(): 文件上传（仅支持文档类型）
"""
import time
import httpx
import logging
from typing import Optional

from .base import AIServiceBase

logger = logging.getLogger(__name__)


class DifyService(AIServiceBase):
    """Dify AI 服务（对标 xm-bot4 DifyService）"""

    def __init__(self, token: str = "", base_url: str = ""):
        super().__init__(token=token, platform="dify")
        self.base_url = base_url.rstrip("/") if base_url else ""
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def update_config(self, config: dict):
        """更新配置"""
        self.token = config.get("token", self.token)
        self.base_url = config.get("baseUrl", self.base_url).rstrip("/")
        self._headers["Authorization"] = f"Bearer {self.token}"

    def is_configured(self) -> bool:
        return bool(self.token and self.base_url)

    async def start_chat(
        self,
        agent_id: str = "",
        message: str = "",
        session_id: str = "",
        user_name: str = "",
        session_name: str = "",
        account_id: str = "",
        cache_session: bool = True,
        friend_tags: list = None,
        history_messages: list = None,
        **kwargs,
    ) -> dict:
        """发起 Dify 对话（blocking 模式）

        对标 xm-bot4 DifyService.start_chat
        POST /{base_url}/chat-messages
        """
        start = time.time()

        if not self.token or not self.base_url:
            return {"success": False, "error": "未配置 Dify Token 或 Base URL"}

        try:
            user_id = f"user_{account_id}" if account_id else f"user_{session_id}"

            # 规范化消息：分离 PromptRouter 指令和用户发言，并清除多余的文本上下文记忆
            final_message = message
            if "---" in message:
                parts = message.rsplit("---", 1)
                system_instr = parts[0].strip()
                user_msg_part = parts[1].strip()

                import re
                system_instr = re.sub(r'\n\n\[上下文(?:记忆)?\].*?(?=\n\n|\Z)', '', system_instr, flags=re.DOTALL)
                if system_instr:
                    final_message = f"[System Instructions & Context]\n{system_instr}\n\n[User Message]\n{user_msg_part}"
                else:
                    final_message = user_msg_part

            # 如果提供本地历史记录，取消服务端的黑盒会话，改为在提示语内拼接
            if history_messages and not cache_session:
                context_str = "\n".join([f"{msg['role']}: {msg['content']}" for msg in history_messages])
                if context_str:
                    final_message = f"最近历史对话：\n{context_str}\n\n请针对我最新的发言进行回复：\nuser: {final_message}"

            # 构建消息
            payload = {
                "inputs": {},
                "query": final_message,
                "response_mode": "blocking",
                "user": user_id,
            }

            # 多轮对话 — 只有在明确开启同步后端 cache 时才传 conversation_id
            if cache_session and session_id:
                conv_id = self._get_conversation_id(session_id)
                if conv_id:
                    payload["conversation_id"] = conv_id

            url = f"{self.base_url}/chat-messages"

            # 🌟 粒度化调试日志：记录完整 Dify 载荷
            import json
            logger.info(
                f"[Dify Payload Debug] === 发送给 Dify API 的载荷 ===\n"
                f"请求 URL: {url}\n"
                f"完整 JSON 载荷:\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
                f"========================================="
            )

            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(url, headers=self._headers, json=payload)
                if resp.status_code != 200:
                    try:
                        data = resp.json()
                        error = data.get("message", data.get("msg", f"HTTP {resp.status_code}"))
                    except Exception:
                        error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    return {"success": False, "error": error, "elapsed": int((time.time() - start) * 1000)}
                try:
                    data = resp.json()
                except Exception as json_err:
                    return {"success": False, "error": f"解析 Dify 响应失败: {json_err}", "elapsed": int((time.time() - start) * 1000)}

            elapsed = int((time.time() - start) * 1000)

            # 提取回复
            answer = data.get("answer", "")
            new_conv_id = data.get("conversation_id", "")

            # 保存会话
            if cache_session and session_id and new_conv_id:
                self._update_conversation(session_id, new_conv_id)

            if answer:
                content = self.clean_reply(answer)
                return {"success": True, "content": content, "elapsed": elapsed}
            else:
                return {"success": False, "error": "Dify 未返回回复", "elapsed": elapsed}

        except httpx.TimeoutException:
            return {"success": False, "error": "Dify 请求超时"}
        except Exception as e:
            return {"success": False, "error": f"Dify 调用异常: {e}"}

    async def generate_comment(
        self,
        content: str,
        agent_id: str = "",
        session_id: str = "",
        user_name: str = "",
        session_name: str = "",
        account_id: str = "",
    ) -> dict:
        """生成朋友圈 AI 评论（接入 CRM 画像）"""
        profile_str = ""
        try:
            from src.crm.profile_manager import ProfileManager
            wxid = session_id.replace('moment_', '') if session_id.startswith('moment_') else session_id
            profile_mgr = ProfileManager(account_id or "main")
            import logging
            profile = profile_mgr.get_profile(wxid) or profile_mgr.get_profile(user_name)
            if profile:
                tag_summary = profile.get_tag_summary()
                if tag_summary:
                    profile_str = f"【客户 CRM 画像】：{tag_summary}\n"
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"提取 CRM 画像生成评论失败: {e}")

        prompt = (f"你是一名高级销售顾问（幽默、情商高、擅长拉近关系），{profile_str}"
                  f"你的微信好友 {user_name} 刚刚发了一条朋友圈：\n"
                  f"“{content[:200]}”\n\n"
                  f"请根据上述画像和朋友圈内容，生成一条非常巧妙、自然、像真人的评论（控制在20字内）。不要废话，要能提供情绪价值或借机产生话题。")

        return await self.start_chat(
            agent_id=agent_id,
            message=prompt,
            session_id=f"moment_{session_id}",
            user_name=user_name,
            session_name=session_name,
            account_id=account_id,
            cache_session=False,
        )

    async def upload_file(self, file_path: str, user_id: str = "") -> dict:
        """上传文件到 Dify（仅支持文档类型）

        对标 xm-bot4 DifyService.upload_file
        """
        import os

        if not os.path.exists(file_path):
            return {"success": False, "error": f"文件不存在: {file_path}"}

        # Dify 目前只支持文档类型文件
        allowed_ext = {".txt", ".md", ".pdf", ".doc", ".docx", ".csv", ".json"}
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in allowed_ext:
            return {
                "success": False,
                "error": f"Dify 目前只支持文档文件，不支持: {ext}",
            }

        try:
            url = f"{self.base_url}/files/upload"
            uid = f"user_{user_id}" if user_id else "user_default"
            headers = {"Authorization": f"Bearer {self.token}"}

            async with httpx.AsyncClient(timeout=60) as client:
                with open(file_path, "rb") as f:
                    resp = await client.post(
                        url,
                        headers=headers,
                        files={"file": f},
                        data={"user": uid},
                    )
                if resp.status_code not in (200, 201):
                    try:
                        data = resp.json()
                        error = data.get("message", "上传失败")
                    except Exception:
                        error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    return {"success": False, "error": error}
                try:
                    data = resp.json()
                except Exception as json_err:
                    return {"success": False, "error": f"解析上传文件响应失败: {json_err}"}

        except Exception as e:
            return {"success": False, "error": f"文件上传异常: {e}"}
