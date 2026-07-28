from typing import List, Dict
from .storage import _lock, _queue

def get_stats_by_industry() -> List[Dict]:
    """按行业统计导入数量和状态"""
    counter: Dict[str, Dict[str, int]] = {}
    with _lock:
        for q in _queue:
            name = q.get("industry_profile_name", "")
            if not name:
                continue
            st = q.get("status", "unknown")
            if name not in counter:
                counter[name] = {}
            counter[name][st] = counter[name].get(st, 0) + 1

    result = []
    for name, statuses in sorted(counter.items()):
        for st, cnt in statuses.items():
            result.append({"industry_profile_name": name, "status": st, "cnt": cnt})
    return result


def get_stats_by_tag() -> List[Dict]:
    """按标签统计平衡数据"""
    counter: Dict[str, Dict[str, int]] = {}
    with _lock:
        for q in _queue:
            raw = q.get("tags", "")
            if not raw or raw == "[]":
                continue
            try:
                import json as _json
                tag_list = _json.loads(raw)
            except (ValueError, TypeError):
                tag_list = [raw] if raw else []
            st = q.get("status", "unknown")
            for tag in tag_list:
                if tag not in counter:
                    counter[tag] = {}
                counter[tag][st] = counter[tag].get(st, 0) + 1

    result = []
    for tag, statuses in sorted(counter.items()):
        total = sum(statuses.values())
        result.append({"tag": tag, "total": total, **statuses})
    return result


def get_import_batches() -> List[Dict]:
    """获取所有导入批次（按时间倒序）"""
    batches: Dict[str, dict] = {}
    with _lock:
        for q in _queue:
            bid = q.get("import_batch_id", "")
            if not bid:
                continue
            if bid not in batches:
                batches[bid] = {
                    "import_batch_id": bid,
                    "original_filename": q.get("original_filename", ""),
                    "industry_profile_name": q.get("industry_profile_name", ""),
                    "cnt": 0,
                    "imported_at": q.get("created_at", ""),
                }
            batches[bid]["cnt"] += 1
            # 取最早的 created_at
            ca = q.get("created_at", "")
            if ca and ca < batches[bid]["imported_at"]:
                batches[bid]["imported_at"] = ca

    result = list(batches.values())
    result.sort(key=lambda x: x.get("imported_at", ""), reverse=True)
    return result
