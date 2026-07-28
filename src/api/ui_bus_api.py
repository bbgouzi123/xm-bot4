"""UIBus 对外 REST 接口

对应前端"驾驶舱"面板所需的数据源。事件流走现有 `ws_manager.broadcast()`，
UIBus 启动时由 main.py 注入广播回调（见 main.py 的 lifespan）。
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.orchestrator.ui_bus import ui_bus
from src.orchestrator.account_profile import (
    configure_tempo,
    get_tempo_snapshot,
)
from src.utils.response import ok

from .ui_bus_alerts import router as alerts_router
from .ui_bus_history import router as history_router
# 提供向后兼容的接口，供 lifespan_helper 和 alerting 导入
from .ui_bus_history import _aggregate_stats

router = APIRouter(prefix="/api/ui-bus", tags=["ui-bus"])
router.include_router(alerts_router)
router.include_router(history_router)


class TempoPatch(BaseModel):
    """账号节奏旋钮的 PATCH 结构。字段均为可选，未传不修改。"""
    min_interval: Optional[float] = None
    max_interval: Optional[float] = None
    rest_backoff: Optional[float] = None
    quota_backoff: Optional[float] = None


def _collect_quota_snapshot(wxids: list) -> dict:
    """为所有已观察到的账号拉 DailyCounter 的各维度配额热图。"""
    try:
        from src.utils.daily_counter import DailyCounter
        counter = DailyCounter()
    except Exception:
        return {}
    result = {}
    for wxid in wxids:
        if not wxid:
            wxid = "main"
        try:
            result[wxid] = counter.get_all_stats(wxid)
        except Exception:
            result[wxid] = {}
    return result


@router.get("/status")
async def get_status():
    """总览：各账号队列深度、当前执行中命令、指标、最近 50 条历史。"""
    snap = ui_bus.snapshot()
    wxids = list({a.get("wxid") or "" for a in snap.get("accounts", [])})
    tempo_snap = get_tempo_snapshot()
    for k in tempo_snap.keys():
        if k == "__primary__":
            k = ""
        if k not in wxids:
            wxids.append(k)
    snap["tempo"] = tempo_snap
    snap["quota"] = _collect_quota_snapshot(wxids or [""])
    return ok(snap)


@router.get("/commands/{cmd_id}")
async def get_command(cmd_id: str):
    """按 id 查单条命令的完整状态。"""
    cmd = ui_bus.get(cmd_id)
    if not cmd:
        raise HTTPException(status_code=404, detail="command not found")
    return ok(cmd.to_dict())


@router.post("/commands/{cmd_id}/cancel")
async def cancel_command(cmd_id: str):
    """取消一条还没开始执行的命令（已在 RUNNING 的不可取消）。"""
    success = ui_bus.cancel(cmd_id)
    if not success:
        raise HTTPException(
            status_code=409,
            detail="命令不存在或已经开始执行，无法取消",
        )
    return ok({"command_id": cmd_id, "canceled": True})


@router.patch("/accounts/{wxid}/tempo")
async def patch_account_tempo(wxid: str, patch: TempoPatch):
    """动态调整账号的拟人节奏参数（驾驶舱"旋钮"写入点）。"""
    real_wxid = "" if wxid in ("primary", "default", "__primary__") else wxid
    kwargs = {k: v for k, v in patch.model_dump().items() if v is not None}
    if not kwargs:
        raise HTTPException(status_code=400, detail="没有要更新的字段")
    for k, v in kwargs.items():
        if not isinstance(v, (int, float)) or v < 0 or v > 7200:
            raise HTTPException(
                status_code=400,
                detail=f"{k}={v} 超出合法区间 [0, 7200]",
            )
    mn = kwargs.get("min_interval")
    mx = kwargs.get("max_interval")
    if mn is not None and mx is not None and mn > mx:
        raise HTTPException(
            status_code=400, detail="min_interval 不能大于 max_interval",
        )
    configure_tempo(real_wxid, **kwargs)
    return ok({
        "wxid": real_wxid or "__primary__",
        "tempo": get_tempo_snapshot().get(
            real_wxid or "__primary__", {},
        ),
    })
