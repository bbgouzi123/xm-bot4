import json
import threading
import urllib.request


class ContactStorageMixin:
    def _load_contacts(self) -> list:
        try:
            if self.CONTACTS_FILE.exists():
                data = json.loads(self.CONTACTS_FILE.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
        except Exception:
            pass
        return []

    def _save_contacts(self, contacts: list, is_incremental: bool = False):
        try:
            self.CONTACTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            existing = self._load_contacts()
            existing_map = {c.get("name", ""): c for c in existing if c.get("name")}
            scanned_names = set()
            
            # [防灾机制] 引入防误删/异常清空的保护阈值
            # 如果不是增量更新，但本次扫描到的联系人数量严重缩水（如之前已有大量联系人，当前突变至少于 50% 且不足原有的一半）
            # 这往往是因为微信未完全就绪、UI被遮挡、页面滚动出错等异常，应触发保护，拒绝丢弃已有的本地缓存
            prev_total = len(existing_map)
            current_total = len(contacts)
            is_anomaly = False
            if not is_incremental and prev_total > 50 and current_total < max(10, prev_total * 0.5):
                is_anomaly = True
                print(f"[联系人同步] 检测到扫描结果异常缩水 (历史: {prev_total}, 本次: {current_total})，启动防灾保护，保留本地缓存，并跳过云端 purge")

            # 如果是增量更新或检测到异常缩水，必须保留已有的所有联系人
            if is_incremental or is_anomaly:
                merged_map = dict(existing_map)
            else:
                merged_map = {}

            for contact in contacts:
                name = contact.get("name", "")
                if not name:
                    continue
                scanned_names.add(name)
                if name in merged_map:
                    old = merged_map[name]
                    for k, v in contact.items():
                        if v is not None:
                            old[k] = v
                    merged_map[name] = old
                else:
                    # 如果不是增量且不是异常，且 name 在 existing_map 中，保留其已有详情，防止覆盖
                    if not is_incremental and not is_anomaly and name in existing_map:
                        old = existing_map[name]
                        for k, v in contact.items():
                            if v is not None:
                                old[k] = v
                        merged_map[name] = old
                    else:
                        merged_map[name] = contact

            merged = list(merged_map.values())
            self.CONTACTS_FILE.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                from src.utils.db_manager import WeChatDBManager

                db = WeChatDBManager("data/wechat_data.db")
                try:
                    db.init_db()
                except Exception:
                    pass
                user_info = self.driver.get_current_user()
                account_id = user_info.get("wxid") or user_info.get("nickname") or "default_user"
                friends = []
                groups = []
                for c in merged:
                    cat = c.get("category", "联系人")
                    name = c.get("name", "")
                    wxid = (c.get("wxid") or "").strip()
                    
                    # [核心修复] 严禁仅靠名字结尾带“群”就判定为群（防止误伤如“吳克群”等用户）
                    # 判定准则：1. 明确属于群聊分类 2. wxid 以 @chatroom 结尾
                    is_group = False
                    if cat in ("群聊", "微信群聊"):
                        is_group = True
                    elif wxid.endswith("@chatroom"):
                        is_group = True
                    # 如果已经是联系人分类，且名字带群字，但不是真正的群 ID，则依然判定为好友
                    elif cat == "联系人" and name.endswith("群") and not wxid.endswith("@chatroom"):
                        is_group = False
                        
                    if is_group:
                        c["contact_type"] = "group"
                        groups.append(c)
                    else:
                        c["contact_type"] = "friend"
                        friends.append(c)
                if friends:
                    db.save_friends(account_id, friends, True)
                if groups:
                    db.save_groups(account_id, groups)
                
                # [核心修复] 增量更新或异常缩水时，绝对不要在此处触发云端 purge。
                if not is_incremental:
                    try:
                        from src.utils.contacts_cache import contacts_cache

                        if friends:
                            contacts_cache.set_friends(account_id, friends, sync_cloud=not is_anomaly)
                        if groups:
                            contacts_cache.set_groups(account_id, groups, sync_cloud=not is_anomaly)
                    except Exception:
                        pass
                    if scanned_names and not is_anomaly:
                        self._async_purge_cloud_contacts(account_id, list(scanned_names))
            except Exception:
                pass
        except Exception:
            pass

    def _async_purge_cloud_contacts(self, account_id: str, valid_names: list):
        def _do_purge():
            try:
                from src.utils.cloud_sync import get_cloud_client

                cloud = get_cloud_client()
                req_url = f"{cloud.cloud_url.rstrip('/')}/api/v1/contacts/purge"
                req_data = json.dumps({"bot_wxid": account_id, "valid_names": valid_names}).encode("utf-8")
                req_obj = urllib.request.Request(
                    req_url,
                    data=req_data,
                    headers={
                        "Authorization": f"Bearer {cloud.jwt_token}",
                        "Content-Type": "application/json",
                    },
                    method="DELETE",
                )
                with urllib.request.urlopen(req_obj, timeout=10):
                    pass
            except Exception:
                pass

        threading.Thread(target=_do_purge, daemon=True, name="contacts-purge").start()

    def _load_tags(self) -> dict:
        try:
            if self.CONTACTS_TAGS_FILE.exists():
                return json.loads(self.CONTACTS_TAGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _save_tags(self, tags: dict):
        try:
            self.CONTACTS_TAGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            self.CONTACTS_TAGS_FILE.write_text(json.dumps(tags, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def get_cached_contacts(self) -> list:
        return self._load_contacts()

    def get_contact_count(self) -> int:
        return len(self._load_contacts())
