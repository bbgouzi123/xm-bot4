"""UIBus 告警子路由"""
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

class AlertRuleIn(BaseModel):
    """告警规则 upsert 请求体（id 为空时新增）。"""
    id: Optional[str] = None
    name: str
    metric: str  # success_rate / avg_queued_ms / failed_total / success_rate_any_account
    op: str  # <, <=, >, >=, ==
    threshold: float
    window_minutes: int = 10
    cooldown_minutes: int = 15
    enabled: bool = True


@router.get("/alerts/rules")
async def list_alert_rules():
    """返回当前告警规则（持久化在 ~/.xm-ai-bot/ui_bus/alert_rules.json）。"""
    from src.orchestrator.alerting import get_alert_engine
    from src.utils.response import ok
    return ok({"items": get_alert_engine().list_rules()})


@router.put("/alerts/rules")
async def upsert_alert_rule(rule: AlertRuleIn):
    """新增或更新一条告警规则。"""
    from src.orchestrator.alerting import get_alert_engine
    from src.utils.response import ok
    allowed_ops = {"<", "<=", ">", ">=", "=="}
    if rule.op not in allowed_ops:
        raise HTTPException(
            status_code=400, detail=f"op 仅支持 {sorted(allowed_ops)}",
        )
    allowed_metrics = {
        "success_rate", "avg_queued_ms", "failed_total",
        "success_rate_any_account",
    }
    if rule.metric not in allowed_metrics:
        raise HTTPException(
            status_code=400,
            detail=f"metric 仅支持 {sorted(allowed_metrics)}",
        )
    if rule.window_minutes < 1 or rule.window_minutes > 1440:
        raise HTTPException(
            status_code=400, detail="window_minutes 必须在 [1, 1440]",
        )
    if rule.cooldown_minutes < 0 or rule.cooldown_minutes > 1440:
        raise HTTPException(
            status_code=400, detail="cooldown_minutes 必须在 [0, 1440]",
        )
    saved = get_alert_engine().upsert_rule(rule.model_dump())
    return ok(saved)


@router.delete("/alerts/rules/{rule_id}")
async def delete_alert_rule(rule_id: str):
    from src.orchestrator.alerting import get_alert_engine
    from src.utils.response import ok
    removed = get_alert_engine().delete_rule(rule_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"规则不存在: {rule_id}")
    return ok({"deleted": rule_id})


@router.get("/alerts/history")
async def list_alert_history(limit: int = 100):
    from src.orchestrator.alerting import get_alert_engine
    from src.utils.response import ok
    limit = max(1, min(limit, 500))
    return ok({"items": get_alert_engine().list_history(limit=limit)})


@router.post("/alerts/evaluate")
async def evaluate_alerts_now():
    """手动触发一次评估，返回刚刚触发的告警（用于调试）。"""
    from src.orchestrator.alerting import get_alert_engine
    from src.utils.response import ok
    fired = get_alert_engine().evaluate_once()
    return ok({"fired": fired, "count": len(fired)})


@router.post("/alerts/test-self")
async def test_self_notify():
    """手动造一条假告警，触发"发消息给微信自己"链路，用于首次接入调通。"""
    from src.orchestrator.alerting import get_alert_engine, AlertEvent
    from src.utils.response import ok
    import time as _t
    engine = get_alert_engine()
    fn = engine._self_notifier  # noqa: SLF001
    if fn is None:
        raise HTTPException(
            status_code=503,
            detail="self_notifier 未注入，请检查 main.py 启动日志",
        )
    evt = AlertEvent(
        rule_id="__test__",
        rule_name="测试告警：确认自通知链路",
        metric="success_rate",
        value=42.0,
        threshold=60.0,
        op="<",
        fired_at=_t.time(),
        message="这是一条自通知测试消息，看到这条就说明链路通了",
        context={
            "total": 100,
            "success_rate": 42.0,
            "avg_queued_ms": 150,
        },
    )
    try:
        fn(evt)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"自通知调用失败: {e}")
    return ok({
        "dispatched": True,
        "preview": engine.format_wechat_text(evt),
    })
