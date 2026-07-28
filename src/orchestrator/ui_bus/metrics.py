import time
import threading
from typing import Any, Dict, List, Optional
from .types import UICommand, UICommandStatus

class UIBusMetrics:
    def __init__(self, bucket_window: int = 60) -> None:
        self.metrics = {
            "submitted": 0,
            "succeeded": 0,
            "failed": 0,
            "timeout": 0,
            "canceled": 0,
            "last_active_wxid": "",
            "current_running_cmd": None,
        }
        self.minute_buckets: Dict[int, Dict[str, int]] = {}
        self.minute_bucket_lock = threading.Lock()
        self.minute_bucket_window = bucket_window

    def record_minute_bucket(self, cmd: UICommand) -> None:
        ts = cmd.finished_ts or time.time()
        minute = int(ts // 60)
        ok = cmd.status == UICommandStatus.SUCCESS
        with self.minute_bucket_lock:
            b = self.minute_buckets.get(minute)
            if b is None:
                b = {"ok": 0, "ko": 0}
                self.minute_buckets[minute] = b
            if ok:
                b["ok"] += 1
            else:
                b["ko"] += 1

            cutoff = minute - self.minute_bucket_window
            if len(self.minute_buckets) > self.minute_bucket_window * 2:
                for k in list(self.minute_buckets.keys()):
                    if k <= cutoff:
                        self.minute_buckets.pop(k, None)

    def get_minute_series(self, window: Optional[int] = None) -> List[Dict[str, Any]]:
        n = window or self.minute_bucket_window
        now_min = int(time.time() // 60)
        start = now_min - n + 1
        out: List[Dict[str, Any]] = []
        with self.minute_bucket_lock:
            for m in range(start, now_min + 1):
                b = self.minute_buckets.get(m)
                out.append({
                    "minute_ts": m * 60,
                    "ok": b["ok"] if b else 0,
                    "ko": b["ko"] if b else 0,
                })
        return out
