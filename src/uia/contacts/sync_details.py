import json
import os
import time
import urllib.request
from typing import Any, Callable, Dict, Optional

from ..retry import (
    capture_avatar_via_clipboard,
    human_scroll,
    is_escape_pressed,
    is_shift_pressed,
    random_delay,
    smooth_click_at,
)
from .constants import (
    clear_contact_sync_pause,
    is_contact_sync_pause_requested,
    is_denied_contact_row_name,
    match_friend_for_detail_row,
)


class ContactSyncDetailsMixin:
    def sync_details(
        self,
        target_category: Optional[str] = None,
        callback: Optional[Callable] = None,
        already_locked: bool = False,
        force_resync: bool = False,
        single_contact_name: Optional[str] = None,
    ) -> dict:
        if not already_locked:
            task_title = f"头像同步#{single_contact_name or target_category or '全部'}"
            return self._run_contact_task(
                task_title,
                lambda: self.sync_details(
                    target_category=target_category,
                    callback=callback,
                    already_locked=True,
                    force_resync=force_resync,
                    single_contact_name=single_contact_name,
                ),
            )

        from src.utils.contacts_cache import contacts_cache

        result = {"success": True, "total": 0, "current": 0, "errors": []}
        processed_names = set()
        detail_round = 0
        account_id = "default_user"
        checkpoint_category = f"details::{target_category or '__all__'}"
        user_paused = False
        clear_contact_sync_pause()

        try:
            user_info = self.driver.get_current_user()
            account_id = user_info.get("wxid") or user_info.get("nickname") or "default_user"
            all_friends = contacts_cache.get_friends(account_id) or []

            # [核心修复] 内存无联系人时，自动先从微信抓取联系人列表，再继续同步详情
            if not all_friends:
                print("[详情同步] ⚠ 内存中无联系人数据，自动先执行通讯录同步...")
                if callback:
                    callback("auto_sync_contacts", {"message": "内存中无联系人，正在自动从微信同步通讯录..."})
                try:
                    sync_result = self.sync_all(
                        target_category=target_category,
                        already_locked=True,
                    )
                    synced_count = sync_result.get("total", 0)
                    print(f"[详情同步] V 自动通讯录同步完成: {synced_count} 个联系人")
                    # 重新获取联系人列表
                    all_friends = contacts_cache.get_friends(account_id) or []
                    if callback:
                        callback("auto_sync_contacts_completed", sync_result)
                except Exception as e:
                    print(f"[详情同步] ✗ 自动通讯录同步失败: {e}")
                    result["errors"].append(f"自动通讯录同步失败: {e}")

            if target_category:
                all_friends = [f for f in all_friends if f.get("category") == target_category]

            # 单联系人同步模式：跳过 checkpoint，强制同步该联系人
            if single_contact_name:
                missing_avatars = [f for f in all_friends if (f.get("name") or "").strip() == single_contact_name]
                if not missing_avatars:
                    # 按 wxid 再找一次
                    missing_avatars = [f for f in all_friends if (f.get("wxid") or "").strip() == single_contact_name]
                processed_names = set()
                result["current"] = 0
                print(f"[详情同步] 单联系人模式: {single_contact_name!r}, matched={len(missing_avatars)}")
            else:
                missing_avatars = list(all_friends) if force_resync else [f for f in all_friends if not f.get("avatar_url")]

                checkpoint = self._checkpoint_store.load(account_id, checkpoint_category)
                if not force_resync and checkpoint and checkpoint.get("state") in ("RUNNING", "FAILED"):
                    try:
                        processed_names = set(checkpoint.get("seen_names") or [])
                        result["current"] = int(checkpoint.get("new", 0))
                        if callback:
                            callback("resumed", {"current": result["current"], "processed": len(processed_names)})
                    except Exception:
                        processed_names = set()
                        result["current"] = 0
                elif force_resync:
                    processed_names = set()
                    result["current"] = 0

                if processed_names:
                    missing_avatars = [f for f in missing_avatars if (f.get("name") or "") not in processed_names]

            total_missing = len(missing_avatars) + len(processed_names)
            result["total"] = total_missing
            if total_missing == 0:
                if not single_contact_name:
                    self._checkpoint_store.clear(account_id, checkpoint_category)
                if callback:
                    callback("completed", result)
                return result

            self._checkpoint_store.save_running(
                account_id=account_id,
                category=checkpoint_category,
                state="RUNNING",
                scroll_round=detail_round,
                seen_names=processed_names,
                total=result["total"],
                new_count=result["current"],
            )

            def _fail_detail_checkpoint(err: str):
                result["success"] = False
                if err not in result["errors"]:
                    result["errors"].append(err)
                try:
                    self._checkpoint_store.save_failed(
                        account_id=account_id,
                        category=checkpoint_category,
                        scroll_round=detail_round,
                        seen_names=processed_names,
                        total=result["total"],
                        new_count=result["current"],
                        error=err,
                    )
                except Exception:
                    pass

            # ── [核心重构] 使用 V2 逻辑 (通讯录管理) 执行详情同步 ──
            print(f"[详情同步] 🔄 切换到 V2 模式同步详情 (target={target_category or '全部'}, single={single_contact_name or '无'})")
            return self.sync_all(
                target_category=target_category,
                callback=callback,
                already_locked=True,
                sync_details=True,
                force_resync=force_resync,
                single_contact_name=single_contact_name,
            )
        except Exception as e:
            print(f"[详情同步] ✗ 详情同步失败: {e}")
            result["success"] = False
            result["errors"].append(str(e))
            if callback:
                callback("completed", result)
            return result
