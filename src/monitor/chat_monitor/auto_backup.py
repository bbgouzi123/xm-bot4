import os
import json
import time
import datetime
import logging
from typing import List, Dict, Any

logger = logging.getLogger("AutoBackup")

BACKUP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../data/backups"))

# 记录上一次自动备份的时间： { account_id: date_str }
_last_auto_backup_date: Dict[str, str] = {}

def trigger_daily_auto_backup(account_id: str):
    """
    每天为当前活动微信号自动生成联系人与标签画像的备份文件。
    在 message_scanner 的定时轮询中被调用。
    """
    global _last_auto_backup_date
    if not account_id:
        return
        
    today = datetime.date.today().isoformat()
    
    # 如果今天已经备份过该账号，直接跳过
    if _last_auto_backup_date.get(account_id) == today:
        return
        
    try:
        from src.utils.contacts_cache import contacts_cache
        friends = contacts_cache.get_friends(account_id)
        if not friends:
            return
            
        # 1. 过滤系统联系人
        sys_prefixes = ("新的朋友", "公众号", "企业微信联系人", "群聊", "标签", "服务号", "我的企业", "联系人", "文件传输助手")
        backup_list = []
        for f in friends:
            name = f.get("name", "").strip()
            if not name:
                continue
            is_sys = False
            if len(name) == 1 and name.isascii() and name.isalpha():
                is_sys = True
            for pre in sys_prefixes:
                if name.startswith(pre) or name == pre:
                    is_sys = True
                    break
            if is_sys:
                continue
                
            tags_val = f.get("tags") or []
            if isinstance(tags_val, list):
                tag_str = ",".join(tags_val)
            else:
                tag_str = str(tags_val)

            wxid = f.get("wxid", "")
            item_data = {
                "wxid": wxid,
                "alias": f.get("alias", ""),
                "name": name,
                "nickname": f.get("nickname", ""),
                "remark": f.get("remark", ""),
                "tag": tag_str,
                "region": f.get("region", ""),
                "source": f.get("source", ""),
                "signature": f.get("signature", "")
            }
            
            from src.api.friend_backup_api import get_avatar_base64
            av_b64 = get_avatar_base64(wxid)
            if av_b64:
                item_data["avatar_base64"] = av_b64
                
            backup_list.append(item_data)
            
        if not backup_list:
            return
            
        # 2. 写入文件
        if not os.path.exists(BACKUP_DIR):
            os.makedirs(BACKUP_DIR, exist_ok=True)
            
        filename = f"auto_backup_{account_id}_{today}.json"
        filepath = os.path.join(BACKUP_DIR, filename)
        
        backup_data = {
            "bot_wxid": account_id,
            "created_at": datetime.datetime.now().isoformat(),
            "type": "auto_daily",
            "total": len(backup_list),
            "data": backup_list
        }
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(backup_data, f, ensure_ascii=False, indent=2)
            
        _last_auto_backup_date[account_id] = today
        logger.info(f"[自动备份] 成功为账号 '{account_id}' 自动生成每日防封灾备: {filename} (共 {len(backup_list)} 个联系人)")
        
    except Exception as e:
        logger.error(f"[自动备份] 账号 '{account_id}' 每日自动备份失败: {e}")
