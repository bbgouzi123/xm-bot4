from typing import List, Dict, Any, Optional
from .storage import _lock, _queue

def get_queue_list(
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    keyword: str = "",
    tag: Optional[str] = None,
    industry_profile_id: Optional[str] = None,
    import_batch_id: Optional[str] = None,
) -> Dict[str, Any]:
    """获取好友队列列表（分页+多维筛选）"""
    with _lock:
        filtered = list(_queue)

    # 筛选
    if status:
        filtered = [q for q in filtered if q.get("status") == status]
    if keyword:
        kw = keyword.lower()
        filtered = [q for q in filtered if
                    kw in (q.get("company_name", "") or "").lower() or
                    kw in (q.get("phone", "") or "").lower() or
                    kw in (q.get("legal_person", "") or "").lower()]
    if tag:
        filtered = [q for q in filtered if f'"{tag}"' in (q.get("tags", "") or "")]
    if industry_profile_id:
        filtered = [q for q in filtered if q.get("industry_profile_id") == industry_profile_id]
    if import_batch_id:
        target_ids = [bid.strip() for bid in import_batch_id.split(",") if bid.strip()]
        if target_ids:
            ent_sources = [f"enterprise:{bid}" for bid in target_ids]
            filtered = [q for q in filtered if (q.get("import_batch_id") is not None and str(q.get("import_batch_id")) in target_ids) or q.get("source_file") in ent_sources]

    total = len(filtered)
    # 按 id 倒序
    filtered.sort(key=lambda x: x.get("id", 0), reverse=True)
    # 分页
    offset = (page - 1) * page_size
    items = filtered[offset:offset + page_size]

    # 状态统计
    status_counts: Dict[str, int] = {}
    with _lock:
        for q in _queue:
            st = q.get("status", "unknown")
            status_counts[st] = status_counts.get(st, 0) + 1

    return {
        "success": True,
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "status_counts": status_counts,
    }


def get_pending(
    limit: int = 5,
    include_failed: bool = False,
    include_unknown: bool = False,
    skip_processing: bool = False,
    industry_profile_id: str = "",
    tag: str = "",
    import_batch_id: str = "",
) -> List[Dict]:
    """获取待添加好友（支持多维筛选+重试策略）"""
    valid_statuses = {"pending"}
    if not skip_processing:
        valid_statuses.add("processing")
    if include_failed:
        valid_statuses.add("failed")
    if include_unknown:
        valid_statuses.add("unknown")

    with _lock:
        candidates = [q for q in _queue if q.get("status") in valid_statuses]

    if industry_profile_id:
        candidates = [q for q in candidates if q.get("industry_profile_id") == industry_profile_id]
    if tag:
        candidates = [q for q in candidates if f'"{tag}"' in (q.get("tags", "") or "")]
    if import_batch_id:
        target_ids = [bid.strip() for bid in import_batch_id.split(",") if bid.strip()]
        if target_ids:
            candidates = [q for q in candidates if q.get("import_batch_id") is not None and str(q.get("import_batch_id")) in target_ids]

    # 排序：processing 优先，然后 pending，最后其他
    priority = {"processing": 0, "pending": 1}
    candidates.sort(key=lambda x: (priority.get(x.get("status", ""), 2), x.get("id", 0)))
    return candidates[:limit]


def get_queue_stats() -> Dict[str, Any]:
    """获取队列统计信息"""
    with _lock:
        total = len(_queue)
        status_counts: Dict[str, int] = {}
        for q in _queue:
            st = q.get("status", "unknown")
            status_counts[st] = status_counts.get(st, 0) + 1

    return {
        "total": total,
        "pending": status_counts.get("pending", 0),
        "added": status_counts.get("added", 0),
        "failed": status_counts.get("failed", 0),
        "already": status_counts.get("already", 0),
        "unknown": status_counts.get("unknown", 0),
        "processing": status_counts.get("processing", 0),
    }
