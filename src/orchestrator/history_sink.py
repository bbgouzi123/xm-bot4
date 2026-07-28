"""UIBus 命令历史落盘 sink
================================

目标：把 UIBus 每条命令的终态（SUCCESS/FAILED/TIMEOUT/CANCELED）同时落到
    1. **同步后端数据库**（Rust Cloud 的 PG `event_logs` 表）
       走统一事件通道 `CloudSyncClient.report_event("ui_bus_command", ...)`，
       自带失败重试 + 本地事件队列兜底。
    2. **本地 JSONL** （`~/.xm-ai-bot/ui_bus/history-YYYYMMDD.jsonl`）
       按天切分，便于驾驶舱历史页/审计排查，默认保留 14 天。

线程模型
--------
UIBus worker 调 `sink(cmd)`，我们把 cmd 的可序列化快照塞进内部队列，
**立刻返回**；真正的文件 IO 和 HTTP 由后台线程 `ui-bus-history-sink` 消费。
这样 UIBus 主循环绝对不会被 IO 拖慢。
"""
from __future__ import annotations

import datetime
import json
import logging
import queue
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.orchestrator.ui_bus import UICommand

logger = logging.getLogger(__name__)


_HISTORY_DIR = Path.home() / ".xm-ai-bot" / "ui_bus"
_RETENTION_DAYS = 14
_QUEUE_MAX = 2000  # 超过就丢最旧（避免无上限内存堆积）


def _safe_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """过滤掉不可 JSON 化的字段（例如闭包 fn）。"""
    if not isinstance(payload, dict):
        return {}
    out: Dict[str, Any] = {}
    for k, v in payload.items():
        try:
            json.dumps(v, ensure_ascii=False)
            out[k] = v
        except (TypeError, ValueError):
            # 闭包/对象：记录类型名即可
            out[k] = f"<non-serializable:{type(v).__name__}>"
    return out


def _command_to_record(cmd: UICommand) -> Dict[str, Any]:
    """把 UICommand 转成可入库的记录（去掉运行时字段）。"""
    d = cmd.to_dict()
    d["payload"] = _safe_payload(d.get("payload", {}))
    # 简单摘要：方便同步后端直接检索
    elapsed = 0.0
    if cmd.started_ts and cmd.finished_ts:
        elapsed = round(cmd.finished_ts - cmd.started_ts, 3)
    d["elapsed_seconds"] = elapsed
    queued_for = 0.0
    if cmd.submit_ts and cmd.started_ts:
        queued_for = round(cmd.started_ts - cmd.submit_ts, 3)
    d["queued_seconds"] = queued_for
    # result 可能是 bool/dict/None，尝试序列化，不行就降级为字符串
    try:
        json.dumps(d.get("result"), ensure_ascii=False)
    except (TypeError, ValueError):
        d["result"] = repr(d.get("result"))[:500]
    return d


class CommandHistorySink:
    """双路（同步后端 + 本地）命令终态落盘器。"""

    def __init__(
        self,
        history_dir: Path = _HISTORY_DIR,
        retention_days: int = _RETENTION_DAYS,
        enable_cloud: bool = True,
    ):
        self._dir = history_dir
        self._retention_days = retention_days
        self._enable_cloud = enable_cloud
        self._dir.mkdir(parents=True, exist_ok=True)

        self._queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(_QUEUE_MAX)
        self._stop = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._last_rotate_day = ""

        # 轻量统计：供 /api/ui-bus/history/stats 使用
        self._metrics = {
            "total_sunk": 0,
            "cloud_ok": 0,
            "cloud_fail": 0,
            "local_ok": 0,
            "local_fail": 0,
            "dropped_overflow": 0,
        }
        self._metrics_lock = threading.Lock()

    # ---------- public ----------

    def __call__(self, cmd: UICommand) -> None:
        """UIBus 会以此方法作为 sink 回调；**保证不阻塞 worker**。"""
        try:
            rec = _command_to_record(cmd)
        except Exception as e:
            logger.debug(f"[UIBusHistory] 转换命令记录失败: {e}")
            return
        try:
            self._queue.put_nowait(rec)
        except queue.Full:
            # 队列满：丢弃最旧一条，放入新的（保留热数据）
            try:
                _ = self._queue.get_nowait()
            except queue.Empty:
                pass
            with self._metrics_lock:
                self._metrics["dropped_overflow"] += 1
            try:
                self._queue.put_nowait(rec)
            except queue.Full:
                pass

    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(
            target=self._run, name="ui-bus-history-sink", daemon=True,
        )
        self._worker.start()
        logger.info(
            f"[UIBusHistory] 已启动 → 本地 {self._dir} / 同步后端={'on' if self._enable_cloud else 'off'}"
        )

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=timeout)

    def get_metrics(self) -> Dict[str, int]:
        with self._metrics_lock:
            return dict(self._metrics)

    def list_history(
        self,
        wxid: Optional[str] = None,
        kind: Optional[str] = None,
        status: Optional[str] = None,
        since: Optional[str] = None,
        limit: int = 200,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """从本地 JSONL 读取命令历史（倒序 = 最新在前）。

        - ``since``: ISO 日期字符串 ``YYYY-MM-DD`` 或 ``YYYY-MM-DDTHH:MM:SS``
        - ``limit`` 上限 1000，防止一次吃爆内存
        """
        limit = max(1, min(limit, 1000))
        offset = max(0, offset)

        since_ts = _parse_since_ts(since) if since else 0.0
        files = self._history_files()
        out: List[Dict[str, Any]] = []
        # 倒序遍历文件（最新天开始），每个文件内部也倒序行
        for fp in reversed(files):
            try:
                lines = fp.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue
            for raw in reversed(lines):
                if len(out) - offset >= limit:
                    return out[offset:offset + limit]
                if not raw.strip():
                    continue
                try:
                    rec = json.loads(raw)
                except Exception:
                    continue
                if wxid is not None and rec.get("wxid", "") != wxid:
                    continue
                if kind and rec.get("kind") != kind:
                    continue
                if status:
                    if status == "failed_or_timeout":
                        if rec.get("status") not in ("failed", "timeout"):
                            continue
                    elif rec.get("status") != status:
                        continue
                if since_ts and (rec.get("finished_ts") or 0) < since_ts:
                    # 这个文件可能整体都早于 since_ts；不过仍然让循环自然结束
                    continue
                out.append(rec)
        # 处理 offset/limit 兜底
        if offset:
            return out[offset:offset + limit]
        return out[:limit]

    # ---------- internals ----------

    def _run(self) -> None:
        # 启动时先清理过期文件，避免累积
        try:
            self._rotate_and_gc()
        except Exception as e:
            logger.debug(f"[UIBusHistory] 启动清理异常: {e}")

        while not self._stop.is_set():
            try:
                rec = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            with self._metrics_lock:
                self._metrics["total_sunk"] += 1

            # 1) 本地 JSONL（基本不会失败，但加 try）
            try:
                self._append_local(rec)
                with self._metrics_lock:
                    self._metrics["local_ok"] += 1
            except Exception as e:
                with self._metrics_lock:
                    self._metrics["local_fail"] += 1
                logger.debug(f"[UIBusHistory] 本地落盘失败: {e}")

            # 2) 同步后端事件（失败时 report_event 内部会入队重试）
            if self._enable_cloud:
                try:
                    self._push_cloud(rec)
                    with self._metrics_lock:
                        self._metrics["cloud_ok"] += 1
                except Exception as e:
                    with self._metrics_lock:
                        self._metrics["cloud_fail"] += 1
                    logger.debug(f"[UIBusHistory] 同步后端推送异常: {e}")

    def _append_local(self, rec: Dict[str, Any]) -> None:
        today = datetime.date.today().strftime("%Y%m%d")
        if today != self._last_rotate_day:
            # 新的一天：清旧文件
            self._last_rotate_day = today
            try:
                self._rotate_and_gc()
            except Exception as e:
                logger.debug(f"[UIBusHistory] 滚动清理异常: {e}")
        fp = self._dir / f"history-{today}.jsonl"
        line = json.dumps(rec, ensure_ascii=False)
        # 追加写：单行 JSON + 换行
        with fp.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _push_cloud(self, rec: Dict[str, Any]) -> None:
        from src.utils.cloud_sync import get_cloud_client
        client = get_cloud_client()
        # event_data 保持 UI 层能直接消费的结构
        client.report_event("ui_bus_command", {
            "command_id": rec.get("id"),
            "wxid": rec.get("wxid") or "",
            "kind": rec.get("kind"),
            "status": rec.get("status"),
            "priority": rec.get("priority"),
            "error": rec.get("error"),
            "submit_ts": rec.get("submit_ts"),
            "started_ts": rec.get("started_ts"),
            "finished_ts": rec.get("finished_ts"),
            "elapsed_seconds": rec.get("elapsed_seconds"),
            "queued_seconds": rec.get("queued_seconds"),
            # payload 可能较大：只留精简摘要，减少 events 表膨胀
            "payload_summary": _compact_payload(rec.get("payload") or {}),
            "result_summary": _compact_result(rec.get("result")),
        })

    def _history_files(self) -> List[Path]:
        if not self._dir.exists():
            return []
        files = sorted(self._dir.glob("history-*.jsonl"))
        return files

    def _rotate_and_gc(self) -> None:
        """按 retention_days 清理过旧的历史文件。"""
        cutoff = datetime.date.today() - datetime.timedelta(
            days=self._retention_days,
        )
        for fp in self._history_files():
            try:
                day_str = fp.stem.replace("history-", "")
                day = datetime.datetime.strptime(day_str, "%Y%m%d").date()
            except Exception:
                continue
            if day < cutoff:
                try:
                    fp.unlink()
                    logger.info(f"[UIBusHistory] 清理过期历史文件: {fp.name}")
                except Exception:
                    pass


# ===== 工具函数 =====

def _parse_since_ts(since: str) -> float:
    """把 since 串解析成 epoch 秒；解析失败返回 0（不过滤）。"""
    fmts = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d")
    for fmt in fmts:
        try:
            dt = datetime.datetime.strptime(since, fmt)
            return dt.timestamp()
        except Exception:
            continue
    return 0.0


def _compact_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """只保留常用 key 的摘要，防止同步后端行过胖。"""
    keep_keys = {
        "text", "target", "wxid_target", "tag", "task_id", "source",
        "remark", "group_name", "message", "count",
    }
    out: Dict[str, Any] = {}
    for k, v in payload.items():
        if k not in keep_keys:
            continue
        if isinstance(v, str) and len(v) > 200:
            out[k] = v[:200] + "…"
        else:
            out[k] = v
    return out


def _compact_result(result: Any) -> Any:
    if isinstance(result, (bool, int, float)) or result is None:
        return result
    if isinstance(result, str):
        return result[:200]
    if isinstance(result, dict):
        # 只保留常见的 success/ok/error 字段
        out = {}
        for k in ("success", "ok", "error", "message", "status"):
            if k in result:
                out[k] = result[k]
        if not out:
            # 兜底：记录 keys，不记录 values，避免敏感数据泄露
            out["_keys"] = list(result.keys())[:10]
        return out
    return repr(result)[:200]


# ===== 全局单例 =====

_singleton: Optional[CommandHistorySink] = None
_singleton_lock = threading.Lock()


def get_command_history_sink() -> CommandHistorySink:
    global _singleton
    if _singleton is not None:
        return _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = CommandHistorySink()
    return _singleton
