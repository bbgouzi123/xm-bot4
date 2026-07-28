import logging
import os
import threading
import json
from urllib.parse import quote
from datetime import datetime
from typing import List

logger = logging.getLogger(__name__)

class ContactsCacheCloudMixin:
    def load_from_cloud(self, force: bool = False):
        """从同步后端拉取通讯录快照到内存"""
        try:
            from src.utils.cloud_sync import get_cloud_client
            from src.crm.account_data import get_active_account
            cloud = get_cloud_client()
            account_id = get_active_account() or "main"
            
            if not hasattr(self, '_cloud_loaded_flag'):
                self._cloud_loaded_flag = {}
            if not force and self._cloud_loaded_flag.get(account_id):
                return
                
            bot_q = quote(account_id, safe="")
            contacts_resp = cloud._get(f"/api/v1/contacts?bot_wxid={bot_q}", need_auth=True)
            if contacts_resp and isinstance(contacts_resp, list):
                from src.uia.contacts.constants import is_synthetic_placeholder_wxid
                # 按 bot_wxid 进行分组分发，支持用户下多微信通讯录的全局拉取和恢复
                grouped_friends = {}
                grouped_groups = {}
                
                for c in contacts_resp:
                    c_bot_wxid = c.get("bot_wxid")
                    if not c_bot_wxid or c_bot_wxid == "default":
                        target_aid = self._normalize_account_id(account_id)
                    else:
                        target_aid = self._normalize_account_id(c_bot_wxid)
                        
                    ctype = c.get("contact_type", "联系人")
                    tags = c.get("tags")
                    tags_str = tags[0] if isinstance(tags, list) and len(tags) > 0 else ""
                    
                    mapped = {
                        "wxid": c.get("wxid", ""),
                        "name": c.get("name", ""),
                        "remark": c.get("remark", ""),
                        "category": ctype if ctype not in ("friend", "group") else ("联系人" if ctype == "friend" else "群聊"),
                        "tag": tags_str,
                        "avatar_url": c.get("avatar_url", ""),
                        "hello_msg": c.get("hello_msg", ""),
                        "status": c.get("status", ""),
                        "is_takeover": c.get("is_takeover", False)
                    }
                    
                    if ctype == "group" or mapped["category"] == "群聊":
                        if target_aid not in grouped_groups:
                            grouped_groups[target_aid] = []
                        grouped_groups[target_aid].append(mapped)
                    else:
                        if target_aid not in grouped_friends:
                            grouped_friends[target_aid] = []
                        grouped_friends[target_aid].append(mapped)
                
                with self._rw_lock:
                    for aid in set(list(grouped_friends.keys()) + list(grouped_groups.keys())):
                        friends_list = grouped_friends.get(aid, [])
                        groups_list = grouped_groups.get(aid, [])
                        
                        purged_friends = {}
                        name_lookup = {}
                        for f in friends_list:
                            wxid = f.get("wxid", "")
                            name = f.get("name", "")
                            if wxid and not is_synthetic_placeholder_wxid(wxid):
                                purged_friends[wxid] = f
                                if name:
                                    name_lookup[name] = wxid
                                    
                        for f in friends_list:
                            wxid = f.get("wxid", "")
                            name = f.get("name", "")
                            if is_synthetic_placeholder_wxid(wxid):
                                continue
                            if not wxid:
                                if name and name in name_lookup:
                                    continue
                                purged_friends[id(f)] = f
                                
                        self._friends[aid] = list(purged_friends.values())
                        self._groups[aid] = groups_list
                        logger.info(f"[ContactsCache] ☁️ 从同步后端加载账号[{aid}]的 {len(self._friends[aid])} 个好友, {len(self._groups[aid])} 个群聊")

            members = cloud._get(f"/api/v1/group-members?bot_wxid={bot_q}", need_auth=True)
            if members and isinstance(members, list):
                grouped_members = {}
                for m in members:
                    m_bot_wxid = m.get("bot_wxid")
                    if not m_bot_wxid or m_bot_wxid == "default":
                        target_aid = self._normalize_account_id(account_id)
                    else:
                        target_aid = self._normalize_account_id(m_bot_wxid)
                    if target_aid not in grouped_members:
                        grouped_members[target_aid] = []
                    grouped_members[target_aid].append(m)
                with self._rw_lock:
                    for aid, m_list in grouped_members.items():
                        self._group_members[aid] = m_list

            tags = cloud._get(f"/api/v1/contact-tags?bot_wxid={bot_q}", need_auth=True)
            if tags and isinstance(tags, list):
                grouped_tags = {}
                for t in tags:
                    t_bot_wxid = t.get("bot_wxid")
                    if not t_bot_wxid or t_bot_wxid == "default":
                        target_aid = self._normalize_account_id(account_id)
                    else:
                        target_aid = self._normalize_account_id(t_bot_wxid)
                    if target_aid not in grouped_tags:
                        grouped_tags[target_aid] = []
                    grouped_tags[target_aid].append(t)
                with self._rw_lock:
                    for aid, t_list in grouped_tags.items():
                        self._contact_tags[aid] = t_list

            self._cloud_loaded_flag[account_id] = True

        except Exception as e:
            logger.debug(f"[ContactsCache] 同步后端加载跳过: {e}")

        from src.crm.account_data import get_active_account, get_contacts_path
        account_id = get_active_account() or "main"
        if not getattr(self, '_friends', {}).get(account_id) or not getattr(self, '_groups', {}).get(account_id):
            if self._load_local_snapshot(account_id):
                logger.info("[ContactsCache] 💾 已从本地快照恢复通讯录数据")
            else:
                try:
                    # 优先加载当前微信账号下的独立通讯录缓存
                    local_path = get_contacts_path(account_id)
                    is_legacy = False
                    if not os.path.exists(local_path):
                        # 仅在账号为 default 或 main 时，才允许读取老版本根目录下的全局未隔离 contacts.json 作为兜底
                        if account_id in ("default", "main"):
                            local_path = os.path.expanduser("~/.xm-ai-bot/contacts.json")
                            is_legacy = True
                        else:
                            local_path = None
                            
                    if local_path and os.path.exists(local_path):
                        with open(local_path, "r", encoding="utf-8") as f:
                            old_contacts = json.load(f)
                        if isinstance(old_contacts, list) and old_contacts:
                            purged_old = {}
                            name_lookup = {}
                            for f_item in old_contacts:
                                wxid = f_item.get("wxid", "")
                                name = f_item.get("name", "")
                                if wxid:
                                    purged_old[wxid] = f_item
                                    if name: name_lookup[name] = wxid
                            for f_item in old_contacts:
                                wxid = f_item.get("wxid", "")
                                name = f_item.get("name", "")
                                if not wxid:
                                    if name and name in name_lookup:
                                        continue
                                    purged_old[id(f_item)] = f_item

                            final_old_contacts = list(purged_old.values())
                            with self._rw_lock:
                                self._friends[account_id] = final_old_contacts
                            logger.info(f"[ContactsCache] 🔄 成功从本地{'旧版' if is_legacy else '专属'}缓存恢复 {len(final_old_contacts)} 个历史联系人！")
                            if is_legacy:
                                self._async_push("contacts", final_old_contacts)
                except Exception as e:
                    logger.warning(f"[ContactsCache] 恢复本地备用通讯录失败: {e}")

    def _async_push(self, endpoint: str, data: list):
        """异步推送到同步后端"""
        if not data:
            from src.crm.account_data import get_active_account
            self._async_purge_cloud(endpoint, purge_all=True, valid_names=[], bot_wxid=get_active_account())
            return
        def _do():
            try:
                from src.utils.cloud_sync import get_cloud_client
                from src.crm.account_data import get_active_account
                cloud = get_cloud_client()
                bot_wxid = get_active_account() or ""

                from src.uia.contacts.constants import is_synthetic_placeholder_wxid
                
                mapped_data = []
                valid_names = []
                for item in data:
                    wxid = item.get("wxid", "")
                    if is_synthetic_placeholder_wxid(wxid):
                        continue
                        
                    tag_list = [item.get("tag")] if item.get("tag") else []
                    cat = item.get("category", "")
                    db_type = "friend" if cat == "联系人" else ("group" if cat == "群聊" else cat)
                    if not db_type:
                        db_type = "friend" if endpoint == "contacts" else "group"
                    
                    name = item.get("name", "")
                    if name:
                        valid_names.append(name)
                        
                    mapped_data.append({
                        "contact_type": db_type,
                        "wxid": wxid,
                        "name": name,
                        "remark": item.get("remark", ""),
                        "tags": tag_list,
                        "is_new": False,
                        "avatar_url": item.get("avatar_url", ""),
                        "hello_msg": item.get("hello_msg", ""),
                        "status": item.get("status", ""),
                        "region": item.get("region", ""),
                        "signature": item.get("signature", ""),
                        "source": item.get("source", ""),
                        "is_takeover": item.get("is_takeover", False)
                    })
                
                actual_endpoint = "contacts" 
                for i in range(0, len(mapped_data), 200):
                    cloud._post(
                        f"/api/v1/{actual_endpoint}",
                        {"bot_wxid": bot_wxid, actual_endpoint: mapped_data[i:i+200]},
                        need_auth=True,
                    )
                
                if valid_names:
                    purge_type = "non_group" if endpoint == "contacts" else "group"
                    try:
                        cloud._request(
                            "DELETE",
                            "/api/v1/contacts/purge",
                            data={
                                "bot_wxid": bot_wxid,
                                "valid_names": valid_names,
                                "contact_type": purge_type,
                            },
                            need_auth=True,
                        )
                        logger.info(f"[ContactsCache] ☁️ 同步后端 purge 成功: 保留 {len(valid_names)} 个 {purge_type}")
                    except Exception as e:
                        logger.debug(f"[ContactsCache] 同步后端 purge 失败（非致命）: {e}")
            except Exception as e:
                logger.debug(f"[ContactsCache] 推送 {endpoint} 失败: {e}")
        threading.Thread(target=_do, daemon=True, name=f"contacts-push-{endpoint}").start()

    def _async_purge_cloud(self, endpoint: str, purge_all: bool = False, valid_names: List[str] = None, bot_wxid: str = None):
        """异步调用同步后端 purge 接口并清理本地 Checkpoint"""
        def _do_purge():
            try:
                from src.crm.account_data import get_active_account
                target_wxid = bot_wxid or get_active_account() or ""
                if not target_wxid:
                    return

                try:
                    from src.utils.contact_sync_checkpoint import ContactSyncCheckpointStore
                    store = ContactSyncCheckpointStore()
                    count = store.clear_by_prefix(f"{target_wxid}::")
                    if count > 0:
                        logger.info(f"[ContactsCache] [purge] 已清理 {count} 条本地续跑记录 (bot_wxid={target_wxid})")
                except Exception as ce:
                    logger.debug(f"[ContactsCache] [purge] 清理本地续跑记录异常: {ce}")

                from src.utils.cloud_sync import get_cloud_client
                cloud = get_cloud_client()
                purge_type = "non_group" if endpoint == "contacts" else "group"
                
                payload = {
                    "bot_wxid": target_wxid,
                    "valid_names": valid_names or [],
                    "contact_type": purge_type,
                    "purge_all": purge_all,
                }
                
                result = cloud._request(
                    "DELETE",
                    "/api/v1/contacts/purge",
                    data=payload,
                    need_auth=True,
                )
                if result:
                    purged = result.get("purged", 0) if isinstance(result, dict) else 0
                    logger.info(f"[ContactsCache] ☁️ 同步后端 purge 完成: 清除 {purged} 条 (purge_all={purge_all})")
            except Exception as e:
                logger.debug(f"[ContactsCache] 同步后端 purge 失败: {e}")
        threading.Thread(target=_do_purge, daemon=True, name=f"contacts-purge-{endpoint}").start()
