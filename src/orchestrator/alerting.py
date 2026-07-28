"""
UIBus 告警引擎
================

对 UIBus 产生的命令历史（最近 N 分钟窗口）做周期性评估，触发规则时：

1. 写入告警历史（内存 ring buffer + 本地 JSON 快照）
2. 通过 WebSocket 推 `ui_bus:alert` 事件到前端（实时铃铛/弹提）
3. 调用配置的外部 webhook（企业微信/钉钉/飞书 群机器人），best-effort

规则内置：
- success_rate 低于阈值
- 平均排队时长 超过阈值
- 失败+超时 单窗口绝对值
- 某账号独立的 success_rate 阈值（by_account 遍历）

所有规则都有 cooldown，避免同一问题在告警面板里刷屏。
规则持久化到 `~/.xm-ai-bot/ui_bus/alert_rules.json`，告警历史到 `alert_history.json`。

本模块 **只读** UIBus 的快照与 CommandHistorySink 的本地文件，
不会反过来阻塞 UIBus worker。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


# ---------------- 基础数据结构 ----------------

@dataclass
class AlertRule:
    """单条告警规则。

    metric 取值：
    - ``success_rate``         : 成功率（0~100）
    - ``avg_queued_ms``        : 平均排队时长（毫秒）
    - ``failed_total``         : 窗口内失败+超时绝对数
    - ``success_rate_any_account``: 只要有任意一个账号 success_rate 低于阈值
    """

    id: str
    name: str
    metric: str  # success_rate / avg_queued_ms / failed_total / success_rate_any_account
    op: str  # "<" / ">" / "<=" / ">="
    threshold: float
    window_minutes: int = 10
    cooldown_minutes: int = 15
    enabled: bool = True
    # 最近一次触发时间戳（秒），用于冷却
    last_fire_ts: float = 0.0


@dataclass
class AlertEvent:
    rule_id: str
    rule_name: str
    metric: str
    value: float
    threshold: float
    op: str
    fired_at: float
    message: str
    context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["fired_at_iso"] = datetime.fromtimestamp(
            self.fired_at, tz=timezone.utc
        ).isoformat()
        return d


# ---------------- 默认规则 ----------------

DEFAULT_RULES: List[AlertRule] = [
    AlertRule(
        id="default_success_rate_60",
        name="最近 10 分钟成功率低于 60%",
        metric="success_rate",
        op="<",
        threshold=60.0,
        window_minutes=10,
        cooldown_minutes=15,
    ),
    AlertRule(
        id="default_queue_30s",
        name="最近 10 分钟平均排队超过 30 秒",
        metric="avg_queued_ms",
        op=">",
        threshold=30_000.0,
        window_minutes=10,
        cooldown_minutes=15,
    ),
    AlertRule(
        id="default_failed_20",
        name="最近 10 分钟失败+超时累计超过 20 次",
        metric="failed_total",
        op=">=",
        threshold=20.0,
        window_minutes=10,
        cooldown_minutes=20,
    ),
    AlertRule(
        id="default_account_40",
        name="任一账号成功率跌破 40%",
        metric="success_rate_any_account",
        op="<",
        threshold=40.0,
        window_minutes=10,
        cooldown_minutes=30,
    ),
]


# ---------------- AlertEngine 单例 ----------------

_STORAGE_DIR = Path.home() / ".xm-ai-bot" / "ui_bus"
_RULES_FILE = _STORAGE_DIR / "alert_rules.json"
_HISTORY_FILE = _STORAGE_DIR / "alert_history.json"
_HISTORY_MAX = 500
_DEFAULT_INTERVAL = 60  # 秒，评估周期


def _compare(value: float, op: str, threshold: float) -> bool:
    if op == "<":
        return value < threshold
    if op == "<=":
        return value <= threshold
    if op == ">":
        return value > threshold
    if op == ">=":
        return value >= threshold
    if op == "==":
        return value == threshold
    return False


class AlertEngine:
    """周期性评估 UIBus 状态，触发规则 → 广播+webhook+持久化。"""

    def __init__(self) -> None:
        self._rules: List[AlertRule] = []
        self._history: List[AlertEvent] = []
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._interval = _DEFAULT_INTERVAL
        self._ws_broadcast: Optional[Callable[[Dict[str, Any]], None]] = None
        # 规则评估所需的数据源由外部注入，避免循环依赖
        self._stats_provider: Optional[Callable[[int], Dict[str, Any]]] = None
        # 自我通知：把告警当成一条 SEND_MESSAGE 命令丢给微信"文件传输助手"
        self._self_notifier: Optional[Callable[[AlertEvent], None]] = None
        # 防止"发告警 → 失败 → 又产生告警"的递归爆炸
        self._in_notify = threading.Event()
        _STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        self._load_rules()
        self._load_history()

    # ---------- 配置 ----------

    def set_ws_broadcaster(
        self, fn: Callable[[Dict[str, Any]], None]
    ) -> None:
        self._ws_broadcast = fn

    def set_stats_provider(
        self, fn: Callable[[int], Dict[str, Any]]
    ) -> None:
        """注入 stats_provider(window_minutes) -> stats dict。

        期望 stats dict 形如（与 ui_bus_api._aggregate_stats 保持兼容）：
        ``{"total": int, "success_rate": float, "avg_queued_ms": float,
           "by_status": {...}, "by_account": [...]}``
        """
        self._stats_provider = fn

    def set_self_notifier(
        self, fn: Callable[[AlertEvent], None]
    ) -> None:
        """注入"微信自通知"回调：收到 AlertEvent → 发一条消息给运营自己。

        具体落地由 main.py 提供（通常是往 UIBus 丢 SEND_MESSAGE 命令，
        target=文件传输助手，priority=URGENT）。
        """
        self._self_notifier = fn

    # ---------- 生命周期 ----------

    def start(self, interval_seconds: int = _DEFAULT_INTERVAL) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._interval = max(10, interval_seconds)
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="ui-bus-alert-engine",
        )
        self._thread.start()
        logger.info(f"[Alert] engine started, interval={self._interval}s")

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    # ---------- 规则管理 ----------

    def list_rules(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [asdict(r) for r in self._rules]

    def upsert_rule(self, rule: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            rid = rule.get("id") or f"rule_{int(time.time() * 1000)}"
            new_r = AlertRule(
                id=rid,
                name=str(rule.get("name") or rid),
                metric=str(rule.get("metric") or "success_rate"),
                op=str(rule.get("op") or "<"),
                threshold=float(rule.get("threshold") or 0),
                window_minutes=int(rule.get("window_minutes") or 10),
                cooldown_minutes=int(rule.get("cooldown_minutes") or 15),
                enabled=bool(rule.get("enabled", True)),
            )
            idx = next(
                (i for i, r in enumerate(self._rules) if r.id == rid), None,
            )
            if idx is None:
                self._rules.append(new_r)
            else:
                # 保留 last_fire_ts 避免重复弹提
                new_r.last_fire_ts = self._rules[idx].last_fire_ts
                self._rules[idx] = new_r
            self._save_rules()
            return asdict(new_r)

    def delete_rule(self, rule_id: str) -> bool:
        with self._lock:
            before = len(self._rules)
            self._rules = [r for r in self._rules if r.id != rule_id]
            changed = len(self._rules) != before
            if changed:
                self._save_rules()
            return changed

    def list_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            return [e.to_dict() for e in self._history[-limit:][::-1]]

    # ---------- 核心评估 ----------

    def _run(self) -> None:
        # 先立刻评估一次，给前端首屏一点反馈
        try:
            self.evaluate_once()
        except Exception as e:
            logger.debug(f"[Alert] first evaluate failed: {e}")
        while not self._stop_evt.wait(self._interval):
            try:
                self.evaluate_once()
            except Exception as e:
                logger.warning(f"[Alert] evaluate failed: {e}")

    def evaluate_once(self) -> List[Dict[str, Any]]:
        if self._stats_provider is None:
            return []
        now = time.time()
        with self._lock:
            rules = [r for r in self._rules if r.enabled]
        fired: List[AlertEvent] = []
        # 按窗口缓存，避免同一窗口多次拉数据
        stats_cache: Dict[int, Dict[str, Any]] = {}
        for r in rules:
            # 冷却检查
            if r.last_fire_ts and (now - r.last_fire_ts) < r.cooldown_minutes * 60:
                continue
            stats = stats_cache.get(r.window_minutes)
            if stats is None:
                try:
                    stats = self._stats_provider(r.window_minutes) or {}
                except Exception as e:
                    logger.debug(
                        f"[Alert] stats_provider({r.window_minutes}) error: {e}"
                    )
                    stats = {}
                stats_cache[r.window_minutes] = stats
            if not stats or stats.get("total", 0) == 0:
                continue
            evt = self._check_rule(r, stats, now)
            if evt is not None:
                fired.append(evt)
                r.last_fire_ts = now
        if not fired:
            return []
        # 落盘 + 广播 + webhook
        with self._lock:
            for e in fired:
                self._history.append(e)
            if len(self._history) > _HISTORY_MAX:
                self._history = self._history[-_HISTORY_MAX:]
            self._save_rules()  # 保留 last_fire_ts
            self._save_history()
        for e in fired:
            self._broadcast(e)
            self._fire_webhook(e)
            self._notify_self(e)
        return [e.to_dict() for e in fired]

    def _check_rule(
        self, rule: AlertRule, stats: Dict[str, Any], now: float,
    ) -> Optional[AlertEvent]:
        m = rule.metric
        by_status = stats.get("by_status") or {}
        ctx = {
            "total": stats.get("total", 0),
            "success_rate": stats.get("success_rate"),
            "avg_queued_ms": stats.get("avg_queued_ms"),
            "failed": by_status.get("failed", 0),
            "timeout": by_status.get("timeout", 0),
        }
        if m == "success_rate":
            v = float(stats.get("success_rate") or 0)
            if _compare(v, rule.op, rule.threshold):
                return self._build_event(
                    rule, v, now,
                    f"成功率 {v:.1f}% {rule.op} {rule.threshold:.1f}%",
                    ctx,
                )
        elif m == "avg_queued_ms":
            v = float(stats.get("avg_queued_ms") or 0)
            if _compare(v, rule.op, rule.threshold):
                return self._build_event(
                    rule, v, now,
                    f"平均排队 {v:.0f}ms {rule.op} {rule.threshold:.0f}ms",
                    ctx,
                )
        elif m == "failed_total":
            v = float(
                (by_status.get("failed") or 0)
                + (by_status.get("timeout") or 0)
            )
            if _compare(v, rule.op, rule.threshold):
                return self._build_event(
                    rule, v, now,
                    f"失败+超时 {int(v)} 次 {rule.op} {int(rule.threshold)} 次",
                    ctx,
                )
        elif m == "success_rate_any_account":
            accounts = stats.get("by_account") or []
            worst_wxid = None
            worst_v: Optional[float] = None
            for a in accounts:
                if (a.get("total") or 0) < 3:
                    continue  # 样本太少不触发
                v = float(a.get("success_rate") or 0)
                if _compare(v, rule.op, rule.threshold):
                    if worst_v is None or v < worst_v:
                        worst_v = v
                        worst_wxid = a.get("wxid")
            if worst_v is not None:
                ctx["worst_wxid"] = worst_wxid
                return self._build_event(
                    rule, worst_v, now,
                    f"账号 {worst_wxid} 成功率 {worst_v:.1f}% "
                    f"{rule.op} {rule.threshold:.1f}%",
                    ctx,
                )
        return None

    def _build_event(
        self, rule: AlertRule, value: float, now: float,
        msg: str, ctx: Dict[str, Any],
    ) -> AlertEvent:
        return AlertEvent(
            rule_id=rule.id,
            rule_name=rule.name,
            metric=rule.metric,
            value=round(value, 2),
            threshold=rule.threshold,
            op=rule.op,
            fired_at=now,
            message=msg,
            context=ctx,
        )

    # ---------- 广播 & webhook ----------

    def _broadcast(self, evt: AlertEvent) -> None:
        if self._ws_broadcast is None:
            return
        try:
            self._ws_broadcast({
                "type": "ui_bus:alert",
                "data": evt.to_dict(),
            })
        except Exception as e:
            logger.debug(f"[Alert] broadcast error: {e}")

    def _notify_self(self, evt: AlertEvent) -> None:
        """给微信里的"文件传输助手/自己"发一条告警消息。

        - best-effort：异常只记 debug，绝不影响其他分发
        - 通过 ``_in_notify`` 标志防止递归（发消息失败又触发告警）
        """
        fn = self._self_notifier
        if fn is None:
            return
        if self._in_notify.is_set():
            logger.debug("[Alert] 自通知递归保护已触发，跳过")
            return
        try:
            self._in_notify.set()
            fn(evt)
        except Exception as e:
            logger.debug(f"[Alert] self notifier error: {e}")
        finally:
            self._in_notify.clear()

    def _fire_webhook(self, evt: AlertEvent) -> None:
        """支持 wechat_work / dingtalk / feishu / generic。

        配置从环境变量 XM_BOT4_ALERT_WEBHOOKS 读取，格式 JSON 数组：
        [{"type":"wechat_work","url":"https://..."},
         {"type":"dingtalk","url":"https://...","secret":"..."}]
        """
        raw = os.getenv("XM_BOT4_ALERT_WEBHOOKS", "").strip()
        if not raw:
            return
        try:
            targets = json.loads(raw)
            if not isinstance(targets, list):
                return
        except Exception as e:
            logger.debug(f"[Alert] invalid XM_BOT4_ALERT_WEBHOOKS: {e}")
            return
        text = self._format_text(evt)
        for tgt in targets:
            try:
                kind = tgt.get("type") or "generic"
                url = tgt.get("url")
                if not url:
                    continue
                if kind == "wechat_work":
                    payload = {
                        "msgtype": "markdown",
                        "markdown": {"content": text},
                    }
                elif kind == "dingtalk":
                    payload = {
                        "msgtype": "markdown",
                        "markdown": {
                            "title": f"[xm-bot4] {evt.rule_name}",
                            "text": text,
                        },
                    }
                elif kind == "feishu":
                    payload = {
                        "msg_type": "text",
                        "content": {"text": text},
                    }
                else:
                    payload = evt.to_dict()
                requests.post(url, json=payload, timeout=5)
            except Exception as e:
                logger.debug(f"[Alert] webhook {tgt.get('type')} error: {e}")

    def format_wechat_text(self, evt: AlertEvent) -> str:
        """给"发到微信自己"场景用的精简文本（纯文本，无 markdown）。"""
        t = datetime.fromtimestamp(evt.fired_at).strftime("%H:%M:%S")
        ctx = evt.context or {}
        lines = [
            f"🔔 xm-bot4 告警 · {t}",
            f"规则：{evt.rule_name}",
            f"指标：{evt.metric}={evt.value} (阈值 {evt.op} {evt.threshold})",
            f"详情：{evt.message}",
        ]
        sr = ctx.get("success_rate")
        if sr is not None:
            lines.append(
                f"窗口：总 {ctx.get('total')} 条，成功率 {sr}%，"
                f"平均排队 {ctx.get('avg_queued_ms')}ms"
            )
        if ctx.get("worst_wxid"):
            lines.append(f"最差账号：{ctx.get('worst_wxid')}")
        return "\n".join(lines)

    def _format_text(self, evt: AlertEvent) -> str:
        t = datetime.fromtimestamp(evt.fired_at).strftime("%Y-%m-%d %H:%M:%S")
        return (
            f"## 🔔 xm-bot4 告警：{evt.rule_name}\n"
            f"- 触发时间：{t}\n"
            f"- 指标：`{evt.metric}` = **{evt.value}** "
            f"(规则 {evt.op} {evt.threshold})\n"
            f"- 说明：{evt.message}\n"
            f"- 最近窗口：total={evt.context.get('total')}, "
            f"success_rate={evt.context.get('success_rate')}%, "
            f"avg_queued_ms={evt.context.get('avg_queued_ms')}"
        )

    # ---------- 持久化 ----------

    def _load_rules(self) -> None:
        if not _RULES_FILE.exists():
            self._rules = list(DEFAULT_RULES)
            self._save_rules()
            return
        try:
            raw = json.loads(_RULES_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                self._rules = [AlertRule(**r) for r in raw]
            else:
                self._rules = list(DEFAULT_RULES)
        except Exception as e:
            logger.warning(f"[Alert] load rules failed, using defaults: {e}")
            self._rules = list(DEFAULT_RULES)

    def _save_rules(self) -> None:
        try:
            _RULES_FILE.write_text(
                json.dumps(
                    [asdict(r) for r in self._rules],
                    ensure_ascii=False, indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as e:
            logger.debug(f"[Alert] save rules failed: {e}")

    def _load_history(self) -> None:
        if not _HISTORY_FILE.exists():
            self._history = []
            return
        try:
            raw = json.loads(_HISTORY_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                out: List[AlertEvent] = []
                for e in raw:
                    try:
                        e.pop("fired_at_iso", None)
                        out.append(AlertEvent(**e))
                    except Exception:
                        continue
                self._history = out[-_HISTORY_MAX:]
        except Exception as e:
            logger.debug(f"[Alert] load history failed: {e}")
            self._history = []

    def _save_history(self) -> None:
        try:
            _HISTORY_FILE.write_text(
                json.dumps(
                    [e.to_dict() for e in self._history],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except Exception as e:
            logger.debug(f"[Alert] save history failed: {e}")


# 单例
_engine: Optional[AlertEngine] = None


def get_alert_engine() -> AlertEngine:
    global _engine
    if _engine is None:
        _engine = AlertEngine()
    return _engine
