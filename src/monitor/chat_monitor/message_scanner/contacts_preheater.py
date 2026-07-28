import os
import json
import logging

logger = logging.getLogger(__name__)

def preheat_contacts_cache(account_id: str):
    """
    优先从本地 JSON 同步加载以加快冷启动通讯录预热，隔离多账号内存
    """
    try:
        from src.crm.account_data import get_contacts_path
        from src.utils.contacts_cache import contacts_cache
        contacts_path = get_contacts_path(account_id)
        if contacts_path and os.path.exists(contacts_path):
            with open(contacts_path, "r", encoding="utf-8") as f_contacts:
                data = json.load(f_contacts)
            if isinstance(data, list) and data:
                # 检查缓存数据是否归属于当前微信号，防止由于以前的错绑把别人的数据当成本号缓存加载出来
                has_self_wxid = any(item.get("wxid") == account_id for item in data)
                if not has_self_wxid:
                    print(f"[监控] 启动检查：本地缓存归属不匹配当前账号({account_id})，跳过预加载并清空脏内存缓存，等待数据库同步...")
                    with contacts_cache._rw_lock:
                        contacts_cache._friends[account_id] = []
                        contacts_cache._groups[account_id] = []
                else:
                    mapped_friends = []
                    mapped_groups = []
                    for item in data:
                        cat = item.get("category", "")
                        if cat == "群聊" or item.get("contact_type") == "group":
                            mapped_groups.append(item)
                        else:
                            mapped_friends.append(item)
                    with contacts_cache._rw_lock:
                        contacts_cache._friends[account_id] = mapped_friends
                        contacts_cache._groups[account_id] = mapped_groups
                    print(f"[监控] 启动检查：从账号隔离缓存加载了 {len(mapped_friends)} 个好友, {len(mapped_groups)} 个群聊")
                    try:
                        from src.uia.session import session_type_cache
                        session_type_cache.revalidate_with_contacts(mapped_friends, mapped_groups)
                    except Exception as rev_err:
                        print(f"[监控] 启动检查订正缓存异常 (忽略): {rev_err}")
    except Exception as e:
        print(f"[监控] 启动检查加载本地通讯录缓存异常 (忽略): {e}")
