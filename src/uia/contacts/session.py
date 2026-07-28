import re
import time
from collections import Counter
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import uiautomation as uia

from ..elements import WxClass, WxName
from ..retry import (
    exists_with_timeout,
    is_escape_pressed,
    is_shift_pressed,
    random_delay,
    human_scroll,
    smooth_click_at,
)
from src.utils.stop_signal import stop_signal
from src.utils.uia_lock import UIATaskPriority
from src.utils.uia_task_runner import run_uia_task
from .constants import (
    is_contact_sync_pause_requested,
    is_denied_contact_row_name,
    is_synthetic_placeholder_wxid,
)
from .group_ops import ContactGroupOpsMixin


class ContactBaseMixin(ContactGroupOpsMixin):
    def _run_contact_task(self, task_name: str, func: Callable[[], dict]) -> dict:
        """统一的通讯录 UIA 任务入口。"""
        with run_uia_task(
            task_name=task_name,
            priority=UIATaskPriority.HIGH,
            timeout=120,
            pause_background_tasks=True,
        ):
            return func()

    def _get_contacts_nav_button(self):
        contacts_btn = self.driver.root.ButtonControl(Name=WxName.CONTACTS_NAV)
        if contacts_btn and exists_with_timeout(contacts_btn, 2):
            return contacts_btn

        nav_bar = self.driver.root.ToolBarControl(AutomationId="main_tabbar")
        if nav_bar and exists_with_timeout(nav_bar, 2):
            children = nav_bar.GetChildren()
            if len(children) > 1:
                return children[1]
        return None

    def _open_contacts_page(self) -> bool:
        try:
            self.driver.SwitchToThisWindow()
        except Exception:
            pass

        contacts_btn = self._get_contacts_nav_button()
        if not contacts_btn:
            return False

        smooth_click_at(contacts_btn)
        random_delay(0.3, 0.5)
        return True

    def _find_contacts_list(self):
        dr = self.driver
        root = getattr(dr, "root", None)
        if not root:
            return None

        def _ok(c, t=2.0):
            return c and exists_with_timeout(c, t)

        try:
            c = dr._walk_find("ListControl", class_name=WxClass.STICKY_LIST, max_depth=22)
            if _ok(c):
                return c
        except Exception:
            pass

        try:
            c = root.ListControl(ClassName=WxClass.STICKY_LIST, searchDepth=24)
            if _ok(c):
                return c
        except Exception:
            pass

        try:
            c = dr._walk_find("TreeControl", name="联系人", max_depth=22)
            if _ok(c):
                return c
        except Exception:
            pass

        try:
            c = dr._walk_find("ListControl", name="联系人", max_depth=22)
            if _ok(c):
                return c
        except Exception:
            pass

        try:
            pane = None
            for ctrl, _d in uia.WalkControl(root, maxDepth=18):
                if is_escape_pressed() or is_contact_sync_pause_requested() or stop_signal.is_stopped:
                    break
                try:
                    if (ctrl.ClassName or "") == WxClass.CONTACTS_CONTROL:
                        pane = ctrl
                        break
                except Exception:
                    continue
            if pane and exists_with_timeout(pane, 1):
                try:
                    sub = pane.ListControl(ClassName=WxClass.STICKY_LIST, searchDepth=14)
                    if _ok(sub, 1.5):
                        return sub
                except Exception:
                    pass
                for ch, _ in uia.WalkControl(pane, maxDepth=10):
                    try:
                        if ch.ControlTypeName == "ListControl" and WxClass.STICKY_LIST in (ch.ClassName or ""):
                            if _ok(ch, 1.5):
                                return ch
                    except Exception:
                        continue
        except Exception:
            pass

        try:
            for ctrl, _depth in uia.WalkControl(root, maxDepth=22):
                if is_escape_pressed() or is_contact_sync_pause_requested() or stop_signal.is_stopped:
                    break
                try:
                    if ctrl.ControlTypeName != "ListControl":
                        continue
                    cls = ctrl.ClassName or ""
                    if WxClass.STICKY_LIST == cls or "StickyHeaderRecycler" in cls:
                        if exists_with_timeout(ctrl, 1.5):
                            return ctrl
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def _contacts_list_rect_ok(self, contacts_list) -> bool:
        try:
            if not contacts_list:
                return False
            r = contacts_list.BoundingRectangle
            w = r.right - r.left
            h = r.bottom - r.top
            return w > 2 and h > 2
        except Exception:
            return False

    def _ensure_contacts_context(self, contacts_list=None, reason: str = ""):
        if stop_signal.is_stopped:
            return None
        try:
            if contacts_list and exists_with_timeout(contacts_list, 0.6):
                if self._contacts_list_rect_ok(contacts_list):
                    contacts_list.GetChildren()
                    return contacts_list
        except Exception:
            pass

        recovered = self._find_contacts_list()
        if recovered:
            return recovered

        if reason:
            print(f"[联系人同步] 检测到上下文漂移，开始回正: {reason}")
        else:
            print("[联系人同步] 检测到上下文漂移，开始回正")

        if not self._open_contacts_page():
            return None

        recovered = self._find_contacts_list()
        if recovered:
            random_delay(0.5, 0.8)
        return recovered



    def _prepare_contacts_list_session(
        self, target_category: Optional[str]
    ) -> Tuple[Optional[Any], Optional[str]]:
        if stop_signal.is_stopped:
            return None, "用户按下 ESC 键中断了操作"

        if not self._open_contacts_page():
            return None, "未找到通讯录按钮"

        contacts_list = None
        for attempt in range(4):
            if stop_signal.is_stopped:
                break
            contacts_list = self._ensure_contacts_context(
                reason="初始化联系人列表" + (f" 第{attempt + 1}次" if attempt else "")
            )
            if contacts_list:
                break
            random_delay(0.45, 0.85)
            if attempt < 3:
                self._open_contacts_page()
        if not contacts_list:
            return None, "未找到联系人列表"

        try:
            scroll_ptn = contacts_list.GetScrollPattern()
            if scroll_ptn:
                scroll_ptn.SetScrollPercent(-1, 0.0)
        except Exception:
            pass

        # 强力回到顶部：多次发送 HOME 键
        try:
            contacts_list.SetFocus()
            smooth_click_at(contacts_list)
            random_delay(0.2, 0.3)
            uia.SendKeys("{HOME}")
            time.sleep(0.3)
            uia.SendKeys("{HOME}")
            time.sleep(0.3)
            uia.SendKeys("{HOME}")
            time.sleep(0.5)
        except Exception:
            pass

        # ── 强力展开分组逻辑 ──
        try:
            target_to_expand = target_category if target_category else "联系人"
            expanded = False
            
            # 循环滚动寻找并展开分组（最多滚动 12 次，防止无限循环）
            for _expand_scroll in range(12):
                if is_contact_sync_pause_requested() or stop_signal.is_stopped:
                    break
                    
                items = contacts_list.GetChildren()
                found_header = None
                
                # [重要优化] 在搜索目标分组的过程中，主动折叠遇到的其它已展开分组
                # 这样可以极大减少滚动距离，并防止“误判”
                from .constants import is_denied_contact_row_name
                
                for item in items:
                    name = (item.Name or "").strip()
                    if not is_denied_contact_row_name(name):
                        continue
                        
                    # 如果是目标分组
                    if name.startswith(target_to_expand):
                        found_header = item
                        # 这里不直接 break，因为可能后面还有需要折叠的组，
                        # 但为了性能，我们先处理目标
                        break
                    
                    # 如果是其它分组（如：新的朋友、企业微信联系人等）
                    # 主动检查并折叠
                    # 注意：只折叠那些在 sys_prefixes 里的已知大分组
                    sys_prefixes = ("新的朋友", "公众号", "企业微信联系人", "服务号", "订阅号", "视频号", "群聊", "我的企业", "标签")
                    is_other_sys = False
                    for pre in sys_prefixes:
                        if name.startswith(pre):
                            is_other_sys = True
                            break
                    
                    if is_other_sys:
                        if self._try_collapse_group_header(item, contacts_list):
                            # 折叠后 UI 变化，重新获取 items
                            random_delay(0.5, 0.8)
                            break 
                
                if found_header:
                    self._try_expand_group_header(found_header, contacts_list)
                    expanded = True
                    break
                
                # 未找到则尝试向下滑动一点再找
                print(f"[联系人同步] 未在当前视图找到分组 '{target_to_expand}'，尝试滑动寻找... ({_expand_scroll+1}/12)")
                human_scroll(contacts_list, min_times=2, max_times=4)
                random_delay(0.5, 0.8)

            if not expanded:
                print(f"[联系人同步] [!] 警告：尝试多次未找到或无法展开分组 '{target_to_expand}'")
                
            # 回到顶部，为后续扫描做准备
            try:
                scroll_ptn = contacts_list.GetScrollPattern()
                if scroll_ptn:
                    scroll_ptn.SetScrollPercent(-1, 0.0)
                else:
                    contacts_list.SetFocus()
                    smooth_click_at(contacts_list)
                    uia.SendKeys("{HOME}")
            except Exception:
                pass

            # 补发 HOME 键确保回到顶部
            try:
                contacts_list.SetFocus()
                uia.SendKeys("{HOME}")
                time.sleep(0.3)
                uia.SendKeys("{HOME}")
                time.sleep(0.5)
            except Exception:
                pass
                
        except Exception as e:
            print(f"[联系人同步] 展开分组异常: {e}")

        random_delay(0.5, 0.8)
        return contacts_list, None

    def _avatar_png_to_jpeg_data_uri(self, png_path: str) -> str:
        from io import BytesIO
        import base64
        from PIL import Image

        im = Image.open(png_path).convert("RGB")
        w, h = im.size
        max_side = 512
        if max(w, h) > max_side:
            r = max_side / float(max(w, h))
            im = im.resize((int(w * r), int(h * r)), Image.Resampling.LANCZOS)
        buf = BytesIO()
        im.save(buf, format="JPEG", quality=86, optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        return f"data:image/jpeg;base64,{b64}"
