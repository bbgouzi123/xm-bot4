"""
平台托管 AI 通道 — 新用户开箱即用的默认配置

架构：
    试用用户 / 付费用户 → 默认使用平台共享 Coze Bot（零配置）
    高级用户 → 可在「系统设置」中切换为自定义 AI 通道

AI 用量由订阅层级 (ai_daily_limit) 控制，平台承担 Coze API 成本。

Token 安全：
    - 生产环境：从环境变量 / 加密配置读取
    - 开发环境：支持 .env 文件
    - 绝不硬编码在源码中
"""
import os
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


def get_platform_ai_defaults() -> Dict[str, Any]:
    """获取平台托管的默认 AI 配置（指向星码统一 AI 代理网关）

    Returns:
        与用户 external_api_settings 同结构的配置字典。
    """
    # 优先检测平台是否配置了 Coze 环境变量，如果是则优先回退到 Coze 平台托管
    coze_token = os.environ.get("XM_PLATFORM_COZE_TOKEN", "")
    coze_chat_bot = os.environ.get("XM_PLATFORM_COZE_CHAT_BOT", "")
    if coze_token and coze_chat_bot:
        return {
            "external_api_settings": {
                "provider": "coze",
                "token": coze_token,
                "agentId": coze_chat_bot,   # factory.py 的 _create_coze 优先读 agentId
                "model": coze_chat_bot,     # 兼容旧逻辑保留
                "image_model": os.environ.get("XM_PLATFORM_COZE_IMAGE_BOT", ""),
                "base_url": "",
            },
            # 同步写入 agents 数组，确保 _agent_map["chat"] 被正确注册，
            # get_agent_id_for_role("chat") 在任何调用路径下都能取到正确 bot_id
            "agents": [
                {
                    "role": "chat",
                    "botId": coze_chat_bot,
                    "isDefault": True,
                },
                *(
                    [{"role": "moment_image", "botId": os.environ.get("XM_PLATFORM_COZE_IMAGE_BOT", "")}]
                    if os.environ.get("XM_PLATFORM_COZE_IMAGE_BOT")
                    else []
                ),
            ],
            "_platform_managed": True,
        }

    import sys
    # 💡 生产环境打包后 (frozen)，或者在客户电脑上：
    # 绝对不在代码和本地配置文件中携带任何敏感平台 Token，直接连线统一网关以保证安全
    if getattr(sys, "frozen", False):
        # 尝试从 SSO 登录态获取有效的 JWT token 作为网关认证凭证（与开发模式保持一致）
        jwt_token = ""
        try:
            from src.utils.cloud_sync import get_cloud_client
            client = get_cloud_client()
            if client and getattr(client, "jwt_token", None):
                jwt_token = client.jwt_token
        except Exception:
            pass

        return {
            "external_api_settings": {
                "provider": "openai",
                "token": jwt_token or "xm_trial_token_placeholder",
                "model": "deepseek-chat",
                "base_url": "https://xmcore.top/api/xm-bot4/ai-proxy",
            },
            "_platform_managed": True,
        }

    # 其次检测平台内置 AI 代理网关的配置参数
    provider = os.environ.get("XM_PLATFORM_AI_PROVIDER", "openai").lower().strip()
    token = os.environ.get("XM_PLATFORM_AI_TOKEN", "")
    base_url = os.environ.get("XM_PLATFORM_AI_BASE_URL", "https://xmcore.top/api/xm-bot4/ai-proxy").rstrip('/')
    chat_model = os.environ.get("XM_PLATFORM_AI_MODEL", "deepseek-chat")
    image_model = os.environ.get("XM_PLATFORM_AI_IMAGE_MODEL", "")

    # 如果平台代理 Token 为空，我们尝试利用商户登录持有的 SSO JWT token，在网关处作为计费和试用校验凭证
    if not token:
        try:
            from src.utils.cloud_sync import get_cloud_client
            client = get_cloud_client()
            if client and getattr(client, "jwt_token", None):
                token = client.jwt_token
        except Exception:
            pass

    return {
        "external_api_settings": {
            "provider": provider,
            "token": token or "xm_trial_token_placeholder",
            "model": chat_model,
            "image_model": image_model,
            "base_url": base_url,
        },
        "_platform_managed": True,  # 标记：平台托管配置（前端据此显示状态）
    }


def has_platform_defaults() -> bool:
    """检查平台是否配置了默认 AI 通道"""
    return True


def _is_local_dev_token(token: str) -> bool:
    if not token:
        return False
    if token == "xm_trial_token_placeholder":
        return True
    # 核心安全屏障：任何以 ey 开头的 JWT 格式 Token 均为本平台 SSO 登录态或自签防空令牌，
    # 绝对不可能是用户配置的第三方大模型 API Key（如 sk-... 或 pat_...），一律视为非自定义配置拦截。
    if token.startswith("ey"):
        return True
    return False


def _has_valid_ai_config(config: dict) -> bool:
    # 1. 检查 Coze 专属通道
    coze = config.get("coze_settings", {})
    if coze and isinstance(coze, dict):
        token = coze.get("token", "")
        if token and not _is_local_dev_token(token):
            return True
        
    # 2. 检查细分能力通道 (文本、图片、视频)
    for key in ["text_settings", "image_settings", "video_settings"]:
        sub = config.get(key, {})
        if sub and isinstance(sub, dict):
            token = sub.get("token", "")
            if token and not _is_local_dev_token(token):
                return True
            
    # 3. 检查通用 API 通道
    ext = config.get("external_api_settings", {})
    if ext and isinstance(ext, dict):
        token = ext.get("token", "")
        if token and not _is_local_dev_token(token):
            # 排除平台托管的 SSO JWT Token 作为自定义凭证
            try:
                from src.utils.cloud_sync import get_cloud_client
                client = get_cloud_client()
                if client and getattr(client, "jwt_token", None) and token == client.jwt_token:
                    pass
                else:
                    if ext.get("provider", "").lower() == "coze":
                        if ext.get("agentId") or ext.get("model"):
                            return True
                    else:
                        return True
            except Exception:
                return True
    return False


def is_user_config_custom(user_config: dict) -> bool:
    """判断用户当前使用的是自定义配置还是平台托管

    Args:
        user_config: 用户的 global_api_config

    Returns:
        True = 用户自己填了 AI 参数（自定义模式）
        False = 用户未配置 AI 或正在使用平台默认
    """
    if user_config.get("_platform_managed") is True:
        return False

    ext = user_config.get("external_api_settings", {})
    user_token = ext.get("token", "")
    user_model = ext.get("model", "")

    # 检查是否为本地开发自签 Token
    if _is_local_dev_token(user_token):
        return False

    # 检查 coze_settings
    coze = user_config.get("coze_settings", {})
    coze_token = coze.get("token", "")
    if coze_token and not _is_local_dev_token(coze_token):
        return True

    # 检查 text_settings, image_settings, video_settings
    for section in ("text_settings", "image_settings", "video_settings"):
        sec = user_config.get(section, {})
        sec_token = sec.get("token", "")
        if sec_token and not _is_local_dev_token(sec_token):
            try:
                from src.utils.cloud_sync import get_cloud_client
                client = get_cloud_client()
                if client and getattr(client, "jwt_token", None) and sec_token == client.jwt_token:
                    continue
            except Exception:
                pass
            return True

    if not user_token or not user_model:
        return False  # 没配置，用平台默认

    # 如果是跟平台托管的网关一致，算平台托管
    platform_token = os.environ.get("XM_PLATFORM_AI_TOKEN", "")
    if platform_token and user_token == platform_token:
        return False

    # 额外通过 cloud_sync token 判定
    try:
        from src.utils.cloud_sync import get_cloud_client
        client = get_cloud_client()
        if client and getattr(client, "jwt_token", None) and user_token == client.jwt_token:
            return False
    except Exception:
        pass

    return True
