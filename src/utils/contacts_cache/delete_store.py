import os
import json
import logging
from typing import List

logger = logging.getLogger(__name__)

def record_deleted_contacts(account_id: str, wxids: List[str] = None, name_categories: List[dict] = None):
    """在被删列表中记录这些微信号和无微信号的条目，以便数据库同步时进行过滤"""
    from src.crm.account_data import get_account_data_dir
    
    dir_path = get_account_data_dir(account_id)
    deleted_file = os.path.join(dir_path, "deleted_contacts.json")
    
    existing = {"wxids": [], "items": []}
    if os.path.exists(deleted_file):
        try:
            with open(deleted_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if not isinstance(existing, dict):
                existing = {"wxids": [], "items": []}
            if "wxids" not in existing:
                existing["wxids"] = []
            if "items" not in existing:
                existing["items"] = []
        except Exception:
            pass
            
    if wxids:
        for w in wxids:
            w_str = str(w).strip()
            if w_str and w_str not in existing["wxids"]:
                existing["wxids"].append(w_str)
                
    if name_categories:
        for item in name_categories:
            if isinstance(item, dict):
                name = (item.get("name") or "").strip()
                category = (item.get("category") or "联系人").strip() or "联系人"
                if name:
                    if not any(x.get("name") == name and x.get("category") == category for x in existing["items"]):
                        existing["items"].append({"name": name, "category": category})
                        
    try:
        with open(deleted_file, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[ContactsCache] 保存 deleted_contacts.json 失败: {e}")

def remove_from_deleted_contacts(account_id: str, wxid: str = None, name: str = None, category: str = None):
    """当联系人重新写入或显式同步时，将其从已删除黑名单中撤销"""
    from src.crm.account_data import get_account_data_dir
    
    dir_path = get_account_data_dir(account_id)
    deleted_file = os.path.join(dir_path, "deleted_contacts.json")
    if not os.path.exists(deleted_file):
        return
        
    try:
        with open(deleted_file, "r", encoding="utf-8") as f:
            deleted = json.load(f)
        if not isinstance(deleted, dict):
            return
    except Exception:
        return
        
    changed = False
    wxids_list = deleted.get("wxids", [])
    if wxid and wxid in wxids_list:
        try:
            wxids_list.remove(wxid)
            deleted["wxids"] = wxids_list
            changed = True
        except ValueError:
            pass
        
    items_list = deleted.get("items", [])
    if name:
        cat = category or "联系人"
        new_items = []
        for item in items_list:
            if item.get("name") == name and item.get("category") == cat:
                changed = True
            else:
                new_items.append(item)
        deleted["items"] = new_items
        
    if changed:
        try:
            with open(deleted_file, "w", encoding="utf-8") as f:
                json.dump(deleted, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[ContactsCache] 更新 deleted_contacts.json 失败: {e}")

def clear_deleted_contacts(account_id: str):
    """清空已删除联系人黑名单，例如在手动点击同步好友时"""
    from src.crm.account_data import get_account_data_dir
    dir_path = get_account_data_dir(account_id)
    deleted_file = os.path.join(dir_path, "deleted_contacts.json")
    if os.path.exists(deleted_file):
        try:
            os.remove(deleted_file)
            logger.info(f"[ContactsCache] 已清空账号 {account_id} 的删除联系人黑名单")
        except Exception as e:
            logger.warning(f"[ContactsCache] 清空 deleted_contacts.json 失败: {e}")

def filter_deleted_contacts(account_id: str, items_list: List[dict], is_group: bool = False) -> List[dict]:
    """从列表中过滤掉在 deleted_contacts.json 里的已被删除联系人"""
    from src.crm.account_data import get_account_data_dir
    
    dir_path = get_account_data_dir(account_id)
    deleted_file = os.path.join(dir_path, "deleted_contacts.json")
    if not os.path.exists(deleted_file):
        return items_list
        
    try:
        with open(deleted_file, "r", encoding="utf-8") as f:
            deleted = json.load(f)
    except Exception:
        return items_list
        
    if not isinstance(deleted, dict):
        return items_list
        
    wxid_targets = {str(x).strip() for x in deleted.get("wxids", []) if str(x).strip()}
    nc_targets = set()
    for item in deleted.get("items", []):
        if isinstance(item, dict):
            name = (item.get("name") or "").strip()
            category = (item.get("category") or ("群聊" if is_group else "联系人")).strip()
            if name:
                nc_targets.add((name, category))
                
    if not wxid_targets and not nc_targets:
        return items_list
        
    result = []
    for item in items_list:
        wxid = (item.get("wxid") or "").strip()
        if wxid and wxid in wxid_targets:
            continue
            
        name = (item.get("name") or "").strip()
        display_name = (item.get("display_name") or "").strip()
        remark = (item.get("remark") or "").strip()
        category = (item.get("category") or ("群聊" if is_group else "联系人")).strip()
        
        should_skip = False
        for t_name, t_cat in nc_targets:
            if name == t_name or display_name == t_name or remark == t_name:
                if category == t_cat or t_cat in ("联系人", "群聊", ""):
                    should_skip = True
                    break
        if should_skip:
            continue
            
        result.append(item)
    return result
