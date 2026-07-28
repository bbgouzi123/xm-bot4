import os
import json
import logging
from typing import Set, Dict
from src.utils.wechat_key_store import KEYS_FILE_PATH, clean_wxid

logger = logging.getLogger("WeChatOfflineDetector")

def get_known_wxids() -> Set[str]:
    """探测系统中所有已知(登录过/有密钥)的微信号集合"""
    known_wxids = set()
    # 1. 密钥库中的 wxid
    if os.path.exists(KEYS_FILE_PATH):
        try:
            with open(KEYS_FILE_PATH, "r", encoding="utf-8") as f:
                key_data = json.load(f)
                known_wxids = {clean_wxid(k) for k in key_data.keys() if k != "last_key"}
        except Exception:
            pass

    # 2. 扫描本地微信数据根目录补充已知微信号
    try:
        from src.wechat_4x.db_match_helper import get_wechat_base_dirs
        base_dirs = get_wechat_base_dirs()
        for base_dir in base_dirs:
            if os.path.isdir(base_dir):
                for entry in os.listdir(base_dir):
                    if entry.lower() in {"all users", "all_users", "backup", "finderlive", "common", "global", "temp", "cache"}:
                        continue
                    db_storage = os.path.join(base_dir, entry, "db_storage")
                    if os.path.isdir(db_storage):
                        wx_c = clean_wxid(entry)
                        if wx_c:
                            known_wxids.add(wx_c)
    except Exception:
        pass
    return known_wxids

def get_offline_instances_dict() -> Dict[str, dict]:
    """获取所有历史离线微信实例的摘要字典"""
    offline_instances = {}
    try:
        key_data = {}
        if os.path.exists(KEYS_FILE_PATH):
            with open(KEYS_FILE_PATH, "r", encoding="utf-8") as f:
                key_data = json.load(f)
    except Exception:
        key_data = {}

    historical_wxids = get_known_wxids()

    from src.crm.account_data import _load_account_meta, make_avatar_url, ACCOUNTS_DIR
    for wxid in sorted(historical_wxids):
        nickname = wxid
        try:
            meta = _load_account_meta(wxid)
            if meta and meta.get("nickname"):
                nickname = meta["nickname"]
        except Exception:
            pass

        # 头像路径探测
        avatar_url = ""
        avatar_path = os.path.join(ACCOUNTS_DIR, f"{wxid}.png")
        if os.path.exists(avatar_path):
            avatar_url = make_avatar_url(wxid)

        offline_instances[wxid] = {
            'window_handle': 0,
            'nickname': nickname,
            'active': False,
            'avatar': avatar_url,
            'status': 'offline',
            'wxid': wxid,
            'has_key': bool(key_data.get(wxid))
        }
    return offline_instances
