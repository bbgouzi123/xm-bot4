"""
通讯录同步续跑记录存储（P2 第一阶段）

用于长链路 UIA 任务中断后的续跑能力。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Dict, Any, Optional, Set


class ContactSyncCheckpointStore:
    FILE_PATH = Path.home() / ".xm-ai-bot" / "contact_sync_checkpoint.json"
    MAX_SEEN_NAMES = 8000
    _lock = threading.Lock()

    @staticmethod
    def _key(account_id: str, category: str) -> str:
        return f"v2_{account_id}_{category}"

    def _load_all(self) -> Dict[str, Any]:
        try:
            if self.FILE_PATH.exists():
                data = json.loads(self.FILE_PATH.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def _save_all(self, data: Dict[str, Any]):
        self.FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.FILE_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self, account_id: str, category: str) -> Optional[Dict[str, Any]]:
        key = self._key(account_id, category)
        with self._lock:
            all_data = self._load_all()
            checkpoint = all_data.get(key)
            if isinstance(checkpoint, dict):
                return checkpoint
        return None

    def save_running(
        self,
        *,
        account_id: str,
        category: str,
        state: str,
        scroll_round: int,
        seen_names: Set[str],
        total: int,
        new_count: int,
    ):
        key = self._key(account_id, category)
        seen_list = [x for x in seen_names if x]
        if len(seen_list) > self.MAX_SEEN_NAMES:
            seen_list = seen_list[-self.MAX_SEEN_NAMES:]
        payload = {
            "state": state,
            "scroll_round": int(scroll_round),
            "seen_names": seen_list,
            "total": int(total),
            "new": int(new_count),
            "updated_at": int(time.time()),
        }
        with self._lock:
            all_data = self._load_all()
            all_data[key] = payload
            self._save_all(all_data)

    def save_failed(
        self,
        *,
        account_id: str,
        category: str,
        scroll_round: int,
        seen_names: Set[str],
        total: int,
        new_count: int,
        error: str,
    ):
        self.save_running(
            account_id=account_id,
            category=category,
            state="FAILED",
            scroll_round=scroll_round,
            seen_names=seen_names,
            total=total,
            new_count=new_count,
        )
        key = self._key(account_id, category)
        with self._lock:
            all_data = self._load_all()
            if key in all_data and isinstance(all_data[key], dict):
                all_data[key]["error"] = error
            self._save_all(all_data)

    def clear(self, account_id: str, category: str):
        key = self._key(account_id, category)
        with self._lock:
            all_data = self._load_all()
            if key in all_data:
                all_data.pop(key, None)
                self._save_all(all_data)

    def list_records(self, account_id: Optional[str] = None) -> Dict[str, Any]:
        """获取续跑记录列表；account_id 为空时返回全部。"""
        with self._lock:
            all_data = self._load_all()
            if not account_id:
                return all_data

            prefix = f"{account_id}::"
            return {
                k: v
                for k, v in all_data.items()
                if isinstance(k, str) and k.startswith(prefix)
            }

    def clear_by_prefix(self, prefix: str) -> int:
        """按 key 前缀批量清理，返回清理数量。"""
        with self._lock:
            all_data = self._load_all()
            keys = [k for k in all_data.keys() if isinstance(k, str) and k.startswith(prefix)]
            for k in keys:
                all_data.pop(k, None)
            if keys:
                self._save_all(all_data)
            return len(keys)

    @staticmethod
    def summarize(records: Dict[str, Any]) -> Dict[str, Any]:
        """将续跑记录聚合成可直接给前端展示的恢复快照。"""
        now = int(time.time())
        state_priority = {"RUNNING": 4, "FAILED": 3, "INTERRUPTED": 2, "COMPLETED": 1}
        best_state = "NONE"
        best_score = 0
        checkpoint_age = None
        remaining_estimate = None

        for key, rec in records.items():
            if not isinstance(rec, dict):
                continue
            state = str(rec.get("state") or "")
            score = state_priority.get(state, 0)
            if score > best_score:
                best_score = score
                best_state = state

            updated_at = int(rec.get("updated_at", 0) or 0)
            if updated_at > 0:
                age = max(0, now - updated_at)
                checkpoint_age = age if checkpoint_age is None else min(checkpoint_age, age)

            # details 续跑记录可估算剩余项
            if isinstance(key, str) and "::details::" in key:
                total = int(rec.get("total", 0) or 0)
                done = int(rec.get("new", 0) or 0)
                left = max(0, total - done)
                if remaining_estimate is None:
                    remaining_estimate = left
                else:
                    remaining_estimate += left

        return {
            "resume_state": best_state,
            "checkpoint_age": checkpoint_age,
            "remaining_estimate": remaining_estimate,
            "checkpoints": len(records),
        }
