import json as _json
from typing import List, Dict, Any, Optional
from .storage import _lock, _queue, _now, _async_sync, logger

def update_status(queue_id: int, status: str, nickname: str = "", error_msg: str = ""):
    """更新单条记录状态"""
    with _lock:
        for q in _queue:
            if q.get("id") == queue_id:
                q["status"] = status
                if nickname:
                    q["nickname"] = nickname
                if error_msg:
                    q["error_msg"] = error_msg
                q["updated_at"] = _now()
                break
    _async_sync()


def reset_processing_to_pending() -> int:
    """将所有 processing 状态回退为 pending"""
    count = 0
    now = _now()
    with _lock:
        for q in _queue:
            if q.get("status") == "processing":
                q["status"] = "pending"
                q["updated_at"] = now
                count += 1
    if count > 0:
        _async_sync()
        logger.info(f"[好友队列] 回退 {count} 条 processing → pending")
    return count


def batch_reset_status(from_status: str, to_status: str = "pending") -> int:
    """批量重置状态"""
    count = 0
    now = _now()
    with _lock:
        for q in _queue:
            if q.get("status") == from_status:
                q["status"] = to_status
                q["error_msg"] = ""
                q["updated_at"] = now
                count += 1
    if count > 0:
        _async_sync()
        logger.info(f"[好友队列] 批量重置 {count} 条 {from_status} → {to_status}")
    return count


def batch_reset_by_import_id(import_batch_id: str, to_status: str = "pending") -> int:
    """按导入批次 ID 重置所有记录状态（用于全部重新开始）"""
    if not import_batch_id:
        return 0
    count = 0
    now = _now()
    with _lock:
        for q in _queue:
            if q.get("import_batch_id") is not None and str(q.get("import_batch_id")) == str(import_batch_id):
                q["status"] = to_status
                q["error_msg"] = ""
                q["updated_at"] = now
                count += 1
    if count > 0:
        _async_sync()
        logger.info(f"[好友队列] 按批次 {import_batch_id} 重置 {count} 条记录状态 → {to_status}")
    return count


def delete_batch_by_import_id(import_batch_id: str) -> int:
    """按导入批次 ID 物理清空该批次下所有队列数据，防止删除后本地批次再次死灰复燃"""
    if not import_batch_id:
        return 0
    count = 0
    with _lock:
        original_len = len(_queue)
        _queue[:] = [q for q in _queue if q.get("import_batch_id") is None or str(q.get("import_batch_id")) != str(import_batch_id)]
        count = original_len - len(_queue)
    if count > 0:
        _async_sync()
        logger.info(f"[好友队列] 按批次 {import_batch_id} 物理删除 {count} 条记录")
    return count


def delete_item(queue_id: int):
    """删除单条记录"""
    with _lock:
        _queue[:] = [q for q in _queue if q.get("id") != queue_id]
    _async_sync()


def clear_queue(status: Optional[str] = None):
    """清空队列（可按状态清空）"""
    with _lock:
        if status:
            _queue[:] = [q for q in _queue if q.get("status") != status]
        else:
            _queue.clear()
    _async_sync()


def batch_delete(ids: List[int]):
    """批量删除"""
    if not ids:
        return
    id_set = set(ids)
    with _lock:
        _queue[:] = [q for q in _queue if q.get("id") not in id_set]
    _async_sync()


def recycle_to_industry(
    new_industry_id: str,
    new_industry_name: str,
    recycle_mode: str = "same_account",
    source_industry_id: str = "",
    source_batch_id: str = "",
    source_tag: str = "",
    add_tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """号码回收复用：批量切换行业 + 重置状态 + 追加标签"""
    recycled = 0
    now = _now()

    if recycle_mode == "same_account":
        valid_statuses = {"failed", "unknown"}
    else:
        valid_statuses = {"added", "failed", "already", "unknown", "pending"}

    with _lock:
        for q in _queue:
            if q.get("status") not in valid_statuses:
                continue
            if source_industry_id and q.get("industry_profile_id") != source_industry_id:
                continue
            if source_batch_id and (q.get("import_batch_id") is None or str(q.get("import_batch_id")) != str(source_batch_id)):
                continue
            if source_tag and f'"{source_tag}"' not in (q.get("tags", "") or ""):
                continue

            current_tags = []
            try:
                current_tags = _json.loads(q.get("tags", "[]"))
            except (ValueError, TypeError):
                pass
            merged = list(dict.fromkeys(current_tags + (add_tags or [])))

            q["industry_profile_id"] = new_industry_id
            q["industry_profile_name"] = new_industry_name
            q["status"] = "pending"
            q["error_msg"] = ""
            q["tags"] = _json.dumps(merged, ensure_ascii=False)
            q["updated_at"] = now
            recycled += 1

    if recycled > 0:
        _async_sync()
    logger.info(f"[好友队列] 号码回收: {recycled} 条 → {new_industry_name}（模式={recycle_mode}）")
    return {"success": True, "recycled": recycled, "skipped": 0}
