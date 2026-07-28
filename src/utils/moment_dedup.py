"""
朋友圈互动去重 — 三层持久化：内存缓存 → 本地 JSON → 同步后端数据库

换电脑场景：启动时从同步后端 GET /api/v1/moment-interactions 拉取指纹集合，
合并本地文件指纹，统一写入内存缓存。记录互动时同时推本地+同步后端。

数据流：
  has_interacted()  → 查内存缓存（热路径 O(1)）
  record_interaction() → 写内存 + 写本地 JSON + 推同步后端 DB
  _ensure_cache()  → 首次调用时合并：同步后端指纹 ∪ 本地指纹 → 内存
"""
import json
import hashlib
import logging
import datetime
import threading
from typing import Dict

logger = logging.getLogger(__name__)

# 内存缓存（热路径加速）
_interacted_cache: Dict[str, set] = {}
# 标记是否已从同步后端拉取
_cloud_loaded: Dict[str, bool] = {}


def generate_moment_fingerprint(author: str, content: str) -> str:
    """为朋友圈生成唯一指纹，防止重复互动"""
    text = f"{author}:{content[:50]}"
    return hashlib.md5(text.encode('utf-8')).hexdigest()


def has_interacted(fingerprint: str, account_id: str = None) -> bool:
    """检查是否已互动过（内存缓存，首次自动加载同步后端+本地）"""
    cache = _ensure_cache(account_id)
    return fingerprint in cache


def record_interaction(
    author: str, content: str, action_type: str,
    fingerprint: str = None, account_id: str = None
):
    """记录一次互动（内存 + 本地文件 + 同步后端 DB）"""
    fp = fingerprint or generate_moment_fingerprint(author, content)
    cache = _ensure_cache(account_id)
    cache.add(fp)

    # 本地持久化
    def _local():
        try:
            records = _load_local(account_id)
            cutoff = datetime.datetime.now() - datetime.timedelta(hours=48)
            records = [r for r in records
                       if _parse_ts(r.get("timestamp", "")) >= cutoff]
            records.append({
                "publisher": author, "content": content[:200],
                "action_type": action_type, "fingerprint": fp,
                "timestamp": datetime.datetime.now().isoformat(),
            })
            _save_local(records, account_id)
        except Exception as e:
            logger.debug(f"[互动去重] 本地持久化失败: {e}")
    threading.Thread(target=_local, daemon=True, name="dedup-local").start()

    # 同步后端持久化
    def _cloud():
        try:
            from src.utils.cloud_sync import get_cloud_client
            get_cloud_client().sync_moment_interactions([{
                "author_name": author,
                "content_snippet": content[:200],
                "action_type": action_type,
                "fingerprint": fp,
            }])
        except Exception as e:
            logger.debug(f"[互动去重] 同步后端推送失败: {e}")
    threading.Thread(target=_cloud, daemon=True, name="dedup-cloud").start()


# ==================== 内部实现 ====================

def _ensure_cache(account_id: str) -> set:
    """确保缓存已加载（首次时合并同步后端+本地指纹）"""
    key = account_id or "default"
    if key not in _interacted_cache:
        fps = set()
        # 1. 从本地文件加载
        fps.update(_load_local_fingerprints(account_id))
        # 2. 从同步后端 DB 加载（换电脑核心）
        if not _cloud_loaded.get(key):
            cloud_fps = _load_cloud_fingerprints()
            if cloud_fps is not None:
                fps.update(cloud_fps)
                _cloud_loaded[key] = True
                logger.info(f"[互动去重] 同步后端拉取 {len(cloud_fps)} 条指纹，"
                            f"合并后共 {len(fps)} 条")
        _interacted_cache[key] = fps
    return _interacted_cache[key]


def _load_cloud_fingerprints() -> set:
    """从同步后端 GET /api/v1/moment-interactions 拉取指纹集合"""
    try:
        from src.utils.cloud_sync import get_cloud_client
        client = get_cloud_client()
        if not client or not getattr(client, 'jwt_token', None):
            return None
        data = client._get("/api/v1/moment-interactions?limit=2000",
                           need_auth=True)
        if not data or not isinstance(data, list):
            return set()
        cutoff = datetime.datetime.now() - datetime.timedelta(hours=48)
        fps = set()
        for item in data:
            fp = item.get("fingerprint", "")
            if not fp:
                continue
            ts = item.get("created_at", "")
            if ts and _parse_ts(ts) < cutoff:
                continue
            fps.add(fp)
        return fps
    except Exception as e:
        logger.warning(f"[互动去重] 同步后端拉取指纹失败: {e}")
        return None


def _load_local_fingerprints(account_id: str = None) -> set:
    """从本地 JSON 加载 48h 内指纹"""
    records = _load_local(account_id)
    cutoff = datetime.datetime.now() - datetime.timedelta(hours=48)
    fps = set()
    for item in records:
        ts = item.get("timestamp", "")
        if ts and _parse_ts(ts) < cutoff:
            continue
        fp = item.get("fingerprint", "")
        if fp:
            fps.add(fp)
    return fps


def _load_local(account_id: str = None) -> list:
    """从本地 JSON 文件读取互动记录"""
    import os
    path = _get_log_path(account_id)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read().strip()
            if not raw:
                return []
            data = json.loads(raw)
            return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"[互动去重] 读本地文件失败: {e}")
        return []


def _save_local(records: list, account_id: str = None):
    """保存互动记录到本地 JSON"""
    import os
    path = _get_log_path(account_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[互动去重] 写本地文件失败: {e}")


def _get_log_path(account_id: str = None) -> str:
    """互动记录本地文件路径"""
    import os
    try:
        from src.crm.account_data import get_account_data_dir
        return os.path.join(get_account_data_dir(account_id),
                            "moment_interactions.json")
    except Exception:
        return os.path.join(os.path.expanduser("~"), ".xm-ai-bot",
                            "moment_interactions.json")


def _parse_ts(ts_str: str) -> datetime.datetime:
    """安全解析时间戳"""
    try:
        if ts_str:
            s = ts_str.replace("Z", "+00:00")
            return datetime.datetime.fromisoformat(s).replace(tzinfo=None)
    except Exception:
        pass
    return datetime.datetime.min


def has_interacted_friend_recently(author: str, cooling_hours: int, account_id: str = None) -> bool:
    """判断某个好友最近 cooling_hours 小时内是否已经进行过任何互动（赞或评）"""
    if cooling_hours <= 0:
        return False
    try:
        records = _load_local(account_id)
        if not records:
            return False
        cutoff = datetime.datetime.now() - datetime.timedelta(hours=cooling_hours)
        for r in records:
            if r.get("publisher") == author:
                ts = r.get("timestamp", "")
                if ts and _parse_ts(ts) >= cutoff:
                    return True
    except Exception as e:
        logger.debug(f"[去重服务] 冷却时间检测异常: {e}")
    return False
