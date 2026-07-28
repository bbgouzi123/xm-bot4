"""
AI 服务工厂 — 从 xm-bot4 AIServiceFactory 逆向移植

支持的平台:
    pass
- deepseek (OpenAI 兼容): 默认，使用 DeepSeek API
- coze: 使用 Coze V3 API
- dify: 使用 Dify API

用法:
    service = AIServiceFactory.create("coze", {
        "token": "xxx",
        "agentId": "bot_xxx",
    })
    # [bytecode] result = await service.start_chat(agent_id="bot_xxx", message="你好")
"""
from typing import Optional

from .base import AIServiceBase
from .openai_compat import OpenAICompatService
from .coze_service import CozeService
from .dify_service import DifyService
from .multi_service import MultiAIService


class AIServiceFactory:
    """AI 服务工厂（对标 xm-bot4 AIServiceFactory）"""

    @staticmethod
    def create(
        service_type: str,
        config: dict,
        agent_info: dict = None,
    ) -> AIServiceBase:
        """创建 AI 服务实例

        参数:
            service_type: "deepseek" / "coze" / "dify" / "openai"
            config: platform 相关配置
            agent_info: 智能体信息（可选）

        返回: AIServiceBase 子类实例
        """
        service_type = (service_type or "deepseek").lower().strip()

        if service_type == "coze":
            return AIServiceFactory._create_coze(config, agent_info)
        elif service_type == "dify":
            return AIServiceFactory._create_dify(config)
        elif service_type in ("deepseek", "openai", "compatible"):
            return AIServiceFactory._create_openai_compat(config)
        else:
            # 默认 OpenAI 兼容
            print(f"[AI 工厂] 未知平台 '{service_type}'，使用 DeepSeek 兼容模式")
            return AIServiceFactory._create_openai_compat(config)

    @staticmethod
    def _create_coze(config: dict, agent_info: dict = None) -> CozeService:
        """创建 Coze 服务（支持多智能体角色路由）"""
        coze_settings = config.get("coze_settings", config)
        
        # 兼容新的 external_api_settings 结构
        if "external_api_settings" in config:
            ext_settings = config.get("external_api_settings", {})
            # 宽容模式：若 provider 为空但 token 存在也认为是 coze 配置
            if ext_settings.get("provider") == "coze" or (not ext_settings.get("provider") and ext_settings.get("token")):
                coze_settings = ext_settings

        token = coze_settings.get("token", "")
        if not token:
            raise ValueError("未配置 Coze Token")

        # ===== 多智能体路由：从 agents 数组构建角色映射 =====
        agents = config.get("agents", [])
        agent_map = {}       # role -> bot_id
        chat_agent_id = ""   # 聊天默认智能体 ID

        for agent in agents:
            bot_id = agent.get("botId", agent.get("id", ""))
            if not bot_id:
                continue
            role = agent.get("role", "")

            # 有明确 role 的直接注册
            if role:
                agent_map[role] = bot_id

            # 确定聊天默认智能体
            if role == "chat":
                chat_agent_id = bot_id
            elif not chat_agent_id and agent.get("isDefault"):
                chat_agent_id = bot_id

        # 如果 agents 数组中没有找到聊天智能体，回退到旧逻辑
        if not chat_agent_id:
            if agent_info:
                chat_agent_id = agent_info.get("agentId", "")
            if not chat_agent_id:
                chat_agent_id = coze_settings.get("agentId", "")
            if not chat_agent_id:
                chat_agent_id = coze_settings.get("model", "")

        # 确保 chat 角色一定有值
        if chat_agent_id and "chat" not in agent_map:
            agent_map["chat"] = chat_agent_id

        # 创建服务实例（默认 agent_id 设为聊天智能体）
        service = CozeService(token=token, agent_id=chat_agent_id)

        # 注册所有角色映射
        for role, bot_id in agent_map.items():
            service.register_agent(role, bot_id)

        # 打印路由概览
        if agent_map:
            roles_str = ", ".join(f"{r}={bid[:8]}..." for r, bid in agent_map.items())
            print(f"[AI 工厂] Coze 多智能体路由: {roles_str}")

        return service

    @staticmethod
    def _create_dify(config: dict) -> DifyService:
        """创建 Dify 服务"""
        ext = config.get("external_api_settings", {})
        is_dify = ext.get("provider") == "dify" or (not ext.get("provider") and ext.get("token") and "dify" in ext.get("base_url", ""))
        settings = ext if is_dify else config.get("dify_settings", config)
        token = settings.get("token") or settings.get("apiKey", "")
        base_url = settings.get("base_url") or settings.get("baseUrl", "")
        if not token or not base_url:
            raise ValueError("未配置 Dify 参数")
        return DifyService(token=token, base_url=base_url)

    @staticmethod
    def _create_openai_compat(config: dict) -> OpenAICompatService:
        """创建 OpenAI 兼容服务（DeepSeek 等）"""
        ext = config.get("external_api_settings", config)
        return OpenAICompatService(
            api_key=ext.get("apiKey") or ext.get("token", ""),
            base_url=ext.get("base_url") or ext.get("baseUrl", "https://api.deepseek.com"),
            model=ext.get("model", "deepseek-chat")
        )

    @staticmethod
    def create_sub_service(configs: dict, key: str) -> Optional[AIServiceBase]:
        """创建子能力服务辅助函数 (文本/图片/视频)"""
        settings = configs.get(key, {})
        provider = settings.get("provider")
        token = settings.get("token")
        if not provider or not token:
            return None

        # 封装为工厂支持的结构
        sub_config = {
            "external_api_settings": {
                "provider": provider,
                "token": token,
                "base_url": settings.get("base_url", ""),
                "model": settings.get("model", ""),
                "image_model": settings.get("model", ""),
                "video_model": settings.get("model", "")
            },
            "coze_settings": {
                "token": token,
                "agentId": settings.get("model", ""),
                "model": settings.get("model", "")
            },
            "agents": configs.get("agents", [])
        }
        return AIServiceFactory.create(provider, sub_config)

    @staticmethod
    def create_from_full_config(configs: dict, agent_info: dict = None) -> AIServiceBase:
        """从完整配置创建（读取 config.json 后调用）"""
        text_service = AIServiceFactory.create_sub_service(configs, "text_settings")
        image_service = AIServiceFactory.create_sub_service(configs, "image_settings")
        video_service = AIServiceFactory.create_sub_service(configs, "video_settings")

        # 兼容性兜底：如果 text_settings 未配置，则退回使用原来的 external_api_settings/coze_settings 逻辑
        if not text_service:
            coze = configs.get("coze_settings", {})
            dify = configs.get("dify_settings", {})
            ext = configs.get("external_api_settings", {})

            # 优先级: 配置中明确指定的 > coze > dify > deepseek
            ai_type = ext.get("provider", configs.get("ai_platform", "")).lower()
            
            # 如果通用 API 通道是平台默认的，而用户自己填了 coze_settings.token/dify_settings.token，则应该以此为主
            is_ext_custom = False
            if ext and isinstance(ext, dict):
                ext_token = ext.get("token", "")
                from src.utils.platform_defaults import _is_local_dev_token
                if ext_token and not _is_local_dev_token(ext_token):
                    try:
                        from src.utils.cloud_sync import get_cloud_client
                        client = get_cloud_client()
                        if client and getattr(client, "jwt_token", None) and ext_token == client.jwt_token:
                            pass
                        else:
                            is_ext_custom = True
                    except Exception:
                        is_ext_custom = True
            
            if not is_ext_custom:
                if coze.get("token"):
                    ai_type = "coze"
                elif dify.get("token"):
                    ai_type = "dify"

            # 兜底：如果前端 UI 显示默认的 Coze但未实际保存 provider，但存在 token，则隐式判定为 coze
            if not ai_type and ext.get("token") and not ext.get("apiKey"):
                ai_type = "coze"

            if ai_type == "coze" or (not ai_type and coze.get("token")):
                text_service = AIServiceFactory.create("coze", configs, agent_info)
            elif ai_type == "dify" or (not ai_type and dify.get("token")):
                text_service = AIServiceFactory.create("dify", configs)
            else:
                text_service = AIServiceFactory.create("deepseek", configs)

        # 返回多平台代理服务
        return MultiAIService(
            text_service=text_service,
            image_service=image_service,
            video_service=video_service,
            configs=configs
        )
