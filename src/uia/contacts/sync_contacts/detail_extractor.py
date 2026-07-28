import time
import uiautomation as uia
import win32gui
import win32con
from src.uia.retry import try_click, physical_click, random_delay


def safe_exists(control, timeout=0.0) -> bool:
    """安全的控件存在性检测，防止在 COM 接口意外断开或窗口销毁时抛出 COM 异常"""
    try:
        return bool(control and control.Exists(timeout, 0))
    except Exception:
        return False


def safe_close_pop_win(pop_win) -> None:
    """安全关闭微信资料卡弹窗，使用 WM_CLOSE 消息避免发送 ESC 按键被输入钩子误拦截为用户中断"""
    try:
        if pop_win and safe_exists(pop_win, 0.1):
            hwnd = pop_win.NativeWindowHandle
            if hwnd:
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                # 轮询等待最多 0.5 秒确认关闭
                for _ in range(10):
                    time.sleep(0.05)
                    if not safe_exists(pop_win, 0.01):
                        return
            # 降级：如果无句柄或未成功关闭，尝试 Close()
            pop_win.Close()
    except Exception:
        pass


def sync_single_contact_detail(
    detail_list,
    item,
    storage_name: str,
    sync_details: bool,
    force_resync: bool,
    name_to_contact: dict,
    contact: dict,
    stop_signal,
    extract_callback,
) -> bool:
    """
    对单个联系人行提取头像和详情资料卡，将其更新写入 contact 字典中。
    如果遭遇用户暂停/停止则返回 True 表示中断。
    """
    if not sync_details:
        return False

    if stop_signal.is_stopped:
        return True

    # 检查是否需要更新详情
    target = name_to_contact.get(storage_name)
    has_avatar = target and target.get("avatar_url")

    if has_avatar and not force_resync:
        print(f"[联系人同步] [详情] {storage_name!r} 已有头像且非强制刷新，跳过提取")
        return False

    try:
        # ⚡️【首要加固】尝试重新定位 item 控件以防止 COM 引用失效（事件无法调用任何订户）
        try:
            _ = item.BoundingRectangle
        except Exception:
            try:
                refreshed_item = detail_list.ListItemControl(Name=storage_name, searchDepth=1)
                if refreshed_item.Exists(0.5):
                    item = refreshed_item
                    print(f"[联系人同步] [详情] ⚡️ 重新定位 '{storage_name}' 成功")
            except Exception as re_err:
                print(f"[联系人同步] [详情] 重新定位 '{storage_name}' 失败: {re_err}")

        # 1. 深度优先遍历（WalkControl）收集该行下所有可能符合头像大小特征的子孙控件
        candidates = []
        try:
            for child, _ in uia.WalkControl(item, maxDepth=3):
                try:
                    r = child.BoundingRectangle
                    if not r:
                        continue
                    w, h = r.width(), r.height()
                    # 头像通常是 30px-70px 左右，放宽到 18px-95px
                    # 长宽比在 0.75 到 1.35 之间
                    if 18 <= w <= 95 and 18 <= h <= 95 and 0.75 <= (w / h) <= 1.35:
                        ctype = child.ControlTypeName or ""
                        cls = child.ClassName or ""
                        # 剔除复选框 CheckBox 控件以及 Edit 控件
                        if "CheckBox" not in ctype and "CheckBox" not in cls and "Edit" not in ctype:
                            # 🌟 [关键优化] 放宽头像左边界距离整行左边界的阈值 (30 到 120 像素之间)，避免抛出 COM 异常
                            item_rect = None
                            try:
                                item_rect = item.BoundingRectangle
                            except Exception:
                                pass
                            if item_rect:
                                offset_x = r.left - item_rect.left
                                if 30 <= offset_x <= 120:
                                    candidates.append(child)
                except Exception:
                    continue
        except Exception:
            pass

        avatar_cell = None
        if candidates:
            # 按 BoundingRectangle.left 从左到右排序
            candidates.sort(key=lambda c: c.BoundingRectangle.left if c.BoundingRectangle else 0)
            avatar_cell = candidates[0]

        # 2. 备用兜底：如果 Walk 没找着，回退使用原有的直接子元素法并加入偏移判定
        if not avatar_cell:
            cells = []
            try:
                cells = item.GetChildren()
            except Exception:
                pass
            for cell in cells[:4]:
                try:
                    r = cell.BoundingRectangle
                    w, h = r.width(), r.height()
                    if 20 <= w <= 75 and 20 <= h <= 75 and 0.75 <= (w / h) <= 1.35:
                        ctype = cell.ControlTypeName or ""
                        cls = cell.ClassName or ""
                        if "CheckBox" not in ctype and "CheckBox" not in cls:
                            item_rect = None
                            try:
                                item_rect = item.BoundingRectangle
                            except Exception:
                                pass
                            if item_rect:
                                offset_x = r.left - item_rect.left
                                if 30 <= offset_x <= 120:
                                    avatar_cell = cell
                                    break
                except Exception:
                    continue
            if not avatar_cell and len(cells) > 1:
                avatar_cell = cells[1]
            elif not avatar_cell and cells:
                avatar_cell = cells[0]

        # 3. 执行点击与弹窗检测
        print(f"[联系人同步] [详情] 正在同步 {storage_name!r} 的详细资料...")
        pop_win = uia.WindowControl(ClassName="mmui::ProfileUniquePop")
        
        # 确保前一个资料卡已清理
        if pop_win.Exists(0.1):
            safe_close_pop_win(pop_win)
            time.sleep(0.2)

        clicked = False
        
        # A 方案：若定位到了 avatar_cell 控件，则优先通过它点击
        if avatar_cell:
            try_click(avatar_cell)
            if safe_exists(pop_win, 1.0):
                clicked = True
            else:
                # 降级：执行物理点击头像控件中心
                print(f"[联系人同步] [详情] 静默点击未触发，尝试 physical_click 头像控件...")
                rect = None
                try:
                    rect = avatar_cell.BoundingRectangle
                except Exception:
                    pass
                if rect:
                    physical_click((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
                    if safe_exists(pop_win, 1.0):
                        clicked = True
                        
        # B 方案：若未定位到控件，或者 A 方案失败，则采用几何中心坐标物理点击兜底
        if not clicked:
            rect = None
            try:
                rect = item.BoundingRectangle
            except Exception:
                pass
            if rect:
                h_item = rect.bottom - rect.top
                # 🌟 [关键优化] 头像中心大致位于整行左侧 h_item * 1.85 倍高度处，以绝对避开左侧单选框 (Checkbox)
                cx = rect.left + int(h_item * 1.85)
                cy = (rect.top + rect.bottom) // 2
                print(f"[联系人同步] [详情] 采用头像中心物理点击兜底: x={cx}, y={cy}")
                physical_click(cx, cy)
                if safe_exists(pop_win, 1.5):
                    clicked = True

        # 4. 提取资料
        if safe_exists(pop_win, 3.0):
            details = extract_callback(pop_win, storage_name)
            # 更新 contact 字典
            for k, v in details.items():
                if v:
                    contact[k] = v
            
            # 🌟 [关键修正] 遵循用户步骤：关闭大图预览后悬浮层会自动关闭。
            # 稍作延时，若悬浮层未自动关闭（即仍然存在），才手动调用安全关闭。
            time.sleep(0.15)
            if safe_exists(pop_win, 0.15):
                print(f"[联系人同步] [详情] 资料卡未随大图自动关闭，手动执行安全关闭...")
                safe_close_pop_win(pop_win)
            else:
                print(f"[联系人同步] [详情] 资料卡已随大图预览自动关闭，符合流程预期")
            
            random_delay(0.3, 0.5)
            print(f"[联系人同步] [详情] {storage_name!r} 资料提取完成")
        else:
            print(f"[联系人同步] [详情] 资料卡未弹出: {storage_name!r}")
    except Exception as de:
        print(f"[联系人同步] [详情] 提取异常: {de}")

    return False
