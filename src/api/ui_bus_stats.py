"""UIBus 统计仪表盘数据聚合子路由"""
from typing import Optional
from fastapi import APIRouter, HTTPException

router = APIRouter()

@router.get("/stats/overview")
async def stats_overview(
    since: Optional[str] = None,
    source: str = "local",
    limit_rows: int = 1000,
):
    """UIBus 历史统计摘要（按账号/按 kind 聚合）。"""
    from src.utils.response import ok
    from .ui_bus_history import _list_cloud_history
    if source not in ("local", "cloud"):
        raise HTTPException(
            status_code=400,
            detail=f"source 只支持 local/cloud，收到 {source!r}",
        )
    limit_rows = max(1, min(limit_rows, 5000))

    if source == "local":
        try:
            from src.orchestrator.history_sink import (
                get_command_history_sink,
            )
            sink = get_command_history_sink()
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"history sink 不可用: {e}",
            )
        items = sink.list_history(since=since, limit=limit_rows, offset=0)
    else:
        items = _list_cloud_history(
            wxid=None, kind=None, status=None, since=since,
            limit=limit_rows, offset=0,
        )

    return ok(_aggregate_stats(items, source=source))


def _aggregate_stats(items: list, *, source: str) -> dict:
    """把扁平 command 列表聚合成驾驶舱用的统计摘要。"""
    from collections import Counter, defaultdict

    total = len(items)
    by_status = Counter()
    elapsed_sum = 0.0
    elapsed_n = 0
    queued_sum = 0.0
    queued_n = 0

    acc_stats = defaultdict(lambda: {
        "wxid": "",
        "total": 0,
        "success": 0,
        "failed": 0,
        "timeout": 0,
        "canceled": 0,
        "elapsed_sum": 0.0,
        "elapsed_n": 0,
    })

    kind_stats = defaultdict(lambda: {
        "kind": "",
        "total": 0,
        "success": 0,
        "failed": 0,
        "timeout": 0,
        "canceled": 0,
        "elapsed_sum": 0.0,
        "elapsed_n": 0,
    })

    minute_counter = Counter()

    for r in items:
        status = r.get("status") or "unknown"
        by_status[status] += 1

        elapsed = r.get("elapsed_seconds") or 0
        if elapsed > 0:
            elapsed_sum += elapsed
            elapsed_n += 1
        queued = r.get("queued_seconds") or 0
        if queued > 0:
            queued_sum += queued
            queued_n += 1

        wxid = r.get("wxid") or ""
        a = acc_stats[wxid]
        a["wxid"] = wxid
        a["total"] += 1
        if status in a:
            a[status] += 1
        if elapsed > 0:
            a["elapsed_sum"] += elapsed
            a["elapsed_n"] += 1

        kind = r.get("kind") or "unknown"
        k = kind_stats[kind]
        k["kind"] = kind
        k["total"] += 1
        if status in k:
            k[status] += 1
        if elapsed > 0:
            k["elapsed_sum"] += elapsed
            k["elapsed_n"] += 1

        ts = r.get("finished_ts") or r.get("started_ts") or r.get("submit_ts")
        if ts:
            minute_counter[int(float(ts) // 60)] += 1

    def _finalize(row):
        t = row["total"] or 1
        succ = row.get("success", 0)
        elapsed_n_ = row.pop("elapsed_n", 0) or 1
        elapsed_sum_ = row.pop("elapsed_sum", 0.0)
        row["success_rate"] = round(succ / t, 4) if t else 0
        row["avg_elapsed_ms"] = round(elapsed_sum_ * 1000 / elapsed_n_, 1)
        return row

    by_account = sorted(
        (_finalize(dict(v)) for v in acc_stats.values()),
        key=lambda x: -x["total"],
    )
    by_kind = sorted(
        (_finalize(dict(v)) for v in kind_stats.values()),
        key=lambda x: -x["total"],
    )

    success = by_status.get("success", 0)
    success_rate = round(success / total, 4) if total else 0
    avg_elapsed_ms = round(elapsed_sum * 1000 / elapsed_n, 1) if elapsed_n else 0
    avg_queued_ms = round(queued_sum * 1000 / queued_n, 1) if queued_n else 0
    peak_per_minute = max(minute_counter.values()) if minute_counter else 0

    return {
        "source": source,
        "total": total,
        "by_status": dict(by_status),
        "success_rate": success_rate,
        "avg_elapsed_ms": avg_elapsed_ms,
        "avg_queued_ms": avg_queued_ms,
        "peak_per_minute": peak_per_minute,
        "by_account": by_account,
        "by_kind": by_kind,
    }
