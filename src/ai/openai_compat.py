"""
DeepSeek / OpenAI 兼容 AI 服务
已升级为继承 AIServiceBase 统一接口
"""
import httpx
import time
import logging
from typing import Optional, List

from .base import AIServiceBase

logger = logging.getLogger(__name__)


class OpenAICompatService(AIServiceBase):
    """OpenAI 兼容 API 服务（支持 DeepSeek 等）"""

    def __init__(self, api_key: str = "", base_url: str = "https://api.deepseek.com",
                 model: str = "deepseek-chat"):
        super().__init__(token=api_key, platform="deepseek")
        self.api_key = api_key
        
        base_url_cleaned = base_url.rstrip('/')
        if base_url_cleaned.endswith('/v1'):
            base_url_cleaned = base_url_cleaned[:-3]
        self.base_url = base_url_cleaned
        
        self.model = model

    def update_config(self, config: dict):
        """更新配置"""
        self.api_key = config.get('apiKey', self.api_key)
        self.token = self.api_key  # 同步基类 token
        
        base_url = config.get('baseUrl', self.base_url)
        base_url_cleaned = base_url.rstrip('/')
        if base_url_cleaned.endswith('/v1'):
            base_url_cleaned = base_url_cleaned[:-3]
        self.base_url = base_url_cleaned
        
        self.model = config.get('model', self.model)

    def is_configured(self) -> bool:
        return bool(self.api_key)

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
        """实现统一接口 — 调用 OpenAI 兼容 API"""
        # 为了避免跟上游 PromptRouter 包裹的完整业务 Prompt 产生冲突和“双重人格压制”，
        # 底层通信适配器只保留最基础的模型设定，不二次注入复杂的行业规则和限制。
        system_prompt = "你是一个高情商的 AI，正在辅助进行对话交互。仔细遵循用户发来的上下文背景和角色设定。"

        final_system_prompt = system_prompt
        final_user_message = message

        if "---" in message:
            parts = message.rsplit("---", 1)
            system_instr = parts[0].strip()
            user_msg_part = parts[1].strip()
            
            if system_instr:
                final_system_prompt = f"{system_prompt}\n\n# 角色设定与业务背景 (Persona & Context)\n{system_instr}"
                final_user_message = user_msg_part
            
        return await self.generate(final_user_message, final_system_prompt, context=history_messages)

    async def generate_comment(
        self,
        content: str,
        agent_id: str = "",
        session_id: str = "",
        user_name: str = "",
        session_name: str = "",
        account_id: str = "",
    ) -> dict:
        """实现统一接口 — 生成朋友圈评论"""
        system_prompt = (
            "你是一个微信朋友圈的智能评论助手。"
            "根据朋友圈的内容，生成一条自然、友好、有互动感的评论。"
            "要求：1) 简短，10-30字 2) 针对具体内容 3) 像真人朋友的评论 "
            "4) 有的帖子不适合评论可以回复\"无需评论\" "
            "5) 不要用太多表情符号 6) 不要敷衍"
        )
        prompt = f"好友 {user_name} 发了一条朋友圈：\n{content[:200]}\n\n请生成一条评论："
        return await self.generate(prompt, system_prompt)

    async def generate(self, message: str, system_prompt: str,
                       context: Optional[List[dict]] = None) -> dict:
        """调用 AI 生成回复（保持向后兼容）"""
        messages = [{"role": "system", "content": system_prompt}]
        if context:
            messages.extend(context)
        messages.append({"role": "user", "content": message})

        start = time.time()

        # 判断是否使用平台内置托管大模型代理通道
        use_platform_proxy = "ai-proxy" in self.base_url or "xmcore.top" in self.base_url

        # 🌟 粒度化调试日志：记录完整 OpenAI 兼容 API 载荷，包含所有 Messages
        import json
        logger.info(
            f"[OpenAI Compat Payload Debug] === 发送给 OpenAI 兼容 API 的载荷 ===\n"
            f"模型: {self.model} | 代理网关: {use_platform_proxy} | 终点 URL: {self.base_url}/v1/chat/completions\n"
            f"完整 Messages 列表:\n{json.dumps(messages, ensure_ascii=False, indent=2)}\n"
            f"========================================="
        )

        if use_platform_proxy:
            from src.utils.cloud_sync.helpers import try_load_sso_token, detect_cloud_url, generate_dev_jwt_token
            from src.utils.http_client import XMClient
            import asyncio

            token = try_load_sso_token()
            if not token:
                token = generate_dev_jwt_token()

            cloud_url = detect_cloud_url()
            # 实例化 XMClient，启用 AES-GCM 端到端加密
            xm_client = XMClient(base_url=cloud_url, token=token, timeout=30, encryption=True)

            loop = asyncio.get_event_loop()
            
            def do_request():
                return xm_client.post(
                    "/ai-proxy/v1/chat/completions",
                    body={
                        "model": self.model,
                        "messages": messages,
                    }
                )

            try:
                data = await loop.run_in_executor(None, do_request)
                if not data:
                    return {"success": False, "error": "平台大模型代理网关请求失败（未响应）", "elapsed": int((time.time() - start) * 1000)}
                
                if "error" in data:
                    error_msg = data["error"].get("message", "接口调用受限或订阅已用尽")
                    return {"success": False, "error": error_msg, "elapsed": int((time.time() - start) * 1000)}
                
            except Exception as e:
                return {"success": False, "error": f"安全传输异常: {e}", "elapsed": int((time.time() - start) * 1000)}

        else:
            if not self.api_key:
                return {"success": False, "error": "未配置 API Key"}

            last_error = ""
            data = None
            for attempt in range(2):  # 最多重试1次
                try:
                    async with httpx.AsyncClient(timeout=30) as client:
                        resp = await client.post(
                            f"{self.base_url}/v1/chat/completions",
                            headers={
                                "Authorization": f"Bearer {self.api_key}",
                                "Content-Type": "application/json",
                            },
                            json={
                                "model": self.model,
                                "messages": messages,
                                "max_tokens": 300,
                                "temperature": 0.8,
                            }
                        )
                        if resp.status_code != 200:
                            try:
                                data = resp.json()
                                error = data.get("error", {}).get("message", f"HTTP {resp.status_code}")
                            except Exception:
                                error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                            return {"success": False, "error": error, "elapsed": int((time.time() - start) * 1000)}
                        try:
                            data = resp.json()
                            break
                        except Exception as json_err:
                            return {"success": False, "error": f"解析 API 响应失败: {json_err}", "elapsed": int((time.time() - start) * 1000)}

                except httpx.TimeoutException:
                    return {"success": False, "error": "请求超时"}
                except (httpx.ConnectError, httpx.RemoteProtocolError) as e:
                    last_error = str(e)
                    if attempt == 0:
                        import asyncio
                        await asyncio.sleep(0.5)  # 短暂等待后重试
                        continue
                    return {"success": False, "error": f"连接失败: {last_error}"}
                except Exception as e:
                    return {"success": False, "error": str(e)}

        # 统一处理返回的 completions 结果（两端分流最终都会拿到标准的 JSON comps）
        elapsed = int((time.time() - start) * 1000)
        
        if not data or 'choices' not in data or not data['choices']:
            return {"success": False, "error": "AI 未返回有效内容", "elapsed": elapsed}

        content = data['choices'][0]['message']['content']
        content = self.clean_reply(content)

        # 提取 API 返回的 usage 统计 Token
        try:
            usage = data.get("usage")
            if usage:
                prompt_tokens = usage.get("prompt_tokens", 0)
                completion_tokens = usage.get("completion_tokens", 0)
                total_tokens = usage.get("total_tokens", 0)
                from src.utils.token_logger import log_token_usage
                log_token_usage(self.model, prompt_tokens, completion_tokens, total_tokens)
        except Exception:
            pass

        return {"success": True, "content": content, "elapsed": elapsed}

    async def generate_image(self, prompt: str) -> Optional[str]:
        """根据提示词调用图像生成 API，返回生成的图片 URL"""
        from .media_helper import call_generate_image
        return await call_generate_image(self.api_key, self.base_url, prompt)

    async def generate_video(self, prompt: str) -> Optional[str]:
        """根据提示词调用视频生成 API，返回生成的视频 MP4 URL"""
        from .media_helper import call_generate_video
        return await call_generate_video(self.api_key, self.base_url, prompt)

    async def describe_image(self, file_path: str) -> Optional[str]:
        """使用具备 Vision 多模态能力的大模型对图片进行内容文字描述和提取"""
        from .media_helper import call_describe_image
        return await call_describe_image(self.api_key, self.base_url, self.model, file_path)


