"""
开放接口 (Open API) 路由 — 专供第三方系统或开发者调用
支持基于 API Key 与功能锁 (openapi_access) 的鉴权校验
"""

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from typing import Optional, Dict, Any
import asyncio
import logging

from src.utils.config_cache import config_cache
from src.utils.license_validator import LicenseValidator
from src.utils.response import ok, err
from app.state import API_KEY

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/openapi/v1", tags=["openapi"])


async def verify_openapi_key(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None),
):
    """
    Open API 统一认证依赖项：
    1. 校验订阅套餐是否支持 openapi_access
    2. 校验传入的 API Key (Bearer 格式或 Header 直传)
    """
    # 1. 功能锁判断
    features = LicenseValidator.check_features()
    if not features.get("openapi_access", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="当前版本/套餐不支持开放接口(API)，请前往控制台升级套餐。",
        )

    # 2. 提取传入密钥
    incoming_key = None
    if authorization and authorization.startswith("Bearer "):
        incoming_key = authorization[7:]
    elif x_api_key:
        incoming_key = x_api_key

    if not incoming_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供 API Key (请在 Header 中传入 Authorization: Bearer <Key> 或 X-API-Key)",
        )

    # 3. 匹配配置密钥
    dev_config = config_cache.get("developer_api_config", {})
    configured_key = dev_config.get("openapi_key")
    if not configured_key:
        # 若未在设置中配置自定义密钥，降级使用默认系统密钥
        configured_key = API_KEY

    if incoming_key != configured_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的 API Key，鉴权失败。",
        )

    return incoming_key


async def _run_uia_executor(func, *args):
    """在专用 UIA 单线程池中执行 UIA 操作（COM 线程安全）"""
    from src.utils.uia_task_runner import run_in_uia_thread
    return await run_in_uia_thread(func, *args)


# ==================== 微信信息接口 ====================

@router.get("/wechat/profile")
async def get_wechat_profile(
    wxid: Optional[str] = None,
    _key: str = Depends(verify_openapi_key),
):
    """获取指定或当前活跃微信号的基本资料 (微信号、昵称、连接状态)"""
    from app.state import account_manager, driver

    inst = None
    if wxid:
        inst = account_manager.get_instance_by_wxid(wxid)
    else:
        inst = account_manager.primary_instance

    target_drv = inst.driver if inst else driver

    if not target_drv or not target_drv.is_connected():
        return err(40001, "目标微信实例未连接，请先在客户端连接微信")

    return ok({
        "connected": True,
        "wxid": inst.wxid if inst else getattr(target_drv, "bot_wxid", ""),
        "nickname": inst.nickname if inst else getattr(target_drv, "_nickname", "微信"),
        "hwnd": inst.hwnd if inst else getattr(target_drv, "_hwnd", 0),
        "avatar": inst.to_dict().get("avatar", "") if inst else "",
    })


# ==================== 通讯录接口 ====================

@router.get("/contacts")
async def get_wechat_contacts(
    wxid: Optional[str] = None,
    limit: int = 1000,
    _key: str = Depends(verify_openapi_key),
):
    """获取指定或当前活跃微信号的通讯录好友列表"""
    from app.state import account_manager
    from src.utils.contacts_cache import contacts_cache

    inst = None
    if wxid:
        inst = account_manager.get_instance_by_wxid(wxid)
    else:
        inst = account_manager.primary_instance

    account_id = inst.wxid if inst else "main"
    friends = contacts_cache.get_friends(account_id)

    if not friends:
        try:
            contacts_cache.load_from_cloud()
            friends = contacts_cache.get_friends(account_id)
        except Exception as e:
            logger.error(f"[OpenAPI] 从持久层恢复通讯录失败: {e}")

    if not friends:
        friends = []

    # 去重与系统好友过滤 (与 friend_api 保持一致)
    sys_prefixes = ("新的朋友", "公众号", "企业微信联系人", "群聊", "标签", "服务号", "我的企业", "联系人", "文件传输助手")
    dedup_map = {}

    for f in friends:
        name = f.get("name", "").strip()
        is_sys = False
        if len(name) == 1 and name.isalpha():
            is_sys = True
        for pre in sys_prefixes:
            if name.startswith(pre):
                suffix = name[len(pre):].strip()
                if not suffix or suffix.isdigit():
                    is_sys = True
                    break
            elif name == pre:
                is_sys = True
                break

        if not is_sys:
            # 优先用 wxid 标识好友，无 wxid 用昵称
            dedup_key = f.get("wxid") or name
            if dedup_key not in dedup_map:
                dedup_map[dedup_key] = f

    contacts_clean = list(dedup_map.values())
    if limit:
        contacts_clean = contacts_clean[:limit]

    return ok({
        "total": len(contacts_clean),
        "contacts": contacts_clean,
    })


# ==================== 发送消息接口 ====================

class SendMessageRequest(BaseModel):
    to_user: str
    content: str
    wxid: Optional[str] = None


@router.post("/message/send")
async def send_wechat_message(
    req: SendMessageRequest,
    _key: str = Depends(verify_openapi_key),
):
    """向好友或群发送文本消息"""
    from app.state import account_manager, driver

    inst = None
    if req.wxid:
        inst = account_manager.get_instance_by_wxid(req.wxid)
    else:
        inst = account_manager.primary_instance

    target_drv = inst.driver if inst else driver
    if not target_drv or not target_drv.is_connected():
        return err(40001, "发送失败：微信实例未连接")

    try:
        success = await _run_uia_executor(
            target_drv.send_message, req.to_user, req.content
        )
        if success:
            return ok(msg="发送成功")
        else:
            return err(50000, "发送失败：UIA 操控未成功，请检查微信窗口是否正常显现")
    except Exception as e:
        logger.error(f"[OpenAPI] 发送消息异常: {e}")
        return err(50000, f"发送失败，发生内部错误: {str(e)}")


# ==================== 前端设置与付费接口 ====================

settings_router = APIRouter(prefix="/api/openapi", tags=["openapi_settings"])


class SaveConfigRequest(BaseModel):
    openapi_key: Optional[str] = None
    webhook_url: Optional[str] = None
    enabled: Optional[bool] = None


@settings_router.get("/config")
async def get_openapi_config():
    """获取前端展示的 OpenAPI 配置及订阅状态"""
    features = LicenseValidator.check_features()
    has_access = features.get("openapi_access", False)

    dev_config = config_cache.get("developer_api_config", {}) or {}
    api_sub = config_cache.get("openapi_addon_subscription", {}) or {}

    return ok({
        "has_access": has_access,
        "openapi_key": dev_config.get("openapi_key", ""),
        "webhook_url": dev_config.get("webhook_url", ""),
        "enabled": dev_config.get("enabled", False),
        "expires_at": api_sub.get("expires_at", "") if api_sub.get("active") else "",
    })


@settings_router.post("/config")
async def save_openapi_config(req: SaveConfigRequest):
    """保存前端设置的 OpenAPI 回调及密钥配置"""
    current = config_cache.get("developer_api_config", {}) or {}
    updated = {
        "openapi_key": req.openapi_key if req.openapi_key is not None else current.get("openapi_key", ""),
        "webhook_url": req.webhook_url if req.webhook_url is not None else current.get("webhook_url", ""),
        "enabled": req.enabled if req.enabled is not None else current.get("enabled", False),
    }
    config_cache.set("developer_api_config", updated)
    return ok(msg="保存配置成功")


@settings_router.post("/buy")
async def buy_openapi_addon():
    """模拟/执行开通 API 接口订阅 (200/月)"""
    import uuid
    from datetime import datetime, timedelta, timezone

    # 计算新的过期时间 (增加30天)
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(days=30)).isoformat()

    # 1. 写入 API 增值包订阅状态
    api_sub = {
        "active": True,
        "expires_at": expires_at,
    }
    config_cache.set("openapi_addon_subscription", api_sub)

    # 2. 如果当前未配置 API 访问 Key，则自动生成一个
    current_config = config_cache.get("developer_api_config", {}) or {}
    if not current_config.get("openapi_key"):
        current_config["openapi_key"] = f"sk-xm-{uuid.uuid4().hex[:16]}"
        current_config["enabled"] = True
        config_cache.set("developer_api_config", current_config)

    # 3. 强制清理本地 Feature 缓存，使其在下一次调用 check_features() 时重新计算
    try:
        from src.utils.license_validator.features import FeaturesMixin
        FeaturesMixin._sub_cache = {}
    except Exception:
        pass

    return ok({
        "expires_at": expires_at,
        "openapi_key": current_config.get("openapi_key", ""),
    }, msg="开通成功！已为您自动生成 API 访问密钥。")

