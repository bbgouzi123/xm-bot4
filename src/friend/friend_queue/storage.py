import logging
import threading
import json as _json
import copy
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)

# ===== 全局内存存储 =====
_queue: List[dict] = []              # 主队列
_logs: List[dict] = []               # 操作日志
_daily_counts: Dict[str, Dict[str, int]] = {}  # {date: {account_id: count}}
_next_id = 1
_next_log_id = 1
_lock = threading.RLock()

_LOCAL_DIR = Path.home() / ".xm-ai-bot"

def _get_fq_snapshot_path() -> Path:
    from src.crm.account_data import get_active_account
    bot_q = get_active_account() or "main"
    safe_bot = "".join(c for c in bot_q if c.isalnum() or c in ("-", "_"))
    return _LOCAL_DIR / f"friend_queue_snapshot_{safe_bot}.json"


def _now() -> str:
    return datetime.now().isoformat()


def _snapshot_payload_unlocked() -> dict:
    """调用方已持有 _lock"""
    return {
        "queue": list(_queue),
        "logs": list(_logs),
        "next_id": _next_id,
        "next_log_id": _next_log_id,
        "daily_counts": copy.deepcopy(_daily_counts),
        "saved_at": datetime.now().isoformat(),
    }


def _flush_local_snapshot(payload: dict) -> None:
    try:
        _LOCAL_DIR.mkdir(parents=True, exist_ok=True)
        snapshot_path = _get_fq_snapshot_path()
        tmp = snapshot_path.with_suffix(".json.tmp")
        tmp.write_text(_json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(snapshot_path)
        logger.debug(
            f"[好友队列] 本地快照已保存 queue={len(payload.get('queue', []))} "
            f"logs={len(payload.get('logs', []))}"
        )
    except Exception as e:
        logger.warning(f"[好友队列] 本地快照保存失败: {e}")


def _reconcile_ids_unlocked():
    """保证 next_id / next_log_id 大于已有记录"""
    global _next_id, _next_log_id
    if _queue:
        mx = max(q.get("id", 0) for q in _queue)
        if _next_id <= mx:
            _next_id = mx + 1
    if _logs:
        mx = max(l.get("id", 0) for l in _logs)
        if _next_log_id <= mx:
            _next_log_id = mx + 1


def _load_local_snapshot() -> bool:
    """从本地快照恢复（持锁写入）"""
    global _next_id, _next_log_id, _daily_counts
    snapshot_path = _get_fq_snapshot_path()
    if not snapshot_path.exists():
        return False
    try:
        raw = _json.loads(snapshot_path.read_text(encoding="utf-8"))
        with _lock:
            _queue.clear()
            _logs.clear()
            _daily_counts.clear()
            for x in raw.get("queue", []) or []:
                if isinstance(x, dict):
                    _queue.append(x)
            for x in raw.get("logs", []) or []:
                if isinstance(x, dict):
                    _logs.append(x)
            _next_id = int(raw.get("next_id", 1))
            _next_log_id = int(raw.get("next_log_id", 1))
            dc = raw.get("daily_counts") or {}
            if isinstance(dc, dict):
                for day, accs in dc.items():
                    if not isinstance(accs, dict):
                        continue
                    _daily_counts[str(day)] = {str(a): int(c) for a, c in accs.items()}
            _reconcile_ids_unlocked()
        logger.info(f"[好友队列] 从本地快照恢复 queue={len(_queue)} logs={len(_logs)}")
        return bool(_queue or _logs or _daily_counts)
    except Exception as e:
        logger.warning(f"[好友队列] 本地快照加载失败: {e}")
        return False


def _persist_after_mutation():
    """落盘本地全量快照 + 异步推送主队列到同步后端"""
    with _lock:
        pl = _snapshot_payload_unlocked()
    _flush_local_snapshot(pl)

    def _push():
        try:
            from src.utils.cloud_sync import get_cloud_client
            get_cloud_client().sync_friend_queue(pl["queue"])
        except Exception as e:
            logger.debug(f"[好友队列] 同步后端推送失败: {e}")

    threading.Thread(target=_push, daemon=True, name="fq-push").start()


def _bootstrap_friend_queue():
    """启动：同步后端名单非空则采用同步后端并写回本地；否则读本地快照。"""
    global _next_id, _next_log_id
    cloud_queue_ok = False
    try:
        from src.utils.cloud_sync import get_cloud_client
        cloud = get_cloud_client()

        from src.crm.account_data import get_active_account
        bot_q = quote(get_active_account() or "", safe="")
        data = cloud._get(f"/api/v1/friend-queue?bot_wxid={bot_q}", need_auth=True)
        if data and isinstance(data, list) and len(data) > 0:
            with _lock:
                _queue.clear()
                _queue.extend(data)
                _next_id = max(q.get("id", 0) for q in _queue) + 1
            cloud_queue_ok = True
            logger.info(f"[好友队列] 从同步后端加载 {len(data)} 条名单")

        logs = cloud._get(f"/api/v1/friend-queue/logs?bot_wxid={bot_q}", need_auth=True)
        if logs and isinstance(logs, list) and len(logs) > 0:
            with _lock:
                _logs.clear()
                _logs.extend(logs)
                _next_log_id = max(l.get("id", 0) for l in _logs) + 1
            logger.info(f"[好友队列] 从同步后端加载 {len(logs)} 条日志")

    except Exception as e:
        logger.debug(f"[好友队列] 同步后端加载跳过: {e}")

    if cloud_queue_ok:
        with _lock:
            _reconcile_ids_unlocked()
            pl = _snapshot_payload_unlocked()
        _flush_local_snapshot(pl)
        logger.info(f"[好友队列] 内存初始化完成（{len(_queue)} 条名单）")
        return

    if _load_local_snapshot():
        logger.info(f"[好友队列] 内存初始化完成（{len(_queue)} 条名单，来源：本地快照）")
        return

    logger.info(f"[好友队列] 内存初始化完成（{len(_queue)} 条名单）")


def reload_from_cloud_for_active_bot():
    """接管微信切换后：按当前 bot_wxid 重拉同步后端获客名单（与 SSO 登录方式无关）。"""
    global _next_id, _next_log_id
    try:
        from src.utils.cloud_sync import get_cloud_client
        from src.crm.account_data import get_active_account
        cloud = get_cloud_client()
        bot_q = quote(get_active_account() or "", safe="")
        data = cloud._get(f"/api/v1/friend-queue?bot_wxid={bot_q}", need_auth=True)
        with _lock:
            _queue.clear()
            if data and isinstance(data, list):
                _queue.extend(data)
                _next_id = max((q.get("id", 0) for q in _queue), default=0) + 1
            else:
                _next_id = 1
            _reconcile_ids_unlocked()
            pl = _snapshot_payload_unlocked()
        _flush_local_snapshot(pl)
        logger.info(f"[好友队列] 已按接管微信刷新名单 queue={len(_queue)}")
    except Exception as e:
        logger.warning(f"[好友队列] 切换后同步后端刷新失败: {e}，尝试退回到本地快照加载")
        _load_local_snapshot()


def _async_sync():
    """兼容旧名：落盘 + 异步推云"""
    _persist_after_mutation()


def get_today_count(account_id: str = "main") -> int:
    """获取今日添加数量"""
    today = datetime.now().strftime("%Y-%m-%d")
    with _lock:
        return _daily_counts.get(today, {}).get(account_id, 0)


def increment_today_count(account_id: str = "main") -> int:
    """今日计数 +1，返回最新值"""
    today = datetime.now().strftime("%Y-%m-%d")
    with _lock:
        if today not in _daily_counts:
            _daily_counts[today] = {}
        _daily_counts[today][account_id] = _daily_counts[today].get(account_id, 0) + 1
        val = _daily_counts[today][account_id]
    _persist_after_mutation()
    return val


_fq_initialized = False
_fq_init_lock = threading.Lock()

def bootstrap_friend_queue_lazy():
    """懒加载/就绪后初始化加粉队列，确保有有效 Token 才会向云端发起请求"""
    global _fq_initialized
    if _fq_initialized:
        return
    with _fq_init_lock:
        if _fq_initialized:
            return
        from src.utils.cloud_sync.helpers import try_load_sso_token
        token = try_load_sso_token()
        if not token:
            logger.debug("[好友队列] SSO Token 尚未就绪，暂不联网初始化")
            return
        
        # 已有有效 Token，启动线程异步向云端同步
        threading.Thread(target=_bootstrap_friend_queue, daemon=True, name="fq-init").start()
        _fq_initialized = True

# 启动初始化（构建/打包时不启动线程避免死锁与外部请求）
import sys
import os

def _is_build_env() -> bool:
    if "PyInstaller" in sys.modules or "setuptools" in sys.modules:
        return True
    main_file = os.path.basename(sys.argv[0]).lower() if sys.argv else ""
    if any(x in main_file for x in ["pyinstaller", "setup.py", "build_protected"]):
        return True
    return False

if not _is_build_env():
    bootstrap_friend_queue_lazy()

