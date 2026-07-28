"""
统计数据 API — 全部走同步后端接口查询

改造说明:
    - 加好友流水 / AI回复 / 违规记录 / 朋友圈操作 → 同步后端 API 查询
    - 在线设备 → 本地实例管理器（物理状态，本来就不涉及数据库）
    - 本地 SQLite 的 ai_chat_history / moment_interactions / auto_friend_requests 表不再读取
    - chat_log 明细合并同步后端 events + 本地待上报事件队列（与 DailyCounter 对齐，避免「有次数无明细」）
"""
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
import json
import datetime
from typing import Optional, List, Dict, Any
from src.utils.response import ok, err
from src.crm.account_data import get_active_account
from urllib.parse import urlencode

router = APIRouter(prefix="/api/stats", tags=["Statistics Dashboard Details"])

CHAT_LOG_LIST_LIMIT = 500  # 明细合并后在内存分页；日回复量通常远小于此


def _normalize_event_ts(raw) -> str:
    s = str(raw or "").replace("T", " ")
    return s[:19]


def _normalize_event_data_dict(raw) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        o = json.loads(raw) if isinstance(raw, str) else {}
        return o if isinstance(o, dict) else {}
    except Exception:
        return {}


def _event_row_matches_account(row: Dict[str, Any], account_id: str) -> bool:
    if not account_id:
        return True
    ed = _normalize_event_data_dict(row.get("event_data"))
    return (ed.get("account_id") or "").strip() == account_id


def _cloud_chat_log_raw_list(result, account_id: Optional[str] = None) -> List[Dict]:
    if result and isinstance(result, dict) and "data" in result:
        result = result["data"]
    if result and isinstance(result, list):
        if account_id:
            return [r for r in result if _event_row_matches_account(r, account_id)]
        return result
    return []


def _pending_chat_log_raw_list(account_id: str) -> List[Dict]:
    try:
        from src.utils.cloud_sync import get_cloud_client
        return get_cloud_client().peek_pending_events("chat_log", account_id or "")
    except Exception:
        return []


def _merge_chat_log_sources(
    cloud_raw: List[Dict],
    pending_raw: List[Dict],
    search: Optional[str],
    page: int,
    limit: int,
    account_id: str = "",
):
    rows: List[Dict] = []
    for is_pending, raw_list in [(True, pending_raw), (False, cloud_raw)]:
        for r in raw_list:
            if account_id and not _event_row_matches_account(r, account_id):
                continue
            data = _normalize_event_data_dict(r.get("event_data"))
            ts = _normalize_event_ts(
                data.get("created_at") or r.get("created_at") or r.get("createdAt") or r.get("timestamp")
            )
            rows.append({
                "_id": r.get("id") if not is_pending else r.get("event_id"),
                "_wxid": data.get("wxid", ""),
                "_message": data.get("message", ""),
                "_reply": data.get("reply", ""),
                "_ts": ts,
            })

    seen, deduped = set(), []
    for row in rows:
        key = (row["_wxid"], row["_message"][:200], row["_reply"][:200], row["_ts"][:16])
        if key not in seen:
            seen.add(key)
            deduped.append(row)
    deduped.sort(key=lambda x: x["_ts"], reverse=True)

    if search:
        s = search.strip()
        deduped = [r for r in deduped if s in f"{r['_wxid']} {r['_message']} {r['_reply']}"]

    start = max(0, (page - 1) * limit)
    return deduped[start : start + limit], len(deduped)


def build_pagination_response(items: List[Dict], total: int, page: int, limit: int) -> Dict:
    return {"items": items, "total": total, "page": page, "page_size": limit}


def _cloud_query(path: str, params: dict = None) -> Optional[dict]:
    try:
        from src.utils.cloud_sync import get_cloud_client
        cloud = get_cloud_client()
        params = params or {}
        if "account_id" not in params:
            params["account_id"] = get_active_account()
        query_str = urlencode({k: v for k, v in params.items() if v is not None and v != ""})
        return cloud._get(f"{path}?{query_str}" if query_str else path, need_auth=True)
    except Exception as e:
        print(f"[StatsAPI] 同步后端查询异常: {e}")
        return None


@router.get("/friend-requests")
async def get_friend_requests(page: int = 1, limit: int = 20, search: Optional[str] = None):
    result = _cloud_query("/api/v1/events", {
        "event_type": "friend_request", "page": page, "limit": limit, "search": search,
    })
    if result and isinstance(result, dict) and "data" in result:
        result = result["data"]
    if result and isinstance(result, list):
        items = []
        for r in result:
            data = r.get("event_data", {})
            items.append({
                "id": r.get("id"),
                "target_name": data.get("target_name", ""),
                "target_wxid": data.get("target_wxid", ""),
                "source": data.get("source", "搜索添加"),
                "msg": data.get("msg", ""),
                "status": data.get("status", "processing"),
                "created_at": _normalize_event_ts(data.get("created_at") or r.get("created_at") or r.get("createdAt") or r.get("timestamp"))
            })
        return ok(build_pagination_response(items, len(items), page, limit))
    return ok(build_pagination_response([], 0, page, limit))


@router.get("/auto-replies")
async def get_auto_replies(page: int = 1, limit: int = 20, search: Optional[str] = None):
    account_id = get_active_account()
    result = _cloud_query("/api/v1/events", {
        "event_type": "chat_log", "page": 1, "limit": CHAT_LOG_LIST_LIMIT, "search": search,
    })
    cloud_raw = _cloud_chat_log_raw_list(result, account_id)
    pending_raw = _pending_chat_log_raw_list(account_id)
    merged, total = _merge_chat_log_sources(cloud_raw, pending_raw, search, page, limit, account_id)
    items = []
    for row in merged:
        items.append({
            "id": row.get("_id"),
            "customer": row["_wxid"],
            "trigger": row["_message"],
            "reply": row["_reply"],
            "tokens": len(row["_reply"]) + len(row["_message"]),
            "created_at": row["_ts"],
        })
    return ok(build_pagination_response(items, total, page, limit))


@router.get("/violations")
async def get_violations(page: int = 1, limit: int = 20, search: Optional[str] = None):
    result = _cloud_query("/api/v1/events", {
        "event_type": "violation",
        "page": page, "limit": limit, "search": search,
    })
    
    if result and isinstance(result, dict) and "data" in result:
        result = result["data"]
        
    if result and isinstance(result, list):
        items = []
        for r in result:
            data = r.get("event_data", {})
            user_name = data.get("user_name", "")
            content = data.get("content", "")
                
            items.append({
                "id": r.get("id"),
                "user_name": user_name,
                "reason": data.get("reason", "触碰红线"),
                "content": content,
                "action": data.get("action", "已拦截/撤回"),
                "created_at": str(data.get("created_at") or r.get("created_at") or r.get("createdAt") or r.get("timestamp") or "").replace("T", " ")[:19]
            })
            
        return ok(build_pagination_response(items, len(items), page, limit))
        
    return ok(build_pagination_response([], 0, page, limit))


@router.get("/online-devices")
async def get_online_devices():
    """在线设备 — 本地物理状态，不涉及数据库"""
    try:
        from src.utils.instance_manager import InstanceManagerV2
        mgr = InstanceManagerV2.get_instance()
        items = []
        for instance_id, inst in mgr.get_all_instances().items():
            state = inst.get("status", "offline")
            w_hwnd = inst.get("window_handle")
            if state == "online" and w_hwnd:
                import win32gui
                if not win32gui.IsWindow(w_hwnd):
                    state = "offline"
            
            items.append({
                "id": instance_id,
                "device_id": f"WX-INST-{instance_id[-4:].upper()}",
                "wxid": inst.get("wxid", "尚未登入"),
                "avatar": inst.get("avatar") or "",
                "nickname": inst.get("nickname") or "",
                "login_ip": "127.0.0.1 (Local)",
                "duration": "运行中" if state in ("online", "active", "running") else "已离线",
                "status": state
            })

            
        return ok({
            "items": items,
            "total": len(items),
            "page": 1,
            "page_size": max(1, len(items))
        })
    except Exception as e:
        return err(50000, str(e))


@router.get("/security-ops")
async def get_security_ops(
    type: str = 'like', 
    page: int = 1, 
    limit: int = 20,
    search: Optional[str] = None,
    status: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None
):
    # 转换前端的统计分类为后端的事件类型
    event_type_map = {
        'add_friend': 'friend_request',
        'auto_reply': 'chat_log'
    }
    actual_event_type = event_type_map.get(type, type)

    account_id = get_active_account()

    if type == "auto_reply":
        result = _cloud_query("/api/v1/events", {
            "event_type": "chat_log",
            "page": 1,
            "limit": CHAT_LOG_LIST_LIMIT,
            "search": search,
        })
        cloud_raw = _cloud_chat_log_raw_list(result, account_id)
        pending_raw = _pending_chat_log_raw_list(account_id)
        merged, total = _merge_chat_log_sources(cloud_raw, pending_raw, search, page, limit, account_id)
        items = []
        for row in merged:
            item = {
                "id": row.get("_id"),
                "executed_at": row["_ts"],
                "status": "success",
                "target": row["_wxid"],
                "input_text": row["_message"],
                "reply_text": row["_reply"],
            }
            if status and status != item.get("status"):
                continue
            items.append(item)
        return ok(build_pagination_response(items, total, page, limit))

    _today = datetime.datetime.now().strftime("%Y-%m-%d")
    result = _cloud_query("/api/v1/events", {
        "event_type": actual_event_type, "page": page, "limit": limit,
        # 默认只查今日（与 DailyCounter 对齐，外层计数和详情页数据一致）
        "start_time": start_time or f"{_today} 00:00:00",
        "end_time": end_time or f"{_today} 23:59:59",
    })

    if result and isinstance(result, dict) and "data" in result:
        result = result["data"]

    if result and isinstance(result, list):
        items = []
        for r in result:
            if not _event_row_matches_account(r, account_id):
                continue
            data = r.get("event_data", {})
            if isinstance(data, str):
                data = _normalize_event_data_dict(data)
            elif not isinstance(data, dict):
                data = {}
            created_at = str(data.get("created_at") or r.get("created_at") or r.get("createdAt") or r.get("timestamp") or "").replace("T", " ")[:19]

            # 基础格式化提取
            item = {
                "id": r.get("id"),
                "executed_at": created_at,
                "status": data.get("status", data.get("result", "success"))
            }

            if type == 'comment':
                item["target_name"] = data.get("target_name", data.get("wxid", ""))
                item["content_snippet"] = data.get("content_snippet", "")
                item["reply_text"] = data.get("reply_text", "")
            elif type == 'like':
                item["target_name"] = data.get("target_name", data.get("wxid", ""))
                item["content_snippet"] = data.get("content_snippet", "")
            elif type == 'moment_post':
                item["reply_text"] = data.get("content", data.get("reply_text", ""))
            elif type == 'group_message':
                item["target"] = data.get("wxid", "")
                item["input_text"] = data.get("message", "")
                item["reply_text"] = data.get("reply", "")
            else:
                item["task_id"] = f"TASK-{str(r.get('id', 'SYS'))}"
                item["target"] = data.get("phone", data.get("wxid", "Unknown"))
                # 将后端的 processing 转化为 UI 安全提示使用的状态
                st = data.get("status", "processing")
                item["status"] = "success" if st == "passed" else ("failed" if "fail" in st.lower() or "error" in st.lower() else st)

            # 执行 Search 过滤
            if search:
                search_hit = False
                for v in item.values():
                    if search in str(v):
                        search_hit = True
                        break
                if not search_hit:
                    continue

            if status and status != item.get("status"):
                continue

            items.append(item)

        return ok(build_pagination_response(items, len(items), page, limit))

    return ok(build_pagination_response([], 0, page, limit))
