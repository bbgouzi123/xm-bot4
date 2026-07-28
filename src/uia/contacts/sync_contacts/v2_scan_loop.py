import time
import uiautomation as uia
from typing import Callable, Optional, Tuple

from ...retry import (
    human_scroll,
    is_shift_pressed,
    physical_click,
    random_delay,
    smooth_click_at,
)
from src.utils.stop_signal import stop_signal
from src.uia.retry import try_click
from ..constants import (
    is_contact_sync_pause_requested,
)
from .detail_extractor import sync_single_contact_detail


class ContactSyncV2ScannerMixin:
    """扁平通讯录管理器列表滚动扫描循环的抽取实现。"""

    def _scan_contacts_manager_loop(
        self,
        mgr_win,
        detail_list,
        max_scroll: int,
        single_contact_name: Optional[str],
        checkpoint_category: str,
        existing_names: set,
        name_to_contact: dict,
        sync_details: bool,
        force_resync: bool,
        callback: Optional[Callable],
        account_id: str,
        seen_names: set,
        scroll_rounds: int,
        name_count: dict,
        contacts: list,
        result: dict,
    ) -> Tuple[int, bool]:
        """
        核心扁平扫描循环。
        Returns: (scroll_rounds, user_paused)
        """
        user_paused = False

        # 获取窗口句柄，用于在循环中进行非阻塞的存活性检测
        mgr_hwnd = None
        try:
            if mgr_win:
                mgr_hwnd = mgr_win.NativeWindowHandle
        except Exception:
            pass

        while scroll_rounds <= max_scroll:
            if is_shift_pressed() or is_contact_sync_pause_requested() or stop_signal.is_stopped:
                user_paused = True
                break

            # 验证窗口仍然存在
            try:
                if stop_signal.is_stopped:
                    user_paused = True
                    break
                
                if mgr_hwnd:
                    import win32gui
                    if not win32gui.IsWindow(mgr_hwnd):
                        result["errors"].append("通讯录管理窗口被关闭")
                        break
                else:
                    if not mgr_win.Exists(1, 0.2):
                        result["errors"].append("通讯录管理窗口被关闭")
                        break
            except Exception:
                result["errors"].append("通讯录管理窗口丢失")
                break

            # 动态获取当前帧的所有子控件，防止因为弹窗/焦点切换导致列表刷新时 COM 对象失效 (Stale Element)
            try:
                current_items = detail_list.GetChildren()
                cell_items = [x for x in current_items if "ContactsManagerDetailCell" in (x.ClassName or "")]
            except Exception:
                time.sleep(0.5)
                try:
                    current_items = detail_list.GetChildren()
                    cell_items = [x for x in current_items if "ContactsManagerDetailCell" in (x.ClassName or "")]
                except Exception:
                    break

            if not cell_items:
                break

            new_in_round = False
            frame_raw_seen = {}
            cell_count = len(cell_items)

            for cell_idx in range(cell_count):
                if is_shift_pressed() or is_contact_sync_pause_requested() or stop_signal.is_stopped:
                    user_paused = True
                    break

                # 优化：同一滚动帧内列表是静态的，避免对每个 Cell 都重复调用昂贵的 GetChildren()
                # 仅在获取 item 失败（发生 Stale COM Exception）时，才尝试刷新列表重定位
                item = cell_items[cell_idx]
                try:
                    raw_name = (item.Name or "").strip()
                except Exception:
                    try:
                        current_items = detail_list.GetChildren()
                        cell_items = [x for x in current_items if "ContactsManagerDetailCell" in (x.ClassName or "")]
                        if cell_idx >= len(cell_items):
                            break
                        item = cell_items[cell_idx]
                        raw_name = (item.Name or "").strip()
                    except Exception:
                        break

                if not raw_name:
                    continue

                display_name = ""
                remark = ""
                tag = ""
                try:
                    # 收集这一行下所有的 TextControl 控件以更鲁棒地提取昵称、备注、标签等列内容
                    txt_cells = []
                    for ctrl, _ in uia.WalkControl(item, maxDepth=3):
                        try:
                            if getattr(ctrl, "ControlTypeName", "") == "TextControl":
                                txt_cells.append(ctrl)
                        except Exception:
                            continue
                    
                    if txt_cells:
                        # 按水平 X 坐标自左向右排序
                        txt_cells.sort(key=lambda c: c.BoundingRectangle.left if c.BoundingRectangle else 0)
                        if len(txt_cells) > 0:
                            display_name = (txt_cells[0].Name or "").strip()
                        if len(txt_cells) > 1:
                            remark = (txt_cells[1].Name or "").strip()
                        if len(txt_cells) > 2:
                            tag = (txt_cells[2].Name or "").strip()
                except Exception as ex:
                    print(f"[联系人同步] 遍历行文本出错: {ex}")

                if not display_name:
                    # 兜底：用 Name 拆分
                    parts = raw_name.split(" ", 1)
                    display_name = parts[0].strip()
                    remark = parts[1].strip() if len(parts) > 1 else ""
                    tag = ""

                if not display_name:
                    continue

                # ── 帧内计数（同名联系人检测）──
                count_in_frame = frame_raw_seen.get(display_name, 0)
                frame_raw_seen[display_name] = count_in_frame + 1

                # ── 同名联系人检测 & 跨帧去重 ──
                _DUP_SUFFIXES = ["", "2", "3", "4", "5", "6", "7", "8", "9", "10"]

                if count_in_frame == 0:
                    cur_count = name_count.get(display_name, 0)
                    if cur_count == 0:
                        storage_name = display_name
                    else:
                        continue  # 跨帧残留，跳过
                else:
                    cur_count = name_count.get(display_name, 0)
                    suffix_idx = min(cur_count, len(_DUP_SUFFIXES) - 1)
                    storage_name = display_name + _DUP_SUFFIXES[suffix_idx]
                    print(f"[联系人同步] [同名] {display_name!r} -> {storage_name!r} (#{cur_count + 1})")

                if storage_name in seen_names:
                    continue

                name_count[display_name] = name_count.get(display_name, 0) + 1
                seen_names.add(storage_name)
                new_in_round = True

                contact = {
                    "name": storage_name,
                    "display_name": display_name,
                    "category": "联系人",
                    "syncTime": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
                if remark:
                    contact["remark"] = remark
                if tag:
                    contact["tag"] = tag

                contacts.append(contact)
                result["total"] += 1
                if storage_name not in existing_names:
                    result["new"] += 1
                if callback:
                    callback("contact_added", {"contact": contact, "total": result["total"], "new": result["new"]})

                # ── 详情同步（头像 & 完整资料） ──
                if sync_details:
                    is_interrupted = sync_single_contact_detail(
                        detail_list=detail_list,
                        item=item,
                        storage_name=storage_name,
                        sync_details=sync_details,
                        force_resync=force_resync,
                        name_to_contact=name_to_contact,
                        contact=contact,
                        stop_signal=stop_signal,
                        extract_callback=self._extract_details_from_profile_pop,
                    )
                    if is_interrupted:
                        user_paused = True
                        break
                    if callback:
                        callback("syncing_avatar", {
                            "contact": contact,
                            "current": result["total"],
                            "total": result["total"],
                            "current_name": storage_name
                        })

            # 更新内存缓存
            try:
                from src.utils.contacts_cache import contacts_cache
                mem = contacts_cache.get_friends(account_id)
                final_mem = {id(c): c for c in mem}
                name_index = {c.get("name"): c for c in mem if c.get("name")}
                wxid_index = {c.get("wxid"): c for c in mem if c.get("wxid")}
                for c in contacts:
                    c_wxid = c.get("wxid")
                    c_name = c.get("name")
                    target = wxid_index.get(c_wxid) if c_wxid else None
                    if not target and c_name:
                        target = name_index.get(c_name)
                    if target:
                        for k, v in c.items():
                            if v:
                                target[k] = v
                    else:
                        final_mem[id(c)] = c
                        if c_name:
                            name_index[c_name] = c
                        if c_wxid:
                            wxid_index[c_wxid] = c
                contacts_cache.set_friends(account_id, list(final_mem.values()), sync_cloud=False)
            except Exception:
                pass

            scroll_rounds += 1
            self._checkpoint_store.save_running(
                account_id=account_id,
                category=checkpoint_category,
                state="SCANNING",
                scroll_round=scroll_rounds,
                seen_names=seen_names,
                total=result["total"],
                new_count=result["new"],
            )

            if not new_in_round:
                break
            if user_paused:
                break

            # 在通讯录管理的 DetailView 上滚动
            try:
                human_scroll(detail_list, min_times=3, max_times=5, min_delay=0.4, max_delay=1.0)
            except Exception:
                # 备用：用键盘 PageDown 滚动
                try:
                    detail_list.SetFocus()
                    uia.SendKeys("{PAGEDOWN}")
                    random_delay(0.5, 0.8)
                except Exception:
                    break

        return scroll_rounds, user_paused
