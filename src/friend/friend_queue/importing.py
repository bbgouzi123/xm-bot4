import json as _json
from typing import List, Dict, Any, Optional
from .storage import _lock, _queue, _now, _async_sync, logger
from . import storage

def import_contacts(
    contacts: List[Dict],
    source_file: str = "",
    import_all_phones: bool = False,
    original_filename: str = "",
    tags: Optional[List[str]] = None,
    import_batch_id: str = "",
    industry_profile_id: str = "",
    industry_profile_name: str = "",
) -> Dict[str, Any]:
    """批量导入联系人到待加好友队列"""
    now = _now()
    tags_json = _json.dumps(tags or [], ensure_ascii=False)
    new_records = []
    imported = 0
    skipped = 0

    with _lock:
        existing_phones = {q.get("phone", "") for q in _queue if q.get("phone")}

        for c in contacts:
            phones = c.get("phones", [])
            primary_phone = c.get("primary_phone", "")
            wechat = c.get("wechat_id", "")

            if import_all_phones and phones:
                identifiers = phones
            elif primary_phone:
                identifiers = [primary_phone]
            elif wechat:
                identifiers = [wechat]
            else:
                skipped += 1
                continue

            for ident in identifiers:
                if ident in existing_phones:
                    skipped += 1
                    continue

                is_phone = len(ident) == 11 and ident.startswith("1")
                record = {
                    "id": storage._next_id,
                    "phone": ident if is_phone else "",
                    "wechat_id": ident if not is_phone else (wechat or ""),
                    "company_name": c.get("company_name", ""),
                    "legal_person": c.get("legal_person", ""),
                    "extra_fields": c.get("extra_fields", {}),
                    "verify_message": "",
                    "tags": tags_json,
                    "status": "pending",
                    "nickname": "",
                    "error_msg": "",
                    "source_file": source_file,
                    "row_index": c.get("row_index", 0),
                    "original_filename": original_filename,
                    "import_batch_id": import_batch_id,
                    "industry_profile_id": industry_profile_id,
                    "industry_profile_name": industry_profile_name,
                    "created_at": now,
                    "updated_at": now,
                }
                _queue.append(record)
                new_records.append(record)
                storage._next_id += 1
                existing_phones.add(ident)
                imported += 1

    _async_sync()
    logger.info(f"[好友队列] 导入完成: {imported} 条, 跳过 {skipped} 条, 批次={import_batch_id}, 行业={industry_profile_name}")
    return {
        "success": True, 
        "imported": imported, 
        "skipped": skipped, 
        "batch_id": import_batch_id,
        "records": new_records
    }
