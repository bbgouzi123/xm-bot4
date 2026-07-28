import time
import logging
import threading
from collections import defaultdict, deque
from typing import Any, Deque, Dict, Optional
from .types import UICommand, UICommandPriority

logger = logging.getLogger(__name__)

class UIBusScheduler:
    PRIO_W = 5.0

    def __init__(self) -> None:
        self.queues: Dict[str, Deque[UICommand]] = defaultdict(deque)
        self.queues_lock = threading.Lock()
        self.rr_cursor: int = 0
        self.active_workflow_session: Optional[str] = None
        self.active_workflow_wxid: Optional[str] = None

    def acquire_session_lock(self, wxid: str, session_name: str):
        with self.queues_lock:
            self.active_workflow_session = session_name
            self.active_workflow_wxid = wxid
            logger.info(f"[UIBus] 会话工作流独占锁已启用: wxid={wxid}, session={session_name}")

    def release_session_lock(self, wxid: str, session_name: str):
        with self.queues_lock:
            if self.active_workflow_session == session_name:
                self.active_workflow_session = None
                self.active_workflow_wxid = None
                logger.info(f"[UIBus] 会话工作流独占锁已释放: wxid={wxid}, session={session_name}")

    def insert_queue(self, command: UICommand):
        with self.queues_lock:
            q = self.queues[command.wxid]
            idx = 0
            for idx in range(len(q)):
                if q[idx].priority < command.priority:
                    break
            else:
                idx = len(q)
            if idx >= len(q):
                q.append(command)
            else:
                q.insert(idx, command)

    def remove_queue(self, cmd: UICommand) -> bool:
        with self.queues_lock:
            q = self.queues.get(cmd.wxid)
            if q and cmd in q:
                q.remove(cmd)
                return True
        return False

    def pick_next(self) -> Optional[UICommand]:
        with self.queues_lock:
            active_session = self.active_workflow_session
            active_wxid = self.active_workflow_wxid

            best: Optional[UICommand] = None
            best_wxid = ""
            for wxid, q in self.queues.items():
                if not q:
                    continue
                if active_session is not None and wxid != active_wxid:
                    continue

                for cmd in q:
                    if active_session is not None:
                        target = cmd.payload.get("target") if cmd.payload else None
                        if target != active_session:
                            continue

                    if cmd.priority >= UICommandPriority.HIGH:
                        if (
                            not best
                            or cmd.priority > best.priority
                            or cmd.submit_ts < best.submit_ts
                        ):
                            best = cmd
                            best_wxid = wxid
                    break

            if best is not None:
                self.queues[best_wxid].remove(best)
                return best

            wxids = [w for w, q in self.queues.items() if q]
            if active_session is not None:
                if active_wxid not in wxids:
                    return None
                wxids = [active_wxid]

            if not wxids:
                return None

            now = time.time()
            best_score = float("-inf")
            best_wxid = wxids[0]
            best_cmd: Optional[UICommand] = None

            for wxid in wxids:
                q = self.queues[wxid]
                matched_cmd = None
                for cmd in q:
                    if active_session is not None:
                        target = cmd.payload.get("target") if cmd.payload else None
                        if target != active_session:
                            continue
                    matched_cmd = cmd
                    break

                if matched_cmd is None:
                    continue

                wait = max(0.0, now - matched_cmd.submit_ts)
                score = int(matched_cmd.priority) * self.PRIO_W + wait
                if score > best_score or (
                    abs(score - best_score) < 1e-6
                    and wxids.index(wxid) > self.rr_cursor
                ):
                    best_score = score
                    best_wxid = wxid
                    best_cmd = matched_cmd

            if best_cmd is not None:
                self.queues[best_wxid].remove(best_cmd)
                try:
                    self.rr_cursor = wxids.index(best_wxid)
                except ValueError:
                    self.rr_cursor = 0
                return best_cmd

            return None
