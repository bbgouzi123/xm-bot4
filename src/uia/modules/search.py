import time
import logging
import pyperclip
import uiautomation as uia

from src.uia.elements import WxClass
from src.uia.retry import random_delay, exists_with_timeout, try_click, smooth_click_at

logger = logging.getLogger("WeChatDriver")

class WeChatSearchMixin:
    """微信搜索会话混入类，负责利用搜索输入框搜索并点击会话"""

    def _find_search_box(self):
        """定位微信主界面搜索输入框"""
        search_box = self.root.EditControl(Name="搜索")
        if not search_box or not search_box.Exists(0.1):
            search_box = self.root.EditControl(ClassName="mmui::XValidatorTextEdit")
        if not search_box or not search_box.Exists(0.1):
            search_box = self._walk_find_edit(name="搜索", class_name=WxClass.SEARCH_BOX)
        if not search_box or not search_box.Exists(0.1):
            search_box = self._walk_find_edit(class_name=WxClass.SEARCH_BOX)
        return search_box if (search_box and search_box.Exists(0.1)) else None

    def _resolve_search_popover(self, wait_total: float = 4.0):
        """轮询 + WalkControl：搜索面板未必挂在 root 下一层。"""
        deadline = time.monotonic() + wait_total
        while time.monotonic() < deadline:
            try:
                for factory in (
                    lambda: self.root.PaneControl(ClassName=WxClass.SEARCH_POPOVER),
                    lambda: self.root.WindowControl(ClassName=WxClass.SEARCH_POPOVER),
                ):
                    try:
                        p = factory()
                        if p and exists_with_timeout(p, 0.25):
                            return p
                    except Exception:
                        pass
                from src.utils.safe_uia import safe_walk_control, safe_class_name
                for ctrl, _ in safe_walk_control(self.root, max_depth=26):
                    try:
                        cn = safe_class_name(ctrl)
                        if cn == WxClass.SEARCH_POPOVER or "SearchContentPopover" in cn:
                            if exists_with_timeout(ctrl, 0.2):
                                return ctrl
                    except Exception:
                        continue
            except Exception:
                pass
            time.sleep(0.12)
        return None

    def _find_search_result_list_fallback(self):
        """popover 枚举失败时，按分组标题行启发式定位结果 ListControl。"""
        markers = (
            "联系人", "群聊", "群组", "聊天记录", "搜索网络结果",
            "小程序", "公众号",
        )
        try:
            best = None
            best_score = -1
            from src.utils.safe_uia import safe_walk_control, safe_control_type, safe_get_name, safe_get_children
            for ctrl, _ in safe_walk_control(self.root, max_depth=28):
                try:
                    if safe_control_type(ctrl) != "ListControl":
                        continue
                    if not exists_with_timeout(ctrl, 0.12):
                        continue
                    kids = safe_get_children(ctrl)
                    if len(kids) < 2:
                        continue
                    names = [safe_get_name(c).strip() for c in kids[:14]]
                    score = sum(1 for n in names if n in markers)
                    if score > best_score:
                        best_score = score
                        best = ctrl
                except Exception:
                    continue
            if best is not None and best_score >= 1:
                return best
        except Exception:
            pass
        return None

    def _search_and_click(self, name: str) -> bool:
        """通过搜索查找并打开会话（对齐老系统 search_session_41x）"""
        try:
            search_box = self._walk_find_edit(name="搜索", class_name=WxClass.SEARCH_BOX)
            if not search_box:
                search_box = self._walk_find_edit(class_name=WxClass.SEARCH_BOX)
            if not search_box:
                print("[UIA] 搜索过程受阻: 未能在主界面找到搜索框！")
                return False

            print("[UIA] 找到搜索框，准备输入目标名称")
            try_click(search_box, max_retries=2, delay=0.25)
            random_delay(0.12, 0.2)
            try:
                search_box.SetFocus()
            except Exception:
                pass
            smooth_click_at(search_box)
            random_delay(0.28, 0.45)

            try:
                pyperclip.copy(name)
            except Exception as _e:
                print(f"[UIA] pyperclip.copy 失败: {_e}")

            try:
                search_box.SendKeys("{Ctrl}a", waitTime=0.1)
                random_delay(0.04, 0.08)
                search_box.SendKeys("{Ctrl}v", waitTime=0.28)
            except Exception as _e:
                print(f"[UIA] 搜索框 Ctrl+V 异常: {_e}")

            random_delay(0.35, 0.55)
            need_fallback_type = True
            try:
                vp = search_box.GetValuePattern()
                if vp:
                    val = (vp.Value or "").strip()
                    if val == name or name in val:
                        need_fallback_type = False
            except Exception:
                pass
            if need_fallback_type:
                print(f"[UIA] 剪贴板未写入搜索框或无法读取，回退逐字输入: {name!r}")
                try:
                    search_box.SendKeys("{Ctrl}a", waitTime=0.08)
                    search_box.SendKeys("{Delete}", waitTime=0.08)
                    search_box.SendKeys(name, waitTime=0.06)
                except Exception as _e:
                    print(f"[UIA] 逐字输入失败: {_e}")

            random_delay(0.85, 1.2)

            popover = self._resolve_search_popover(4.0)

            # 【核心优化】结合分类过滤缩短等待时间，防止在结果快速加载时发生物理偏移或错失本地优先点击期
            print("[UIA] 等待微信搜索结果稳定加载与渲染...")
            random_delay(0.5, 0.8)

            if popover:
                # 🚀 快速定位优化：优先尝试通过 AutomationId 精准定位结果，规避 Walk 带来的超时和死锁
                try:
                    for autoid in (f"search_item_{name}", f"search_item_function_{name}"):
                        target_ctrl = popover.Control(AutomationId=autoid)
                        if target_ctrl.Exists(0.05):
                            print(f"[UIA] 快速通过 AutomationId '{autoid}' 精准匹配到目标控件，尝试点击")
                            self._click_session_physically(target_ctrl)
                            random_delay(0.5, 1.0)
                            if self._verify_chat_switched(name):
                                return True
                except Exception as _e:
                    pass

            search_list = None
            if popover:
                try:
                    search_list = popover.ListControl()
                    if not search_list or not exists_with_timeout(search_list, 1.5):
                        search_list = None
                        for ch in popover.GetChildren():
                            if getattr(ch, "ControlTypeName", "") == "ListControl":
                                search_list = ch
                                break
                except Exception:
                    search_list = None

            if not search_list:
                search_list = self._find_search_result_list_fallback()

            if not search_list:
                print("[UIA] 搜索过程受阻: 弹窗未出现或未加载。")
                self._search_back(search_box)
                return False

            current_category = ""
            found = False
            
            # 使用两轮扫描机制：点击“查看全部”展开后，再重新扫描一次新加载的完整结果
            for scan_round in range(2):
                items = search_list.GetChildren()
                if not items:
                    break
                
                has_expanded = False
                for item in items:
                    item_name = (item.Name or "").strip()
                    if not item_name:
                        continue

                    if item_name in ("联系人", "群聊", "群组", "小程序", "公众号", "搜索网络结果", "网络搜索结果", "搜一搜", "聊天记录", "功能", "最常使用", "服务号"):
                        current_category = item_name
                        print(f"[UIA] 搜索分类定位: {current_category}")
                        continue

                    # 🔍 自动展开“查看全部”列表项：
                    if "查看全部" in item_name and current_category in ("联系人", "群聊", "群组", "功能", "服务号"):
                        print(f"[UIA] 发现展开更多按钮 '{item_name}'，点击以显示所有搜索结果...")
                        try_click(item, max_retries=2, delay=0.15)
                        random_delay(0.3, 0.5)
                        has_expanded = True
                        break

                    # 排除网络搜索结果（如搜一搜、网络结果等），这些并不是真实的微信会话/联系人
                    if "网络" in current_category or "搜一搜" in current_category:
                        continue

                    if item_name == name or item_name.startswith(f"{name}"):
                        print(f"[UIA] 搜索精准匹中: '{item_name}' (属于: {current_category})，即将执行点击。")
                        self._click_session_physically(item)
                        random_delay(0.5, 1.0)
                        if self._verify_chat_switched(name):
                            found = True
                            break
                            
                if found or not has_expanded:
                    break

            if not found:
                print("[UIA] 搜索无精确命中项，尝试强制回车进入...")
                try:
                    search_box.SendKeys("{Enter}", waitTime=0.2)
                except Exception:
                    uia.SendKeys("{Enter}")
                random_delay(0.5, 1.0)
                if self._verify_chat_switched(name):
                    found = True

            if not found:
                print("[UIA] 搜索强制回车后依然未能验证切换成功！")
                self._search_back(search_box)
                return False

            self._search_back(search_box)
            return True

        except Exception as e:
            logger.error(f"搜索会话失败: {e}")
            return False

    def _search_back(self, search_box=None):
        """关闭搜索并恢复窗口（对齐老系统 search_back）"""
        try:
            if search_box:
                search_box.SendKeys('{Ctrl}a{Delete}', waitTime=0.5)
            uia.SendKeys('{Escape}')
            random_delay(0.2, 0.3)
            self.SwitchToThisWindow()
        except Exception:
            pass
