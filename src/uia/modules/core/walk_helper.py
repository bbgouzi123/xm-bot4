import logging
import time
import random
import ctypes
import re
import uiautomation as uia


def _logical_to_physical(hwnd: int, lx: int, ly: int):
    """将逻辑坐标转换为物理像素坐标（兼容高 DPI 多显示器场景）。

    UIA BoundingRectangle / GetWindowRect 在 DPI-aware 进程中返回逻辑坐标，
    而 GetWindowDC + GetPixel 操作的是物理像素坐标。
    在 125%/150% 等高缩放比例的电脑上两者存在系数差异，
    必须通过此函数统一坐标系后才能正确扫描像素。
    """
    try:
        # Per-Monitor DPI V2 接口（Win 10 1607+，支持多显示器不同 DPI）
        pt = ctypes.wintypes.POINT(lx, ly)
        ctypes.windll.user32.LogicalToPhysicalPointForPerMonitorDPI(hwnd, ctypes.byref(pt))
        return pt.x, pt.y
    except Exception:
        try:
            # 降级：单显示器全局 DPI 接口（Win 8.1+）
            pt = ctypes.wintypes.POINT(lx, ly)
            ctypes.windll.user32.LogicalToPhysicalPoint(hwnd, ctypes.byref(pt))
            return pt.x, pt.y
        except Exception:
            # 兜底：返回原始逻辑坐标（100% DPI 下等价）
            return lx, ly

logger = logging.getLogger("WeChatDriver.WalkHelper")

class WeChatCoreWalkHelperMixin:
    def get_tabbar_chat_unread_count(self) -> int:
        """从左侧导航栏（MainTabBar）的“微信”按钮中获取未读消息数（支持解析 Name 中的未读文案及子控件数字角标）"""
        from src.utils.safe_uia import safe_walk_control, safe_get_name

        if not getattr(self, "hwnd", None):
            return 0

        try:
            wechat_win = uia.ControlFromHandle(self.hwnd)
            if not wechat_win.Exists(0.5):
                return 0

            tabbar = wechat_win.ToolBarControl(ClassName="mmui::MainTabBar")
            if not tabbar.Exists(0.5):
                return 0

            chat_btn = None
            buttons = [c for c in tabbar.GetChildren() if c.ControlTypeName in ("ButtonControl", "CustomControl", "Control", "Custom")]
            for btn in buttons:
                c_name = btn.Name or ""
                if any(k in c_name for k in ["微信", "Chat", "聊天", "消息", "Chats"]):
                    chat_btn = btn
                    break
            if not chat_btn and buttons:
                chat_btn = buttons[0]

            if not chat_btn:
                return 0

            # 1. 尝试从按钮自身的 Name 中提取未读数
            btn_name = chat_btn.Name or ""
            # 例如: "微信, 1条未读", "微信, 3条新消息", "1条新消息", "2条未读"
            m = re.search(r'(\d+)条(?:新消息|未读)', btn_name)
            if m:
                return int(m.group(1))

            # 2. 扫描子控件中的数字角标（通过 walk 遍历）
            for ctrl, depth in safe_walk_control(chat_btn, max_depth=5):
                c_name = safe_get_name(ctrl).strip()
                if c_name.isdigit() and 1 <= len(c_name) <= 3:
                    return int(c_name)

            # 3. 兜底检测：如果按钮 Name 包含 "新消息" 或 "未读"，直接返回 1
            if "新消息" in btn_name or "未读" in btn_name:
                return 1

            # 4. 检查是否有未读红色标记子控件
            for ctrl, depth in safe_walk_control(chat_btn, max_depth=5):
                cls = ctrl.ClassName or ""
                name_val = ctrl.Name or ""
                if "Badge" in cls or "RedDot" in cls or "红点" in name_val:
                    return 1

            # 5. 【微信 4.x Skia 架构 GDI 像素扫描兜底】
            # 因为 4.x 微信的角标是直接使用 Skia 绘制的图像，在 UIA 树中不显示，且所有无障碍属性都为空。
            # 我们通过获取 chat_btn 的 BoundingRectangle，扫描其右上角区域是否存在微信标志性的鲜红色角标像素。
            # ⚠️ 【DPI 修复】BoundingRectangle 返回的是逻辑坐标，而 GetWindowDC+GetPixel 操作物理像素坐标，
            # 在高 DPI 缩放（125%/150%）的新电脑上两者存在比例偏差，必须先转换坐标系。
            rect = chat_btn.BoundingRectangle
            # 过滤最小化 (-32000) 或非法坐标，避免无效像素扫描造成 CPU 飙升
            if rect.left == -32000 or rect.top == -32000 or rect.right <= rect.left or rect.bottom <= rect.top:
                return 0

            import win32gui
            if win32gui.IsIconic(self.hwnd):
                return 0

            # 将扫描区域的逻辑坐标转换为物理坐标，保证 GetPixel 能命中真实像素位置
            phys_left, phys_top = _logical_to_physical(self.hwnd, rect.left, rect.top)
            phys_right, phys_bottom = _logical_to_physical(self.hwnd, rect.right, rect.bottom)

            # 获取窗口物理坐标系原点（GetWindowRect 本身在物理坐标下是准确的）
            win_rect = win32gui.GetWindowRect(self.hwnd)
            if win_rect[2] <= win_rect[0] or win_rect[3] <= win_rect[1]:
                return 0

            hdc = win32gui.GetWindowDC(self.hwnd)
            try:
                red_count = 0
                scan_size = 25  # 缩小扫描范围至 25x25，防止大范围扫描向上溢出到头像区域（头像可能包含红色）
                step = 2  # 采用步长为 2 的采样扫描，减少 75% 的 GetPixel Win32 调用开销
                for y in range(phys_top, min(phys_top + scan_size, phys_bottom), step):
                    for x in range(max(phys_left, phys_right - scan_size), phys_right, step):
                        rel_x = x - win_rect[0]
                        rel_y = y - win_rect[1]
                        color = win32gui.GetPixel(hdc, rel_x, rel_y)
                        if color == -1:
                            # 遭遇 CLR_INVALID (-1)，说明该坐标或 GDI 上下文失效，直接终止扫描，避免无效循环
                            return 0
                        b = (color >> 16) & 0xff
                        g = (color >> 8) & 0xff
                        r = color & 0xff
                        # 微信红圈鲜红色特征：R强，G与B弱
                        if r > 180 and g < 120 and b < 120:
                            red_count += 1
                            if red_count > 3:  # 范围缩小后，红色采样点达 3 个以上即可判定红点存在
                                return 1
            except Exception as ex:
                logger.debug(f"[像素扫描] 扫描红点异常: {ex}")
            finally:
                win32gui.ReleaseDC(self.hwnd, hdc)

            return 0
        except Exception as e:
            logger.debug(f"[获取导航栏未读数] 异常: {e}")
            return 0

    def get_tabbar_contacts_unread_count(self) -> int:
        """从左侧导航栏（MainTabBar）的“通讯录”按钮中获取未读新好友申请数/红点数（支持安全静默的红点与像素检测，不切 Tab）"""
        from src.utils.safe_uia import safe_walk_control, safe_get_name
        from src.uia.elements import WxName

        if not getattr(self, "hwnd", None):
            return 0

        try:
            wechat_win = uia.ControlFromHandle(self.hwnd)
            if not wechat_win.Exists(0.5):
                return 0

            tabbar = wechat_win.ToolBarControl(ClassName="mmui::MainTabBar")
            if not tabbar.Exists(0.5):
                return 0

            contacts_btn = None
            buttons = [c for c in tabbar.GetChildren() if c.ControlTypeName in ("ButtonControl", "CustomControl", "Control", "Custom")]
            for btn in buttons:
                c_name = btn.Name or ""
                if WxName.CONTACTS_NAV in c_name or "ͨѶ¼" in c_name:
                    contacts_btn = btn
                    break

            if not contacts_btn:
                return 0

            # 1. 尝试从按钮自身的 Name 中提取未读数
            btn_name = contacts_btn.Name or ""
            m = re.search(r'(\d+)条(?:新消息|未读|申请)', btn_name)
            if m:
                return int(m.group(1))

            # 2. 扫描子控件中的数字角标（通过 walk 遍历）
            for ctrl, depth in safe_walk_control(contacts_btn, max_depth=5):
                c_name = safe_get_name(ctrl).strip()
                if c_name.isdigit() and 1 <= len(c_name) <= 3:
                    return int(c_name)

            # 3. 检查是否有未读红色标记子控件
            for ctrl, depth in safe_walk_control(contacts_btn, max_depth=5):
                cls = ctrl.ClassName or ""
                name_val = ctrl.Name or ""
                if "Badge" in cls or "RedDot" in cls or "红点" in name_val:
                    return 1

            # 4. 【微信 4.x Skia 架构 GDI 像素扫描兜底】
            # ⚠️ 【DPI 修复】同步修复逻辑坐标 → 物理坐标的转换，保证高 DPI 新电脑上像素扫描命中正确区域。
            rect = contacts_btn.BoundingRectangle
            if rect.left == -32000 or rect.top == -32000 or rect.right <= rect.left or rect.bottom <= rect.top:
                return 0

            import win32gui
            if win32gui.IsIconic(self.hwnd):
                return 0

            # 将扫描区域的逻辑坐标转换为物理坐标
            phys_left, phys_top = _logical_to_physical(self.hwnd, rect.left, rect.top)
            phys_right, phys_bottom = _logical_to_physical(self.hwnd, rect.right, rect.bottom)

            win_rect = win32gui.GetWindowRect(self.hwnd)
            if win_rect[2] <= win_rect[0] or win_rect[3] <= win_rect[1]:
                return 0

            hdc = win32gui.GetWindowDC(self.hwnd)
            try:
                red_count = 0
                scan_size = 25
                step = 2
                for y in range(phys_top, min(phys_top + scan_size, phys_bottom), step):
                    for x in range(max(phys_left, phys_right - scan_size), phys_right, step):
                        rel_x = x - win_rect[0]
                        rel_y = y - win_rect[1]
                        color = win32gui.GetPixel(hdc, rel_x, rel_y)
                        if color == -1:
                            return 0
                        b = (color >> 16) & 0xff
                        g = (color >> 8) & 0xff
                        r = color & 0xff
                        # 微信红圈鲜红色特征：R强，G与B弱
                        if r > 180 and g < 120 and b < 120:
                            red_count += 1
                            if red_count > 3:
                                return 1
            except Exception as ex:
                logger.debug(f"[像素扫描] 扫描通讯录红点异常: {ex}")
            finally:
                win32gui.ReleaseDC(self.hwnd, hdc)

            return 0
        except Exception as e:
            logger.debug(f"[获取通讯录未读数] 异常: {e}")
            return 0

    def _check_contacts_unread(self) -> int:
        """检查侧边栏是否有未读好友申请（采用安全静默的红点与像素检测，不切 Tab）"""
        return self.get_tabbar_contacts_unread_count()


    def _ensure_contacts_page(self, force: bool = False):
        """确保当前在通讯录页面"""
        import win32gui
        from src.uia.elements import WxName
        from src.uia.retry import exists_with_timeout, smooth_click_at
        try:
            if not force and win32gui.GetForegroundWindow() != self.hwnd: return
            print("[导航] 正在切换到通讯录页...")
            contacts_btn = self._walk_find('ButtonControl', name_contains=WxName.CONTACTS_NAV, max_depth=25)
            if contacts_btn and exists_with_timeout(contacts_btn, 1):
                smooth_click_at(contacts_btn)
                time.sleep(0.5)
        except Exception as e:
            print(f"[_ensure_contacts_page] 异常: {e}")

    def _click_session_physically(self, item) -> bool:
        """使用物理鼠标点击会话列表项"""
        try:
            if ctypes.windll.user32.GetForegroundWindow() != self.hwnd:
                print(f"[UIA] ⚠️ 微信窗口不在最前台，跳过物理点击以防误触其他窗口")
                return False

            # === 物理级防双击机制安全拦截 ===
            now = time.time()
            last_click = getattr(self, "_last_phys_click_time", 0.0)
            diff = now - last_click
            # 若距离上一次物理点击不到 1.0 秒，说明处于危险的操作系统双击判定区间，强制休眠拉开间隔
            if diff < 1.0:
                wait_time = 1.0 - diff
                logger.warning(f"[UIA] 检测到极速连续物理点击会话项（间隔仅 {diff:.3f}s），强制休眠等待 {wait_time:.3f}s 以防止误触发微信双击独立窗口")
                time.sleep(wait_time)

            from src.uia.retry.clicks import physical_click
            try:
                item.ScrollIntoView()
            except Exception:
                pass
            rect = item.BoundingRectangle
            physical_click((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2, settle=random.uniform(0.05, 0.15))
            self._last_phys_click_time = time.time()  # 记录本次物理点击的时间戳
            time.sleep(random.uniform(0.3, 0.6))
            return True
        except Exception as e:
            print(f"[UIA] 物理点击会话失败: {e}")
            return False
