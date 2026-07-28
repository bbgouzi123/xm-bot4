import enum
import time
import uuid
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

class UICommandKind(str, enum.Enum):
    SEND_MESSAGE = "send_message"
    SEND_FILE = "send_file"
    SEND_VOICE = "send_voice"
    PUBLISH_MOMENT = "publish_moment"
    MOMENT_INTERACT = "moment_interact"
    ADD_FRIEND = "add_friend"
    ACCEPT_FRIEND = "accept_friend"
    SYNC_TAGS = "sync_tags"
    FETCH_AVATAR = "fetch_avatar"
    EXTRACT_USER_INFO = "extract_user_info"
    ENABLE_VOICE_TO_TEXT = "enable_voice_to_text"
    CUSTOM = "custom"

class UICommandPriority(int, enum.Enum):
    LOW = 0
    NORMAL = 5
    HIGH = 10
    URGENT = 20

class UICommandStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELED = "canceled"

@dataclass
class UICommand:
    wxid: str
    kind: UICommandKind
    payload: Dict[str, Any] = field(default_factory=dict)
    priority: UICommandPriority = UICommandPriority.NORMAL
    timeout: float = 60.0
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    submit_ts: float = field(default_factory=time.time)
    started_ts: Optional[float] = None
    finished_ts: Optional[float] = None
    status: UICommandStatus = UICommandStatus.QUEUED
    result: Any = None
    error: Optional[str] = None
    _done_event: threading.Event = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        self._done_event = threading.Event()

    def to_dict(self) -> Dict[str, Any]:
        def _safe_serialize(val: Any) -> Any:
            if isinstance(val, (str, int, float, bool, type(None))):
                return val
            if isinstance(val, (list, tuple, set)):
                return [_safe_serialize(x) for x in val]
            if isinstance(val, dict):
                return {str(k): _safe_serialize(v) for k, v in val.items() if not (k == "fn" or callable(v))}
            if isinstance(val, enum.Enum):
                return val.value
            if callable(val):
                return getattr(val, "__name__", str(val))
            if hasattr(val, "to_dict") and callable(val.to_dict):
                try:
                    return val.to_dict()
                except Exception:
                    pass
            return str(val)

        return {
            "wxid": self.wxid,
            "kind": self.kind.value if isinstance(self.kind, enum.Enum) else self.kind,
            "payload": _safe_serialize(self.payload),
            "priority": int(self.priority),
            "timeout": self.timeout,
            "id": self.id,
            "submit_ts": self.submit_ts,
            "started_ts": self.started_ts,
            "finished_ts": self.finished_ts,
            "status": self.status.value if isinstance(self.status, enum.Enum) else self.status,
            "result": _safe_serialize(self.result),
            "error": self.error,
        }

UICommandHandler = Callable[[UICommand], Any]
