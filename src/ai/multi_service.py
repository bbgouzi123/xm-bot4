"""MultiAIService — 多能力路由 AI 服务包装器"""
from typing import Optional
from .base import AIServiceBase


class MultiAIService(AIServiceBase):
    """多能力路由 AI 服务包装器，支持文本、图片、视频分别配置不同平台"""

    def __init__(
        self,
        text_service: AIServiceBase,
        image_service: Optional[AIServiceBase] = None,
        video_service: Optional[AIServiceBase] = None,
        configs: dict = None
    ):
        super().__init__(
            token=text_service.token if text_service else "",
            platform=text_service.platform if text_service else "multi"
        )
        self.text_service = text_service
        self.image_service = image_service or text_service
        self.video_service = video_service or text_service
        self.configs = configs or {}

    @property
    def agent_id(self) -> str:
        """代理获取底层文本对话服务的默认智能体 ID"""
        if self.text_service:
            return getattr(self.text_service, 'agent_id', '') or ''
        return ''

    @agent_id.setter
    def agent_id(self, val: str):
        """代理设置底层文本对话服务的默认智能体 ID"""
        if self.text_service:
            try:
                self.text_service.agent_id = val
            except Exception:
                pass

    def get_agent_id_for_role(self, role: str) -> str:
        """从底层文本对话服务获取特定角色注册的智能体 ID"""
        if self.text_service and hasattr(self.text_service, 'get_agent_id_for_role'):
            return self.text_service.get_agent_id_for_role(role)
        return ""

    def is_configured(self) -> bool:
        return bool(self.text_service and self.text_service.is_configured())

    def register_agent(self, role: str, bot_id: str):
        if self.text_service:
            self.text_service.register_agent(role, bot_id)
        if self.image_service and self.image_service != self.text_service:
            self.image_service.register_agent(role, bot_id)
        if self.video_service and self.video_service != self.text_service and self.video_service != self.image_service:
            self.video_service.register_agent(role, bot_id)

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
        file_ids: list = None,
    ) -> dict:
        ext = self.configs.get("external_api_settings", {})
        image_model = ext.get("image_model") or self.configs.get("image_settings", {}).get("model")
        video_model = ext.get("video_model") or self.configs.get("video_settings", {}).get("model")

        # 自动注入销冠话术包逻辑 (适用于聊天、评论等所有文本对话场景)
        try:
            from src.crm.chat_knowledge_prompt import inject_sales_package_to_message
            message = inject_sales_package_to_message(message, agent_id, image_model, video_model, wxid=account_id)
        except Exception as e:
            import logging
            logging.getLogger("MultiAIService").debug(f"自动注入销冠话术异常: {e}")

        # 朋友圈画图智能体使用 start_chat 走 Coze 工作流
        _kwargs = dict(
            agent_id=agent_id, message=message, session_id=session_id,
            user_name=user_name, session_name=session_name, account_id=account_id,
            cache_session=cache_session, friend_tags=friend_tags,
            history_messages=history_messages,
        )
        # file_ids 仅在支持的服务上传递（Coze），其他平台忽略
        if file_ids:
            _kwargs["file_ids"] = file_ids

        res = None
        if agent_id and image_model and agent_id == image_model:
            if self.image_service:
                res = await self.image_service.start_chat(**_kwargs)
        elif agent_id and video_model and agent_id == video_model:
            if self.video_service:
                res = await self.video_service.start_chat(**_kwargs)
        else:
            res = await self.text_service.start_chat(**_kwargs)

        self._handle_quota_alert(res)
        return res

    def _handle_quota_alert(self, res: dict):
        if not res or res.get("success"):
            return
        err_msg = res.get("error", "未知错误")
        is_quota = any(kw in err_msg.lower() for kw in ["额度", "余额", "quota", "balance", "rate limit", "depleted", "402", "429"])
        try:
            from src.utils.alert_notifier import alert_notifier, background_tasks
            import asyncio
            title = "⚠️ AI 接口欠费/限流" if is_quota else "❌ AI 接口调用失败"
            body = "AI 会话回复失败，大模型接口欠费，请检查 AI 通道设置" if is_quota else f"AI 通道接口异常，详情: {err_msg}"
            task = asyncio.create_task(alert_notifier.send_user_notification(title=title, body=body, category="system"))
            background_tasks.add(task)
            task.add_done_callback(background_tasks.discard)
        except Exception:
            pass

    async def generate_comment(
        self,
        content: str,
        agent_id: str = "",
        session_id: str = "",
        user_name: str = "",
        session_name: str = "",
        account_id: str = "",
    ) -> dict:
        # 自动注入销冠话术包逻辑 (适用于聊天、评论等所有文本对话场景)
        try:
            from src.crm.chat_knowledge_prompt import inject_sales_package_to_message
            content = inject_sales_package_to_message(content, wxid=account_id)
        except Exception as e:
            import logging
            logging.getLogger("MultiAIService").debug(f"自动注入销冠话术异常: {e}")

        res = await self.text_service.generate_comment(
            content=content, agent_id=agent_id, session_id=session_id,
            user_name=user_name, session_name=session_name, account_id=account_id
        )
        self._handle_quota_alert(res)
        return res

    async def generate_image(self, prompt: str) -> Optional[str]:
        if self.image_service:
            return await self.image_service.generate_image(prompt)
        return await self.text_service.generate_image(prompt)

    async def generate_video(self, prompt: str) -> Optional[str]:
        if self.video_service:
            return await self.video_service.generate_video(prompt)
        return await self.text_service.generate_video(prompt)

    async def upload_file(self, file_path: str, user_id: str = "") -> dict:
        """转发文件上传到 text_service（Coze 等支持文件上传的平台）"""
        return await self.text_service.upload_file(file_path, user_id)

    async def describe_image(self, file_path: str) -> Optional[str]:
        """代理图片 Vision 分析到 text_service（需要 text_service 实现 describe_image）"""
        if hasattr(self.text_service, 'describe_image'):
            return await self.text_service.describe_image(file_path)
        # 降级：尝试 image_service
        if self.image_service and self.image_service != self.text_service and hasattr(self.image_service, 'describe_image'):
            return await self.image_service.describe_image(file_path)
        return None


    async def close(self):
        if self.text_service:
            await self.text_service.close()
        if self.image_service and self.image_service != self.text_service:
            await self.image_service.close()
        if self.video_service and self.video_service != self.text_service and self.video_service != self.image_service:
            await self.video_service.close()
