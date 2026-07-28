import os
import ctypes
import time
import logging
import win32gui
import uiautomation as uia

logger = logging.getLogger("WeChatProfile")

def print(*args, **kwargs):
    try:
        msg = " ".join(str(arg) for arg in args)
        logger.debug(msg)
    except:
        pass

def force_profile_wnd_refresh(hwnd_card):
    try:
        WM_GETOBJECT = 0x003D
        OBJID_CLIENT = -4
        UIA_ROOT_OBJECT_ID = -25
        ctypes.windll.user32.SendMessageW(hwnd_card, WM_GETOBJECT, 0, UIA_ROOT_OBJECT_ID)
        ctypes.windll.user32.SendMessageW(hwnd_card, WM_GETOBJECT, 0, OBJID_CLIENT)
        rect_w = win32gui.GetWindowRect(hwnd_card)
        x_w, y_w = rect_w[0], rect_w[1]
        w_w = rect_w[2] - rect_w[0]
        h_w = rect_w[3] - rect_w[1]
        if w_w > 100 and h_w > 100:
            win32gui.MoveWindow(hwnd_card, x_w, y_w, w_w + 1, h_w + 1, True)
            time.sleep(0.05)
            win32gui.MoveWindow(hwnd_card, x_w, y_w, w_w, h_w, True)
    except Exception as e_rf:
        print(f"[UIA] 专属强刷资料卡窗口异常: {e_rf}")

def wait_profile_tree_ready(info_win_hwnd, info_win, uia_lock):
    """等待个人资料树的控件渲染就绪"""
    try:
        info_win.SetFocus()
    except Exception:
        pass
    time.sleep(0.3)

    try:
        SPI_SETSCREENREADER = 0x0047
        ctypes.windll.user32.SystemParametersInfoW(SPI_SETSCREENREADER, True, None, 2)
    except Exception:
        pass

    force_profile_wnd_refresh(info_win_hwnd)

    for _wait_tree in range(30):
        uia_lock.update_status(f"正在等待微信个人资料树渲染数据 ({_wait_tree + 1}/30)...")
        try:
            fresh_win = uia.ControlFromHandle(info_win_hwnd)
            if fresh_win:
                fresh_win.Refetch()
            
            if not fresh_win or len(fresh_win.GetChildren()) == 0:
                force_profile_wnd_refresh(info_win_hwnd)
                fresh_win = uia.ControlFromHandle(info_win_hwnd)
                if fresh_win:
                    fresh_win.Refetch()

            if fresh_win and len(fresh_win.GetChildren()) > 0:
                info_win = fresh_win
                print(f"[UIA] 资料窗口无障碍树渲染成功，检测到子节点数量: {len(info_win.GetChildren())}")
                break
        except Exception:
            pass
        time.sleep(0.1)
    return info_win

def scan_profile_fields(info_win, info_win_hwnd, uia_lock) -> tuple:
    """使用 BFS 扫描资料卡中的信息"""
    nickname, wxid, head_view = '', '', None
    for _bfs_attempt in range(4):
        uia_lock.update_status(f"正在扫描解析获取昵称与微信号 (轮次 {_bfs_attempt + 1}/4)...")
        if _bfs_attempt > 0:
            print(f"[UIA] BFS 第 {_bfs_attempt} 轮未完全提取到数据 (昵称={nickname!r}, 微信号={wxid!r})，等待后重试...")
            nickname = ''
            wxid = ''
            time.sleep(1.0)
            force_profile_wnd_refresh(info_win_hwnd)

        try:
            fresh_win = uia.ControlFromHandle(info_win_hwnd)
            if fresh_win:
                fresh_win.Refetch()
            if not fresh_win or len(fresh_win.GetChildren()) == 0:
                force_profile_wnd_refresh(info_win_hwnd)
                fresh_win = uia.ControlFromHandle(info_win_hwnd)
                if fresh_win:
                    fresh_win.Refetch()
            if fresh_win:
                info_win = fresh_win
        except Exception:
            pass

        queue = [(info_win, 0)]
        max_depth = 8
        count = 0
        visited = set()

        try:
            init_id = tuple(info_win.GetRuntimeId())
        except Exception:
            init_id = id(info_win)
        visited.add(init_id)

        while queue:
            ctrl, depth = queue.pop(0)
            count += 1
            if count > 300:
                break

            try:
                ctrl_name = getattr(ctrl, 'Name', '') or ''
                ctrl_type = getattr(ctrl, 'ControlTypeName', '') or ''
                ctrl_cls = getattr(ctrl, 'ClassName', '') or ''

                if ctrl_type == 'TextControl' and ctrl_name in ('微信号：', '微信号:'):
                    next_ctrl = ctrl.GetNextSiblingControl()
                    if next_ctrl:
                        w_val = getattr(next_ctrl, 'Name', '') or ''
                        w_val = w_val.strip()
                        if w_val:
                            wxid = w_val

                if not wxid and ('微信号：' in ctrl_name or '微信号:' in ctrl_name):
                    w_val = ctrl_name.replace('微信号：', '').replace('微信号:', '').strip()
                    if w_val:
                        wxid = w_val

                if ctrl_type == 'TextControl' and ctrl_name:
                    if (not nickname and
                            len(ctrl_name) < 30 and
                            ctrl_name not in ('微信', 'Weixin', '导航', '', '朋友圈',
                                              '收藏', '通讯录', '聊天信息', '搜索',
                                              '更多', '设置', '视频号', '小程序',
                                              '发消息', '拍一拍', '朋友权限',
                                              '标签', '备注', '来源') and
                            '微信号' not in ctrl_name and
                            '地区' not in ctrl_name and
                            '条未读' not in ctrl_name and
                            not ctrl_name.endswith(':') and
                            not ctrl_name.endswith('：') and
                            '(O)' not in ctrl_name and
                            '(&' not in ctrl_name and
                            ctrl_name not in ('打开', '关闭', '复制', '粘贴', '删除',
                                              '剪切', '全选', '撤销', '重做') and
                            not any(k in ctrl_name for k in ('创建此任务', '管理权限', '管理员', 'Sandboxie', '沙箱', '以管理员', '提升权限', '运行此程序'))):
                        nickname = ctrl_name

                if ctrl_cls == 'mmui::ContactHeadView':
                    head_view = ctrl

                if depth < max_depth:
                    try:
                        children = ctrl.GetChildren()
                        for child in children:
                            try:
                                child_id = tuple(child.GetRuntimeId())
                            except Exception:
                                child_id = id(child)
                            if child_id not in visited:
                                visited.add(child_id)
                                queue.append((child, depth + 1))
                    except Exception:
                        pass
            except Exception:
                continue

        print(f"[UIA] BFS 第 {_bfs_attempt + 1} 轮遍历完成: 昵称={nickname!r}, 微信号={wxid!r} (控件数={count})")

        if nickname and wxid:
            uia_lock.update_status(f"成功读取基础数据：昵称='{nickname}'，微信号='{wxid}'")
            break

    return nickname, wxid, head_view

def download_avatar_flow(driver_obj, info_win, info_win_hwnd, head_view, skip_avatar_if_exists, nickname, wxid, uia_lock):
    """查找、下载高清头像大图流程"""
    from .preview_helpers import download_avatar_from_head_view
    from src.crm.account_data import ACCOUNTS_DIR

    if not head_view:
        print("[UIA] BFS 未找到头像控件，启动 WalkControl 搜索...")
        count = 0
        try:
            for ctrl, _ in uia.WalkControl(info_win, maxDepth=6):
                count += 1
                if count > 300:
                    break
                try:
                    if (getattr(ctrl, 'ClassName', '') or '') == 'mmui::ContactHeadView':
                        head_view = ctrl
                        print("[UIA] 成功找到头像控件!")
                        break
                except Exception:
                    continue
        except Exception as ex:
            print(f"[UIA] 搜索头像控件发生异常: {ex}")

    _should_skip_avatar = False
    if skip_avatar_if_exists and wxid:
        _cached = os.path.join(ACCOUNTS_DIR, f"{wxid}.png")
        if os.path.exists(_cached):
            _should_skip_avatar = True

    if _should_skip_avatar:
        uia_lock.update_status("本地已缓存个人头像，自动跳过下载。")
    elif head_view and wxid:
        uia_lock.update_status("正在下载并保存微信高清头像图片...")
        exclude = {info_win_hwnd} if info_win_hwnd else None
        avatar_path = download_avatar_from_head_view(
            head_view=head_view,
            wxid=wxid,
            main_hwnd=driver_obj.hwnd,
            exclude_hwnds=exclude
        )
        if avatar_path:
            uia_lock.update_status(f"高清头像提取成功：{os.path.basename(avatar_path)}")
            print(f"[UIA] 高清头像已保存: {avatar_path}")
            try:
                from src.uia.privacy_shield import get_privacy_shield
                get_privacy_shield().update_user_info(nickname or "", avatar_path)
            except Exception:
                pass
        else:
            print("[UIA] 头像提取失败或被跳过")
    elif not head_view:
        print("[UIA] 未找到头像控件")
    elif not wxid:
        print("[UIA] 微信号未提取到，跳过头像保存")

def close_profile_card(driver_obj, info_win_hwnd, target_x, target_y, uia_lock):
    """关闭个人资料卡"""
    if info_win_hwnd and win32gui.IsWindow(info_win_hwnd):
        uia_lock.update_status("个人信息获取完成，正在关闭个人资料卡...")
        _t_card_start = time.time()
        closed = False
        try:
            print(f"[UIA] 尝试向资料卡窗口发送标准的 WM_CLOSE 消息以关闭: hwnd={info_win_hwnd}")
            import win32con
            win32gui.PostMessage(info_win_hwnd, win32con.WM_CLOSE, 0, 0)
            for _ in range(10):
                time.sleep(0.05)
                if not win32gui.IsWindow(info_win_hwnd) or not win32gui.IsWindowVisible(info_win_hwnd):
                    closed = True
                    print("[UIA] 发送 WM_CLOSE 成功关闭资料卡窗口")
                    break
        except Exception as e:
            print(f"[UIA] 发送 WM_CLOSE 异常: {e}")

        if not closed and target_x > 0 and target_y > 0:
            try:
                from src.uia.retry.clicks import physical_click
                print("[UIA] 资料卡仍未关闭，尝试物理点击头像坐标以收起...")
                from src.uia.retry import ensure_wechat_foreground
                ensure_wechat_foreground(driver_obj.hwnd)
                if ctypes.windll.user32.GetForegroundWindow() == driver_obj.hwnd:
                    physical_click(target_x, target_y, settle=0.05, restore_cursor=False)
                    time.sleep(0.1)
                else:
                    print("[UIA] ⚠️ 微信未置前，跳过资料卡关闭的物理点击以防误触")
            except Exception as e:
                print(f"[UIA] 物理点击关闭资料卡异常: {e}")
        print(f"[UIA] 关闭资料卡耗时: {time.time() - _t_card_start:.2f}s")
