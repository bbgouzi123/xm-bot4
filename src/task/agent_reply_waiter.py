import asyncio
import time
from typing import Dict, Optional, Any, List

class AgentReplyWaiter:
    """Agent 回复等待器
    持有所有处于 PENDING_AGENT 状态的任务等待句柄。
    """
    def __init__(self):
        self._pending: Dict[str, Dict[str, Any]] = {}

    def register(self, task_id: str, session_name: str):
        self._pending[task_id] = {
            'event': asyncio.Event(),
            'result': None,
            'registered_at': time.time(),
            'session_name': session_name
        }

    async def wait_result(self, task_id: str, timeout: float = 30.0) -> Optional[Dict[str, Any]]:
        entry = self._pending.get(task_id)
        if not entry:
            return None
        try:
            await asyncio.wait_for(entry['event'].wait(), timeout=timeout)
            result = entry.get('result')
        except asyncio.TimeoutError:
            result = None
        finally:
            self._pending.pop(task_id, None)
        return result

    def submit_result(self, task_id: str, action: str, reply: Optional[str] = None, reason: Optional[str] = None) -> bool:
        entry = self._pending.get(task_id)
        if not entry:
            return False
        entry['result'] = {
            'action': action,
            'reply': reply,
            'reason': reason
        }
        entry['event'].set()
        return True

    def is_pending(self, task_id: str) -> bool:
        return task_id in self._pending

    def get_pending_list(self) -> List[Dict[str, Any]]:
        now = time.time()
        return [
            {
                "taskId": tid,
                "sessionName": entry["session_name"],
                "registeredAt": entry["registered_at"],
                "waitingSeconds": round(now - entry["registered_at"], 1)
            }
            for tid, entry in self._pending.items()
        ]

agent_reply_waiter = AgentReplyWaiter()
