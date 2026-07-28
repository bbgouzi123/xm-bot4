import time
import re
from typing import Callable, Optional
from ...retry import (
    human_scroll,
    is_shift_pressed,
)
from src.utils.stop_signal import stop_signal
from ..constants import (
    clear_contact_sync_pause,
    is_contact_sync_pause_requested,
    is_denied_contact_row_name,
)

class ContactSyncLegacyMixin:
    """旧版同步逻辑（基于侧边栏分组展开）"""

    def _sync_all_legacy(
        self,
        target_category: Optional[str] = None,
        callback: Optional[Callable] = None,
        already_locked: bool = True,
    ) -> dict:
        """旧版同步逻辑（基于侧边栏分组展开），仅在通讯录管理窗口无法打开时使用。"""
        result = {"success": True, "total": 0, "new": 0, "errors": []}
        scroll_rounds = 0
        seen_names = set()
        account_id = "default_user"
        checkpoint_category = target_category or "__all__"
        user_paused = False
        clear_contact_sync_pause()
        name_count: dict = {}

        try:
            if stop_signal.is_stopped:
                return {"success": False, "total": 0, "new": 0, "errors": ["用户按下 ESC 键中断了操作"]}

            contacts_list, prep_err = self._prepare_contacts_list_session(target_category)
            if prep_err:
                return {"success": False, "total": 0, "new": 0, "errors": [prep_err]}

            existing = self._load_contacts()
            existing_names = {c.get("name", "") for c in existing}
            contacts = []
            max_scroll = 500

            user_info = self.driver.get_current_user()
            account_id = user_info.get("wxid") or user_info.get("nickname") or "default_user"

            current_category = "联系人"
            current_category_goal = 0
            current_category_count = 0
            current_index = ""

            while scroll_rounds <= max_scroll:
                if is_shift_pressed() or is_contact_sync_pause_requested() or stop_signal.is_stopped:
                    user_paused = True
                    break
                contacts_list = self._ensure_contacts_context(contacts_list, reason=f"扫描第{scroll_rounds + 1}轮")
                if not contacts_list:
                    result["errors"].append("通讯录页面被打断，自动回正失败")
                    break

                items = contacts_list.GetChildren()
                if not items:
                    break

                new_in_round = False
                frame_raw_seen = {}
                for item in items:
                    if is_shift_pressed() or is_contact_sync_pause_requested() or stop_signal.is_stopped:
                        user_paused = True
                        break
                    raw_name = (item.Name or "").strip()
                    if not raw_name:
                        continue

                    count_in_frame = frame_raw_seen.get(raw_name, 0)
                    frame_raw_seen[raw_name] = count_in_frame + 1

                    if is_denied_contact_row_name(raw_name):
                        seen_names.add(raw_name)
                        continue
                    if (len(raw_name) == 1 and raw_name.isalpha()) or raw_name == "星标朋友":
                        current_index = raw_name
                        seen_names.add(raw_name)
                        continue

                    # 跳过系统行
                    sys_prefixes = ("新的朋友", "公众号", "企业微信联系人", "服务号", "订阅号", "视频号", "群聊", "我的企业", "标签", "星标朋友", "联系人")
                    is_sys = False
                    for pre in sys_prefixes:
                        if raw_name.startswith(pre):
                            suffix = raw_name[len(pre):].strip()
                            if not suffix or re.match(r"^\(?\d+\)?$", suffix):
                                is_sys = True
                                current_category = pre
                            break
                    if is_sys:
                        seen_names.add(raw_name)
                        continue

                    _DUP_SUFFIXES = ["", "2", "3", "4", "5", "6", "7", "8", "9", "10"]
                    if count_in_frame == 0:
                        cur_count = name_count.get(raw_name, 0)
                        if cur_count == 0:
                            storage_name = raw_name
                        else:
                            continue
                    else:
                        cur_count = name_count.get(raw_name, 0)
                        suffix_idx = min(cur_count, len(_DUP_SUFFIXES) - 1)
                        storage_name = raw_name + _DUP_SUFFIXES[suffix_idx]

                    if storage_name in seen_names:
                        continue

                    name_count[raw_name] = name_count.get(raw_name, 0) + 1
                    seen_names.add(storage_name)
                    new_in_round = True

                    contact = {
                        "name": storage_name,
                        "display_name": raw_name,
                        "category": current_category,
                        "index": current_index or "#",
                        "syncTime": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    }

                    contacts.append(contact)
                    result["total"] += 1
                    if storage_name not in existing_names:
                        result["new"] += 1
                    if callback:
                        callback("contact_added", {"contact": contact, "total": result["total"], "new": result["new"]})

                scroll_rounds += 1
                if not new_in_round:
                    break
                if user_paused:
                    break
                try:
                    human_scroll(contacts_list, min_times=3, max_times=6, min_delay=0.6, max_delay=1.8)
                except Exception:
                    break

            if contacts:
                self._save_contacts(contacts)
                try:
                    from src.utils.contacts_cache import contacts_cache
                    mem_friends = contacts_cache.get_friends(account_id)
                    if mem_friends:
                        contacts_cache.set_friends(account_id, mem_friends, sync_cloud=True)
                        print(f"[联系人同步] [同步后端] 已触发同步后端推送: {len(mem_friends)} 条联系人数据")
                except Exception as _e:
                    print(f"[联系人同步] [!] 同步后端推送失败: {_e}")
            else:
                result["errors"].append("未读取到任何联系人")

            if not result["errors"] and contacts:
                # 同步完成后切回“通讯录”页（方便监听消息）
                self.driver._ensure_contacts_page(force=True)

            if callback:
                callback("completed", result)
            if user_paused:
                clear_contact_sync_pause()
                result["paused"] = True
            else:
                self._checkpoint_store.clear(account_id, checkpoint_category)
        except Exception as e:
            result["success"] = False
            result["errors"].append(str(e))
        return result
