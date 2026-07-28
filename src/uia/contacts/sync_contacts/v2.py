import time
import random
from typing import Callable, Optional
import uiautomation as uia
from ...retry import (
    human_scroll,
    is_shift_pressed,
    physical_click,
    random_delay,
    smooth_click_at,
)
from src.utils.stop_signal import stop_signal
from ..constants import (
    clear_contact_sync_pause,
    is_contact_sync_pause_requested,
)
from .v2_details import ContactV2DetailExtractorMixin
from .v2_scan_loop import ContactSyncV2ScannerMixin
from .v2_groups import ContactSyncV2GroupsMixin


class ContactSyncV2Mixin(ContactV2DetailExtractorMixin, ContactSyncV2ScannerMixin, ContactSyncV2GroupsMixin):
    """联系人同步 V2 — 基于「通讯录管理」窗口的扁平列表方案"""

    def sync_all(
        self,
        target_category: Optional[str] = None,
        callback: Optional[Callable] = None,
        already_locked: bool = False,
        sync_details: bool = False,
        force_resync: bool = False,
        single_contact_name: Optional[str] = None,
    ) -> dict:
        if not already_locked:
            task_title = f"通讯录同步#{single_contact_name or target_category or '全部'}" + ("(含详情)" if sync_details else "")
            return self._run_contact_task(
                task_title,
                lambda: self.sync_all(
                    target_category=target_category,
                    callback=callback,
                    already_locked=True,
                    sync_details=sync_details,
                    force_resync=force_resync,
                    single_contact_name=single_contact_name,
                ),
            )

        result = {"success": True, "total": 0, "new": 0, "errors": []}
        sync_state = "INIT"
        scroll_rounds = 0
        seen_names = set()
        account_id = "default_user"
        checkpoint_category = target_category or "__all__"
        user_paused = False
        clear_contact_sync_pause()

        # ── 同名联系人支持：帧内重名检测 ──
        name_count: dict = {}

        try:
            user_info = self.driver.get_current_user()
            account_id = user_info.get("wxid") or user_info.get("nickname") or "default_user"

            # ═══════════════════════════════════════════════════════════
            # V2 核心：通过「通讯录管理」窗口同步，彻底规避分组展开问题
            # ═══════════════════════════════════════════════════════════
            mgr_win, detail_list, open_err = self._open_contacts_manager()
            if stop_signal.is_stopped:
                print("[联系人同步] 检测到用户按下 ESC 终止信号，中止同步")
                return {"success": False, "total": 0, "new": 0, "errors": ["用户按下 ESC 键中断了操作"]}

            if open_err:
                # 回退到旧方案（保底）
                print(f"[联系人同步] 通讯录管理窗口打开失败: {open_err}，回退到旧方案")
                return self._sync_all_legacy(
                    target_category=target_category,
                    callback=callback,
                    already_locked=True,
                )

            print(f"[联系人同步] [V2] 通讯录管理窗口已打开，开始扫描扁平列表...")

            existing = self._load_contacts()
            existing_names = {c.get("name", "") for c in existing}
            name_to_contact = {c.get("name", ""): c for c in existing if c.get("name")}
            contacts = []
            max_scroll = 500

            # ── 单联系人模式：在管理器中搜索 ──
            if single_contact_name:
                print(f"[联系人同步] [V2] 正在搜索单联系人: {single_contact_name!r}")
                try:
                    search_box = mgr_win.EditControl(Name="搜索")
                    if search_box and search_box.Exists(2):
                        smooth_click_at(search_box)
                        search_box.SendKeys("{Ctrl}a{Delete}")
                        search_box.SendKeys(single_contact_name)
                        random_delay(1.0, 1.5)
                except Exception as se:
                    print(f"[联系人同步] [V2] 搜索框操作异常: {se}")

            checkpoint = self._checkpoint_store.load(account_id, checkpoint_category)
            if checkpoint and checkpoint.get("state") in ("RUNNING", "FAILED"):
                try:
                    seen_names = set(checkpoint.get("seen_names") or [])
                    scroll_rounds = max(0, int(checkpoint.get("scroll_round", 0)))
                    result["total"] = max(result["total"], int(checkpoint.get("total", 0)))
                    result["new"] = max(result["new"], int(checkpoint.get("new", 0)))
                    sync_state = "RESUMED"
                    if callback:
                        callback("resumed", {
                            "scroll_round": scroll_rounds,
                            "seen_count": len(seen_names),
                            "total": result["total"],
                            "new": result["new"],
                        })
                except Exception:
                    seen_names = set()
                    scroll_rounds = 0

            sync_state = "SCANNING"
            self._checkpoint_store.save_running(
                account_id=account_id,
                category=checkpoint_category,
                state=sync_state,
                scroll_round=scroll_rounds,
                seen_names=seen_names,
                total=result["total"],
                new_count=result["new"],
            )
            # 等待列表内容渲染加载完成，防止因异步渲染而误判为空列表直接退出
            has_items = False
            for _ in range(15):
                if stop_signal.is_stopped:
                    break
                items = detail_list.GetChildren()
                if items:
                    has_items = True
                    break
                time.sleep(0.3)

            if target_category == "群聊":
                result = self._sync_groups(
                    mgr_win=mgr_win,
                    account_id=account_id,
                    callback=callback,
                )
                scroll_rounds = 1
            else:
                # ── 扁平列表扫描循环（已抽取至 v2_scan_loop.py） ──
                scroll_rounds, user_paused = self._scan_contacts_manager_loop(
                    mgr_win=mgr_win,
                    detail_list=detail_list,
                    max_scroll=max_scroll,
                    single_contact_name=single_contact_name,
                    checkpoint_category=checkpoint_category,
                    existing_names=existing_names,
                    name_to_contact=name_to_contact,
                    sync_details=sync_details,
                    force_resync=force_resync,
                    callback=callback,
                    account_id=account_id,
                    seen_names=seen_names,
                    scroll_rounds=scroll_rounds,
                    name_count=name_count,
                    contacts=contacts,
                    result=result,
                )

            # ── 扫描完成 ──
            print(f"[联系人同步] [V2] 扫描完成: total={result['total']}, new={result['new']}, rounds={scroll_rounds}")

            # 关闭通讯录管理窗口
            if mgr_win: self._close_contacts_manager(mgr_win)

            if target_category != "群聊":
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
                    if not result.get("errors"): result["errors"].append("未读取到任何联系人")

            if not result["errors"] and target_category != "群聊":
                self.driver._ensure_contacts_page(force=True)

        except Exception as e:
            result["success"] = False
            result["errors"].append(str(e))
            try:
                self._checkpoint_store.save_failed(
                    account_id=account_id,
                    category=checkpoint_category,
                    scroll_round=scroll_rounds,
                    seen_names=seen_names,
                    total=result["total"],
                    new_count=result["new"],
                    error=str(e),
                )
            except Exception: pass
        finally:
            # 无论如何都要上报任务结束状态，防止前端 UI 状态卡死
            if callback:
                if user_paused:
                    result["paused"] = True
                callback("completed", result)

            if user_paused:
                clear_contact_sync_pause()
                self._checkpoint_store.save_running(
                    account_id=account_id,
                    category=checkpoint_category,
                    state="RUNNING",
                    scroll_round=scroll_rounds,
                    seen_names=seen_names,
                    total=result["total"],
                    new_count=result["new"],
                )
            elif result["success"] and not result["errors"]:
                self._checkpoint_store.clear(account_id, checkpoint_category)

        return result

