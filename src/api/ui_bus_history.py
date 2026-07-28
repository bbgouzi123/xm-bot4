"""UIBus 历史与重放子路由"""
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .ui_bus_stats import router as stats_router
from .ui_bus_stats import _aggregate_stats

router = APIRouter()
router.include_router(stats_router)

# 只有"纯数据 payload"的 kind 可重放——依赖 callable fn 的不能跨进程重建
_REPLAYABLE_KINDS = {
    "send_message",
    "publish_moment",
    "add_friend",
    "sync_tags",
    "fetch_avatar",
}

# 哪些字段属于历史记录的"真实业务数据"，跨进程可序列化
_PAYLOAD_SAFE_KEYS = {
    "target", "name", "text", "content", "remark", "tag",
    "wxid_target", "group_name", "message", "media_urls",
    "friend_wxid", "task_id", "source", "url", "count",
}


class ReplayOptions(BaseModel):
    priority: Optional[int] = None  # 不传则沿用原优先级
    timeout: Optional[float] = None


def _find_history_record(command_id: str) -> Optional[dict]:
    """在本地历史 JSONL 里找一条命令记录。"""
    try:
        from src.orchestrator.history_sink import (
            get_command_history_sink,
        )
        sink = get_command_history_sink()
    except Exception:
        return None
    # 从最近开始扫，命中即返回
    items = sink.list_history(since=None, limit=5000, offset=0)
    for rec in items:
        if rec.get("id") == command_id:
            return rec
    return None


@router.get("/commands/replayable")
async def list_replayable_kinds():
    """前端用这个判定"重放"按钮是否可点（kind 白名单）。"""
    from src.utils.response import ok
    return ok({"kinds": sorted(_REPLAYABLE_KINDS)})


@router.post("/commands/{command_id}/replay")
async def replay_command(command_id: str, opts: ReplayOptions = ReplayOptions()):
    """按历史记录重新提交一条命令。"""
    from src.utils.response import ok
    rec = _find_history_record(command_id)
    if rec is None:
        raise HTTPException(
            status_code=404, detail=f"历史记录不存在: {command_id}",
        )
    kind = rec.get("kind")
    if kind not in _REPLAYABLE_KINDS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"kind={kind!r} 不支持重放"
                f"（可重放类型: {sorted(_REPLAYABLE_KINDS)}）"
            ),
        )
    raw_payload = rec.get("payload") or {}
    safe_payload = {
        k: v for k, v in raw_payload.items() if k in _PAYLOAD_SAFE_KEYS
    }
    if not safe_payload:
        raise HTTPException(
            status_code=400,
            detail="该命令的历史 payload 为空，无法重放（可能是闭包任务）",
        )

    try:
        from src.orchestrator.ui_bus import (
            ui_bus,
            UICommand,
            UICommandKind,
            UICommandPriority,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"UIBus 未就绪: {e}")

    try:
        kind_enum = UICommandKind(kind)
    except Exception:
        raise HTTPException(status_code=400, detail=f"未知 kind: {kind}")

    orig_prio_raw = rec.get("priority")
    try:
        prio_int = (
            int(opts.priority) if opts.priority is not None
            else int(orig_prio_raw) if orig_prio_raw is not None
            else int(UICommandPriority.NORMAL)
        )
        prio = UICommandPriority(prio_int)
    except Exception:
        prio = UICommandPriority.NORMAL

    cmd = UICommand(
        wxid=str(rec.get("wxid") or ""),
        kind=kind_enum,
        payload=safe_payload,
        priority=prio,
        timeout=float(opts.timeout) if opts.timeout else 60.0,
    )
    new_id = ui_bus.submit(cmd)
    return ok({
        "new_command_id": new_id,
        "origin_command_id": command_id,
        "kind": kind,
        "wxid": cmd.wxid,
        "priority": int(prio),
        "payload": safe_payload,
    })


@router.get("/history")
async def list_history(
    wxid: Optional[str] = None,
    kind: Optional[str] = None,
    status: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
    source: str = "local",
):
    """UIBus 命令历史回放（双源：本地 JSONL / 同步后端 events 表）。"""
    from src.utils.response import ok
    limit = max(1, min(limit, 1000))
    offset = max(0, offset)

    if source not in ("local", "cloud"):
        raise HTTPException(
            status_code=400,
            detail=f"source 只支持 local/cloud，收到 {source!r}",
        )

    q_wxid: Optional[str]
    if wxid is None:
        q_wxid = None
    elif wxid in ("primary", "__primary__"):
        q_wxid = ""
    else:
        q_wxid = wxid

    if source == "cloud":
        items = _list_cloud_history(
            wxid=q_wxid, kind=kind, status=status,
            since=since, limit=limit, offset=offset,
        )
        return ok({
            "items": items,
            "count": len(items),
            "limit": limit,
            "offset": offset,
            "source": "cloud",
        })

    try:
        from src.orchestrator.history_sink import get_command_history_sink
        sink = get_command_history_sink()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"history sink 不可用: {e}")

    items = sink.list_history(
        wxid=q_wxid,
        kind=kind,
        status=status,
        since=since,
        limit=limit,
        offset=offset,
    )
    return ok({
        "items": items,
        "count": len(items),
        "limit": limit,
        "offset": offset,
        "source": "local",
        "metrics": sink.get_metrics(),
    })


def _list_cloud_history(
    *, wxid: Optional[str], kind: Optional[str], status: Optional[str],
    since: Optional[str], limit: int, offset: int,
) -> list:
    """从 Rust Cloud 拉 ``ui_bus_command`` 事件，并进行本地二次过滤。"""
    import logging
    _log = logging.getLogger(__name__)

    fetch_limit = min(limit * 3 + offset, 1000)
    search = None
    if kind:
        search = f'"kind": "{kind}"'
    elif status:
        if status != "failed_or_timeout":
            search = f'"status": "{status}"'

    try:
        from src.api.stats_api import _cloud_query
        result = _cloud_query("/api/v1/events", {
            "event_type": "ui_bus_command",
            "since": since,
            "search": search,
            "limit": fetch_limit,
        })
    except Exception as e:
        _log.warning(f"[UIBusHistory] 同步后端查询异常: {e}")
        return []

    rows = result
    if isinstance(result, dict) and "data" in result:
        rows = result["data"]
    if not isinstance(rows, list):
        return []

    out = []
    for r in rows:
        data = r.get("event_data") or {}
        if not isinstance(data, dict):
            continue
        if wxid is not None and (data.get("wxid") or "") != wxid:
            continue
        if kind and data.get("kind") != kind:
            continue
        if status:
            if status == "failed_or_timeout":
                if data.get("status") not in ("failed", "timeout"):
                    continue
            elif data.get("status") != status:
                continue
        out.append({
            "id": data.get("command_id") or str(r.get("id") or ""),
            "wxid": data.get("wxid") or "",
            "kind": data.get("kind"),
            "status": data.get("status"),
            "priority": data.get("priority", 0),
            "error": data.get("error"),
            "submit_ts": data.get("submit_ts"),
            "started_ts": data.get("started_ts"),
            "finished_ts": data.get("finished_ts"),
            "elapsed_seconds": data.get("elapsed_seconds", 0),
            "queued_seconds": data.get("queued_seconds", 0),
            "payload": data.get("payload_summary") or {},
            "result": data.get("result_summary"),
            "_cloud_id": r.get("id"),
            "_cloud_created_at": r.get("created_at"),
        })
    return out[offset:offset + limit]
