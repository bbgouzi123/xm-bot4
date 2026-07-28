import json as _json
from typing import List
from .storage import _lock, _queue, _now, _async_sync

def get_all_tags() -> List[str]:
    """获取所有已使用的标签"""
    all_tags = set()
    with _lock:
        for q in _queue:
            raw = q.get("tags", "")
            if not raw or raw == "[]":
                continue
            try:
                tag_list = _json.loads(raw)
                if isinstance(tag_list, list):
                    all_tags.update(tag_list)
            except (ValueError, TypeError):
                if raw:
                    all_tags.add(raw)
    return sorted(all_tags)


def batch_set_tags(ids: List[int], tags: List[str]):
    """批量给记录设置标签（合并，不覆盖）"""
    if not ids or not tags:
        return
    id_set = set(ids)
    with _lock:
        for q in _queue:
            if q.get("id") not in id_set:
                continue
            current = []
            try:
                current = _json.loads(q.get("tags", "[]"))
            except (ValueError, TypeError):
                pass
            merged = list(dict.fromkeys(current + tags))
            q["tags"] = _json.dumps(merged, ensure_ascii=False)
            q["updated_at"] = _now()
    _async_sync()


def batch_remove_tags(ids: List[int], tags: List[str]):
    """批量移除记录的标签"""
    if not ids or not tags:
        return
    id_set = set(ids)
    remove_set = set(tags)
    with _lock:
        for q in _queue:
            if q.get("id") not in id_set:
                continue
            current = []
            try:
                current = _json.loads(q.get("tags", "[]"))
            except (ValueError, TypeError):
                pass
            filtered = [t for t in current if t not in remove_set]
            q["tags"] = _json.dumps(filtered, ensure_ascii=False)
            q["updated_at"] = _now()
    _async_sync()
