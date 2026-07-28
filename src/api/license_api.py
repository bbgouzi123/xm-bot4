"""
许可证 API 路由 — 本地中转层
前端通过这些 API 操作许可证，后端转发到 XM-User 统一授权平台
"""

from fastapi import APIRouter, Request
from typing import Dict, Any
import asyncio

from src.utils.license_validator import LicenseValidator
from src.utils.response import ok, err, ok_msg

router = APIRouter()


# ==================== V2/V3 订阅制 API ====================

@router.get("/api/subscription/status")
@router.get("/api/license/status")  # 兼容性保留路径，逻辑已切到订阅制
async def subscription_status():
    """
    V2/V3 订阅校验 — 基于 user_id（优先）或降级到微信绑定模式
    前端统一调这个接口获取当前账号的授权状态
    """
    # check_subscription 内部使用 urllib 同步 HTTP，必须在线程池执行以免阻塞事件循环
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, LicenseValidator.check_subscription)
    return ok(result)


@router.post("/api/subscription/refresh")
async def refresh_subscription():
    """
    强制刷新订阅状态（当用户在前端完成支付/升级后调用）
    """
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, lambda: LicenseValidator.check_subscription(force=True))
    return ok(result)


@router.get("/api/subscription/features")
async def subscription_features():
    """
    Feature Gate 功能锁 — 返回当前用户可用的功能列表
    用于前端和后端的功能开关判断（如并发数、AI 额度等）
    """
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, LicenseValidator.check_features)
    return ok(result)


@router.get("/api/subscription/unified")
async def unified_status():
    """
    统一状态接口 — 返回包含订阅信息与机器码的聚合对象
    """
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, LicenseValidator.get_unified_status)
    return ok(result)


@router.post("/api/subscription/unbind-wechat")
async def unbind_wechat_subscription(request: Request):
    """
    解绑微信号（受订阅套餐中的 max_unbinds 次数限制）
    """
    body = await request.json()
    wechat_id = (body.get("wechat_id") or "").strip()
    if not wechat_id:
        return err(40000, "wechat_id 不能为空")

    user_id = LicenseValidator._get_sso_user_id()
    if not user_id:
        return err(40001, "未登录，无法执行解绑")

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, lambda: LicenseValidator._http_request("POST", "/api/subscription/unbind-wechat", {
        "user_id": user_id,
        "wechat_id": wechat_id,
    }))

    if result and result.get("success") is not False:
        data = result.get("data") or result
        return ok({
            "message": data.get("message", "解绑成功"),
            "wechat_ids": data.get("wechat_ids", []),
            "used_unbinds": data.get("used_unbinds", 0),
        })
    else:
        msg = result.get("message", "解绑失败") if result else "网络不可达，请稍后再试"
        return err(40302, msg)


@router.get("/api/subscription/ai-quota")
async def get_ai_quota():
    """
    查询当前用户今日 AI 使用配额
    """
    user_id = LicenseValidator._get_sso_user_id()
    if not user_id:
        return err(40001, "未登录")

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, lambda: LicenseValidator._http_request("POST", "/api/ai/quota/_query", {"user_id": user_id}))
    if result and result.get("success") is not False:
        data = result.get("data") or result
        return ok({
            "plan_name": data.get("plan_name", "试用版"),
            "daily_limit": data.get("daily_limit", 30),
            "used_today": data.get("used_today", 0),
            "remaining": data.get("remaining", 0),
            "unlimited": data.get("unlimited", False),
            "exhausted": data.get("exhausted", False),
        })
    else:
        # 降级：从本地订阅缓存读取
        sub = await loop.run_in_executor(None, LicenseValidator.check_subscription)
        limit = sub.get("ai_daily_limit", 30)
        local_used = sub.get("_local_ai_used", 0)
        return ok({
            "plan_name": sub.get("plan_name", "试用版"),
            "daily_limit": limit,
            "used_today": local_used,
            "remaining": max(0, limit - local_used) if limit > 0 else -1,
            "unlimited": limit == -1,
            "exhausted": limit > 0 and local_used >= limit,
            "_offline": True,
        })


@router.post("/api/ai/report-usage")
async def report_ai_usage_local(request: Request):
    """
    内部上报：AI 成功调用后，同步到 xm-user 端做权威计数
    """
    body = await request.json()
    user_id = LicenseValidator._get_sso_user_id()
    if not user_id:
        return err(40001, "未登录，无法上报")

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, lambda: LicenseValidator._http_request("POST", "/api/ai/report-usage", {
        "user_id": user_id,
        "platform": body.get("platform", "coze"),
        "model": body.get("model", "unknown"),
        "message_length": body.get("message_length", 0),
        "response_length": body.get("response_length", 0),
    }))

    if result and result.get("success") is not False:
        data = result.get("data") or result
        return ok({
            "recorded": True,
            "used_today": data.get("used_today", 0),
            "daily_limit": data.get("daily_limit", 30),
            "remaining": data.get("remaining", 0),
            "exhausted": data.get("exhausted", False),
        })
    else:
        return ok({"recorded": False, "reason": "上报失败"})
