"""
码上查企企业数据 → 加好友队列：搜索代理、联系人规范化、会话暂存
"""
from __future__ import annotations

import json
import logging
import os
import uuid
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from src.utils.http_client import XMClient

logger = logging.getLogger(__name__)

SESSION_SUBDIR = "enterprise_sessions"


def session_dir(base_upload: Path) -> Path:
    d = base_upload / SESSION_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def ui_row(raw: Dict[str, Any]) -> Dict[str, Any]:
    """前端表格展示用：脱敏敏感字段，防止未支付前数据泄露"""
    o = {}
    # 允许公开展示的字段
    o["id"] = str(raw.get("id") or "")
    o["company_name"] = (raw.get("company_name") or raw.get("name") or "").strip()
    
    # 敏感字段：手动脱敏（优先信任已脱敏或占位符数据）
    raw_phone = str(raw.get("primary_phone") or raw.get("phone") or raw.get("mobile") or "").strip()
    phone_digits = "".join(c for c in raw_phone if c.isdigit())
    
    if len(phone_digits) == 11 and phone_digits.startswith("1"):
        # 如果是完整号码，执行脱敏
        o["primary_phone"] = phone_digits[:3] + "****" + phone_digits[-4:]
    elif "*" in raw_phone or "—" in raw_phone:
        # 如果已经是脱敏格式或占位符，直接透传
        o["primary_phone"] = raw_phone
    else:
        # 真正为空或无效时
        o["primary_phone"] = "—"
        
    legal = (raw.get("legal_person") or raw.get("legal_representative") or "").strip()
    if legal:
        o["legal_person"] = legal[0] + "*" * (len(legal) - 1)
    else:
        o["legal_person"] = "—"
        
    # 行业、地区、状态也进行脱敏，保留首字增加吸引力
    industry = (raw.get("industry") or raw.get("industry_name") or "").strip()
    o["industry"] = industry[0] + "**" if industry else "—"
    
    region = (raw.get("region") or raw.get("registered_address") or "").strip()
    o["region"] = region[0] + "**" if region else "—"
    
    status = (raw.get("reg_status") or raw.get("business_status") or "").strip()
    o["reg_status"] = status[0] + "**" if status else "—"
    
    return o


DEFAULT_INDUSTRIES = [
    {"id": "sys_001", "name": "批发和零售业"},
    {"id": "sys_002", "name": "信息传输、软件和信息技术服务业"},
    {"id": "sys_003", "name": "居民服务、修理和其他服务业"},
    {"id": "sys_004", "name": "建筑业"},
    {"id": "sys_005", "name": "租赁和商务服务业"},
    {"id": "sys_006", "name": "制造业"},
    {"id": "sys_007", "name": "住宿和餐饮业"},
    {"id": "sys_008", "name": "文化、体育和娱乐业"},
]


def _detect_mashangchaqi_origin() -> str:
    """
    自动检测码上查企 API 服务地址：
    1. 优先使用环境变量 MASHANGCHAQI_API_ORIGIN 覆盖。
    2. 生产打包环境 (is_frozen) -> 走本地反代 (proxy.py 已注册 /api/xm-mashangchaqi 路由)
       本地反代会自动转发到公网网关，避免 Python 端明文直连公网被加密中间件 406 拦截。
    3. 普通开发环境 -> 连接本地开发微服务
    """
    import sys
    from app.constants import BOT4_LOCAL_ORIGIN

    env_origin = os.getenv("MASHANGCHAQI_API_ORIGIN")
    if env_origin:
        return env_origin.rstrip("/")

    is_frozen = getattr(sys, "frozen", False)
    if is_frozen and ("--dev" not in sys.argv):
        return f"{BOT4_LOCAL_ORIGIN}/api/xm-mashangchaqi"
    
    # 兼容环境变量配置
    mode = os.getenv("XM_CROSS_SERVICE_MODE", "").strip().lower()
    if mode in {"prod", "online", "remote"}:
        return f"{BOT4_LOCAL_ORIGIN}/api/xm-mashangchaqi"
    if os.getenv("NODE_ENV") == "production" or os.getenv("XM_ENV") == "production":
        return f"{BOT4_LOCAL_ORIGIN}/api/xm-mashangchaqi"

    return "http://127.0.0.1:42032"


async def get_enterprises_by_ids(
    *, ids: List[str], auth_header: Optional[str] = None
) -> List[Dict[str, Any]]:
    """通过 ID 列表获取完整企业详情（客户端 AES-GCM 加密传输）"""
    base = _detect_mashangchaqi_origin()
    token = ""
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]

    client = XMClient(base, token=token, encryption=True)
    try:
        loop = asyncio.get_running_loop()
        body = await loop.run_in_executor(
            None,
            lambda: client.post("/api/v1/enterprises/batch-get", {"ids": ids})
        )
        if isinstance(body, dict) and body.get("code") in (0, 20000) and "data" in body:
            return body["data"]
        return []
    except Exception as e:
        logger.error("[enterprise] batch-get failed: %s", e)
        return []


def _normalize_phone(raw: str) -> str:
    if not raw:
        return ""
    digits = "".join(c for c in str(raw) if c.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        return digits
    return ""


def enterprise_row_to_contact(row: Dict[str, Any], row_index: int = 0) -> Optional[Dict[str, Any]]:
    """转换为 excel_parser / friend_queue 使用的联系人结构"""
    phone = _normalize_phone(
        row.get("primary_phone")
        or row.get("phone")
        or row.get("mobile")
        or row.get("contact_phone")
        or ""
    )
    name = (row.get("name") or row.get("company_name") or "").strip()
    legal = (row.get("legal_person") or row.get("legal_representative") or "").strip()
    if not phone:
        return None
    if not name:
        return None
    extra = {k: v for k, v in row.items() if k not in ("primary_phone", "phone", "mobile")}
    return {
        "id": str(row.get("id") or ""),
        "phone": phone,
        "primary_phone": phone,
        "phones": [phone] if phone else [],
        "company_name": name,
        "legal_person": legal,
        "wechat_id": (row.get("wechat_id") or "").strip(),
        "industry": (row.get("industry") or "").strip(),
        "employee_count": str(row.get("employee_count") or ""),
        "extra_fields": extra,
        "row_index": row_index,
    }


async def search_enterprises(
    *,
    auth_header: Optional[str],
    q: str = "",
    region: str = "",
    industry: str = "",
    reg_status: str = "",
    page: int = 1,
    page_size: int = 20,
    hide_purchased: bool = False,
) -> Dict[str, Any]:
    """代理请求码上查企 REST API（自动执行客户端 AES-GCM 加密传输）"""
    base = _detect_mashangchaqi_origin()
    token = ""
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]

    client = XMClient(base, token=token, encryption=True)

    logger.info("[enterprise] 查企来源=%s, token=%s, crypto_lib=%s",
                base, "有" if token else "无",
                "可用" if client.encryption else "不可用(已降级明文)")
    
    params: Dict[str, Any] = {
        "q": q or "",
        "page": max(1, page),
        "page_size": max(1, min(50, page_size)),
    }
    if region:
        params["region"] = region
    if industry:
        params["industry"] = industry
    if reg_status:
        params["reg_status"] = reg_status
    if hide_purchased:
        params["hide_purchased"] = "true"

    try:
        loop = asyncio.get_running_loop()
        body = await loop.run_in_executor(
            None,
            lambda: client.get("/api/v1/enterprises", params=params)
        )
        if not body:
            logger.warning("[enterprise] XMClient 返回 None (连接失败或响应解析失败)")
            return {"list": [], "total": 0, "page": page, "page_size": page_size, "error": "无法连接到查企服务，请检查网络或配置"}

        code = body.get("code") if isinstance(body, dict) else None
        
        logger.info("[enterprise] XMClient 原始返回: type=%s, code=%s, keys=%s",
                    type(body).__name__, code,
                    list(body.keys())[:10] if isinstance(body, dict) else "N/A")
        
        # 标准成功响应处理 (支持 0 和 20000 两种常见业务码)
        if isinstance(body, dict) and code in (0, 20000) and "data" in body:
            data = body["data"]
            # 支持 { data: { list: [], total: 0 } } 和 { data: [] } 两种结构
            if isinstance(data, dict) and "list" in data:
                logger.info("[enterprise] ✅ 标准响应: total=%s, list_len=%s",
                            data.get("total"), len(data.get("list", [])))
                return data
            if isinstance(data, list):
                logger.info("[enterprise] ✅ 数组响应: list_len=%s", len(data))
                return {"list": data, "total": len(data), "page": page, "page_size": page_size}
            logger.warning("[enterprise] ⚠ data 非 dict/list: type=%s", type(data).__name__)
            return data

        # 认证/授权类错误：透传具体错误信息给前端
        if isinstance(body, dict) and code and code >= 40000:
            msg = body.get("msg", "服务端业务错误")
            logger.warning("[enterprise] 查企服务返回业务错误 [%s]: %s", code, msg)
            return {"list": [], "total": 0, "page": page, "page_size": page_size, "error": msg}

        # 直接透传 list 结构
        if isinstance(body, dict) and "list" in body:
            logger.info("[enterprise] 直接 list 结构: list_len=%s", len(body.get("list", [])))
            return body

        logger.error("[enterprise] 接口格式异常: %s", str(body)[:300])
        return {"list": [], "total": 0, "page": page, "page_size": page_size, "error": "接口返回格式不符合预期"}

    except Exception as e:
        logger.exception("[enterprise] 搜索请求发生未知异常")
        return {"list": [], "total": 0, "page": page, "page_size": page_size, "error": str(e)}


async def get_industries(*, auth_header: Optional[str] = None) -> List[Dict[str, Any]]:
    """获取码上查企行业列表（客户端 AES-GCM 加密传输）"""
    base = _detect_mashangchaqi_origin()
    token = ""
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]

    client = XMClient(base, token=token, encryption=True)
    try:
        loop = asyncio.get_running_loop()
        body = await loop.run_in_executor(
            None,
            lambda: client.get("/api/v1/industries")
        )
        if isinstance(body, dict) and body.get("code") in (0, 20000) and "data" in body:
            return body["data"]
        if isinstance(body, list):
            return body
        return DEFAULT_INDUSTRIES
    except Exception as e:
        logger.warning("[enterprise] 无法获取行业列表: %s, 使用本地数据兜底", e)
        return DEFAULT_INDUSTRIES


def save_session(base_upload: Path, contacts: List[Dict[str, Any]], user_id: str = "") -> str:
    sid = uuid.uuid4().hex[:16]
    path = session_dir(base_upload) / f"{sid}.json"
    payload = {"contacts": contacts, "user_id": user_id, "consumable_order_id": None}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return sid


def load_session(base_upload: Path, session_id: str) -> Optional[Dict[str, Any]]:
    path = session_dir(base_upload) / f"{session_id.strip()}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def delete_session(base_upload: Path, session_id: str) -> None:
    path = session_dir(base_upload) / f"{session_id.strip()}.json"
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


async def verify_consumable_order(
    *, user_id: str, order_id: int, expect_ref: str
) -> bool:
    """调用 xm-user 校验按量订单已支付且 ref_key 匹配"""
    origin = os.getenv("XM_USER_API_ORIGIN", "http://127.0.0.1:42001").rstrip("/")
    url = f"{origin}/api/subscription/consumable-order/_query"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                url,
                json={"user_id": user_id, "order_id": order_id},
                headers={"Content-Type": "application/json"},
            )
        data = r.json()
        if not data.get("success") or not data.get("data"):
            return False
        d = data["data"]
        if d.get("status") != "approved":
            return False
        ref = (d.get("ref_key") or "").strip()
        return ref == expect_ref.strip()
    except Exception as e:
        logger.warning("[enterprise] 校验 consumable 订单失败: %s", e)
        return False

async def report_purchased_contacts(
    *,
    auth_header: Optional[str],
    batch_id: int,
    contacts: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """报送用户购买的联系人清单（自动执行客户端 AES-GCM 加密传输）"""
    base = _detect_mashangchaqi_origin()
    token = ""
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]

    client = XMClient(base, token=token, encryption=True)
    
    safe_batch_id = batch_id & 0x7FFFFFFF
    payload = {
        "batch_id": safe_batch_id,
        "source_product": "xm-bot4",
        "contacts": contacts,
    }

    try:
        loop = asyncio.get_running_loop()
        body = await loop.run_in_executor(
            None,
            lambda: client.post("/api/v1/purchases", payload)
        )
        if isinstance(body, dict):
            return body
        return {}
    except Exception as e:
        logger.warning(f"Failed to report purchased contacts: {e}")
        return {}
