import os
import logging

logger = logging.getLogger("WcdbHelpers")

_verified_key_cache = {}

def detect_all_db_storage_dirs() -> list:
    """
    遍历所有 xwechat_files 下的账号目录，返回所有存在的 db_storage 目录路径列表。
    用于配合密钥校验确认正确账号。
    """
    from src.wechat_4x.db_match_helper import get_wechat_base_dirs
    result = []
    base_dirs = get_wechat_base_dirs()
    for base_dir in base_dirs:
        if not os.path.isdir(base_dir):
            continue
        try:
            for entry in os.listdir(base_dir):
                db_storage = os.path.join(base_dir, entry, "db_storage")
                if os.path.isdir(db_storage):
                    result.append(db_storage)
        except Exception:
            pass
    return result


def detect_db_path() -> str:
    """兼容旧调用：返回环境变量中保存的路径，或第一个探测到的 session.db（不保证与密钥匹配）。"""
    path = os.environ.get("WECHAT_4X_SESSION_DB_PATH") or os.environ.get("WCDB_SESSION_DB_PATH")
    if path and os.path.exists(path):
        return path

    for db_storage in detect_all_db_storage_dirs():
        for sub in ["Session", "session"]:
            candidate = os.path.join(db_storage, sub, "session.db")
            if os.path.exists(candidate):
                return candidate
        candidate2 = os.path.join(db_storage, "session.db")
        if os.path.exists(candidate2):
            return candidate2
    return ""


def match_db_storage_by_key(hex_key: str) -> str:
    """
    用密钥的 HMAC 校验来匹配真正对应的 db_storage 目录。
    """
    global _verified_key_cache
    if hex_key in _verified_key_cache:
        cached_path = _verified_key_cache[hex_key]
        if not cached_path or os.path.isdir(cached_path):
            return cached_path

    matched_storage = ""
    try:
        from src.wechat_4x.db_match_helper import match_db_storage_by_key as match_helper
        matched_storage = match_helper(hex_key, detect_all_db_storage_dirs()) or ""
    except Exception as e:
        logger.error(f"[WCDB Helpers] match_db_storage_by_key 发生异常: {e}")

    _verified_key_cache[hex_key] = matched_storage
    return matched_storage
