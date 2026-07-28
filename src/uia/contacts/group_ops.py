import time
import uiautomation as uia
from typing import Any

from ..elements import WxClass
from ..retry import (
    exists_with_timeout,
    random_delay,
    smooth_click_at,
)
from src.utils.stop_signal import stop_signal


class ContactGroupOpsMixin:
    """通讯录分组展开、折叠与可见性辅助操作。"""

    def _has_real_contacts_visible(self, contacts_list) -> bool:
        """启发式检测：列表中是否有真正的联系人行（而非全是分组标题）。"""
        try:
            from .constants import is_denied_contact_row_name
            
            items = contacts_list.GetChildren()
            for it in items:
                # 1. 强校验：如果是已知的分组类名，绝对不是联系人
                cls = getattr(it, "ClassName", "") or ""
                if cls == WxClass.CONTACT_GROUP:
                    continue
                
                # 2. 弱校验：名称过滤
                n = (it.Name or "").strip()
                if not n:
                    continue
                # 分组标题（公众号181、联系人190 等）
                if is_denied_contact_row_name(n):
                    continue
                # 字母索引（A、B、C…）或特殊固定行
                if (len(n) == 1 and n.isalpha()) or n == "星标朋友":
                    continue
                
                # 能走到这里说明有真正的联系人行
                return True
        except Exception:
            pass
        return False

    def _is_group_header_expanded(self, header_item, contacts_list) -> bool:
        """
        探测指定的分组标题当前是否处于展开状态。
        基于微信 4.x 启发式判断：检查在 List 中紧随该标题之后的元素。
        """
        try:
            # 1. 尝试使用标准的 ExpandCollapsePattern
            try:
                ec_pattern = header_item.GetExpandCollapsePattern()
                if ec_pattern:
                    import uiautomation as uia_lib
                    return ec_pattern.ExpandCollapseState == uia_lib.ExpandCollapseState.Expanded
            except Exception:
                pass

            # 2. 启发式判断：定位当前 Header 在列表中的位置，检查后继项
            items = contacts_list.GetChildren()
            header_hwnd = header_item.NativeWindowHandle
            
            for i, item in enumerate(items):
                if item.NativeWindowHandle == header_hwnd:
                    # 找到了当前 Header，检查其后的项
                    if i + 1 < len(items):
                        nxt = items[i + 1]
                        nxt_name = (nxt.Name or "").strip()
                        nxt_class = nxt.ClassName or ""
                        
                        # A. 如果下一项包含头像控件，肯定是展开的
                        if "ContactHeadView" in nxt_class:
                            return True
                            
                        # B. 如果下一项是另一个系统标题或字母索引，则当前项一定是折叠的
                        from .constants import is_denied_contact_row_name
                        if is_denied_contact_row_name(nxt_name) or (len(nxt_name) == 1 and nxt_name.isalpha()):
                            return False
                        
                        # C. 既不是头像也不是另一个标题，且名称不为空，通常是联系人名，视为展开
                        if nxt_name:
                            return True
                    
                    # 如果 Header 是列表最后一项，且无法通过 Pattern 确认，保守估计为折叠
                    return False
            return False
        except Exception as e:
            print(f"[联系人同步] 判断分组展开状态异常: {e}")
            return False

    def _try_collapse_group_header(self, header_item, contacts_list) -> bool:
        """尝试折叠通讯录分组标题。"""
        if self._is_group_header_expanded(header_item, contacts_list):
            header_name = (header_item.Name or "").strip()
            print(f"[联系人同步] 检测到分组 '{header_name}' 处于展开状态，正在折叠以清理视图...")
            try:
                ec_pattern = header_item.GetExpandCollapsePattern()
                if ec_pattern:
                    ec_pattern.Collapse()
                else:
                    smooth_click_at(header_item)
                random_delay(0.8, 1.2)
                return True
            except Exception:
                pass
        return False

    def _try_expand_group_header(self, header_item, contacts_list) -> bool:
        """尝试展开通讯录分组标题。返回 True 表示执行了展开操作。"""
        try:
            header_name = (header_item.Name or "").strip()

            # 使用改进后的判断逻辑
            if self._is_group_header_expanded(header_item, contacts_list):
                print(f"[联系人同步] 分组 '{header_name}' 已处于展开状态")
                return False

            print(f"[联系人同步] 分组 '{header_name}' 处于折叠状态，正在点击展开...")
            
            # 尝试通过 Pattern 展开
            try:
                ec_pattern = header_item.GetExpandCollapsePattern()
                if ec_pattern:
                    import uiautomation as uia_lib
                    ec_pattern.Expand()
                    random_delay(1.0, 1.5)
                    return True
            except Exception:
                pass

            # 物理点击展开
            smooth_click_at(header_item)
            random_delay(1.5, 2.0)

            # 二次验证
            if self._is_group_header_expanded(header_item, contacts_list):
                print(f"[联系人同步] V 分组 '{header_name}' 展开成功")
            else:
                print(f"[联系人同步] 展开后仍未检测到联系人，尝试再次点击...")
                smooth_click_at(header_item)
                random_delay(1.5, 2.0)
            return True

        except Exception as e:
            print(f"[联系人同步] 展开分组异常: {e}")
            return False
