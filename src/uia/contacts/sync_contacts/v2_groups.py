import time
import random
import re
import uiautomation as uia
from src.utils.stop_signal import stop_signal
from ...retry import (
    smooth_click_at,
    is_shift_pressed,
)
from ..constants import (
    is_contact_sync_pause_requested,
)

class ContactSyncV2GroupsMixin:
    """群聊同步 V2 的实现。"""

    def _sync_groups(self, mgr_win, account_id, callback) -> dict:
        res = {"success": True, "total": 0, "new": 0, "errors": []}
        groups = []

        def is_group_item(name: str) -> bool:
            name = (name or "").strip()
            if not name:
                return False
            for exclude in ["全部", "朋友权限", "标签", "最近群聊", "群聊", "通讯录管理", "筛选"]:
                if name.startswith(exclude):
                    return False
            return bool(re.search(r'\(\d+\)$', name))

        def clean_group_name(name: str) -> str:
            return re.sub(r'\s*\(\d+\)$', '', name).strip()

        # ── 1. 定位“最近群聊”或“群聊”导航项 ──
        gp = None
        for name in ["最近群聊", "群聊"]:
            try:
                ctrl = mgr_win.PaneControl(Name=name)
                if ctrl.Exists(1.0):
                    gp = ctrl
                    break
            except Exception:
                pass
        
        if not gp:
            try:
                for child, _ in uia.WalkControl(mgr_win, maxDepth=5):
                    if (child.Name or "").strip() in ("最近群聊", "群聊"):
                        gp = child
                        break
            except Exception:
                pass

        if not gp or not gp.Exists(2):
            res["errors"].append("未找到最近群聊/群聊分组按钮")
            return res

        # ── 2. 展开分类（如果未展开） ──
        is_expanded = False
        try:
            ec = gp.GetExpandCollapsePattern()
            if ec:
                import uiautomation as uia_lib
                if ec.ExpandCollapseState == uia_lib.ExpandCollapseState.Expanded:
                    is_expanded = True
        except Exception:
            pass
        
        if not is_expanded:
            try:
                parent = gp.GetParentControl()
                if parent:
                    for child in parent.GetChildren():
                        if is_group_item(child.Name):
                            is_expanded = True
                            break
            except Exception:
                pass
        
        if not is_expanded:
            print("[群聊同步] 检测到群聊折叠项未展开，正在点击展开...")
            smooth_click_at(gp)
            time.sleep(1.0)

        # ── 3. 抓取左侧导航树展开子项 ──
        left_list_ctrl = None
        curr = gp
        for _ in range(5):
            if not curr:
                break
            if curr.ControlTypeName == "ListControl":
                left_list_ctrl = curr
                break
            curr = curr.GetParentControl()
        
        if not left_list_ctrl:
            left_list_ctrl = gp.GetParentControl()

        def _get_left_items(container):
            if not container:
                return
            for item in container.GetChildren():
                if stop_signal.is_stopped or is_contact_sync_pause_requested() or is_shift_pressed():
                    break
                name = (item.Name or "").strip()
                if not name:
                    try:
                        for sub_child, _ in uia.WalkControl(item, maxDepth=2):
                            sub_name = (sub_child.Name or "").strip()
                            if is_group_item(sub_name):
                                name = sub_name
                                break
                    except Exception:
                        pass
                
                if is_group_item(name):
                    clean_name = clean_group_name(name)
                    g = {
                        "name": clean_name,
                        "display_name": clean_name,
                        "category": "群聊",
                        "syncTime": time.strftime("%Y-%m-%dT%H:%M:%S")
                    }
                    if not any(x["name"] == clean_name for x in groups):
                        groups.append(g)
                        res["total"] += 1
                        print(f"[群聊同步] 成功提取到群聊: {clean_name}")
                        if callback:
                            callback("contact_added", {"contact": g, "total": res["total"], "new": res["total"]})

        if left_list_ctrl:
            print("[群聊同步] 开始抓取左侧导航栏中的群聊列表...")
            _get_left_items(left_list_ctrl)
            
            # 滚动加载更多
            scr = left_list_ctrl.GetScrollPattern()
            if scr:
                step, pos, last = 0.05, 0.0, len(groups)
                while pos <= 1.0:
                    if stop_signal.is_stopped or is_contact_sync_pause_requested() or is_shift_pressed():
                        break
                    before = len(groups)
                    pos += step
                    try:
                        scr.SetScrollPercent(-1, min(pos, 1.0))
                    except Exception:
                        break
                    time.sleep(random.uniform(0.3, 0.5))
                    _get_left_items(left_list_ctrl)
                    
                    new_c = len(groups) - before
                    step *= 10 if new_c == 0 else (2 if new_c < 5 else 1)
                    if len(groups) == last and pos >= 1.0:
                        break
                    last = len(groups)
            else:
                try:
                    left_list_ctrl.SetFocus()
                    last_count = 0
                    for _ in range(25):
                        if stop_signal.is_stopped or is_contact_sync_pause_requested() or is_shift_pressed():
                            break
                        uia.SendKeys("{PAGEDOWN}")
                        time.sleep(0.4)
                        _get_left_items(left_list_ctrl)
                        if len(groups) == last_count:
                            break
                        last_count = len(groups)
                except Exception:
                    pass

        # ── 4. 兜底方案：如果左侧没有抓到，尝试旧的右侧主面板方案 ──
        if not groups:
            print("[群聊同步] 左侧抓取未获取到群聊，执行右侧主面板兜底方案...")
            def _get_items_legacy(list_ctrl):
                for item in list_ctrl.GetChildren():
                    if stop_signal.is_stopped or is_contact_sync_pause_requested() or is_shift_pressed():
                        break
                    btn = item.ButtonControl()
                    if btn.Exists(0.5) and btn.Name and btn.Name != "群聊" and btn.Name != "最近群聊":
                        clean_name = clean_group_name(btn.Name)
                        g = {
                            "name": clean_name,
                            "display_name": clean_name,
                            "category": "群聊",
                            "syncTime": time.strftime("%Y-%m-%dT%H:%M:%S")
                        }
                        if not any(x["name"] == clean_name for x in groups):
                            groups.append(g)
                            res["total"] += 1
                            if callback:
                                callback("contact_added", {"contact": g, "total": res["total"], "new": res["total"]})

            def _find_legacy(pane):
                for child in pane.GetChildren():
                    if stop_signal.is_stopped or is_contact_sync_pause_requested() or is_shift_pressed():
                        break
                    t = child.ControlTypeName or ""
                    if t == "ListControl":
                        scr = child.GetScrollPattern()
                        if scr:
                            _get_items_legacy(child)
                            step, pos, last = 0.005, 0.0, len(groups)
                            while pos <= 1.0:
                                if stop_signal.is_stopped or is_contact_sync_pause_requested() or is_shift_pressed():
                                    break
                                before = len(groups)
                                pos += step
                                try:
                                    scr.SetScrollPercent(-1, min(pos, 1.0))
                                except Exception:
                                    break
                                time.sleep(random.uniform(0.2, 0.4))
                                _get_items_legacy(child)
                                new_c = len(groups) - before
                                step *= 10 if new_c == 0 else (2 if new_c < 5 else 1)
                                if len(groups) == last and pos >= 1.0:
                                    break
                                last = len(groups)
                        else:
                            _get_items_legacy(child)
                    elif t == "PaneControl":
                        _find_legacy(child)

            try:
                np = gp.GetNextSiblingControl()
                if np and np.Exists(1):
                    cp = np.PaneControl()
                    if cp and cp.Exists(1):
                        _find_legacy(cp)
            except Exception as le:
                print(f"[群聊同步] 右侧主面板兜底方案异常: {le}")

        # ── 5. 同步写入与报错 ──
        if groups:
            from src.utils.contacts_cache import contacts_cache
            contacts_cache.set_groups(account_id, groups, sync_cloud=True)
            print(f"[群聊同步] 同步群聊完成，共成功提取 {len(groups)} 个群聊")
        else:
            res["errors"].append("未读取到任何群聊，请检查最近群聊是否在微信中正常显示并包含群聊项。")

        return res
