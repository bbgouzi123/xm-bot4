import uiautomation as auto
import win32api
import time

def extract_wechat_from_overflow() -> bool:
    """
    检查系统托盘小箭头（溢出区），如果微信在里面，则将其拖拽到主托盘上。
    仅适用于 Windows 10 环境。Win11 没有传统的折叠小箭头机制，直接跳过。
    """
    # Win11 没有传统的折叠小箭头（chevron）和 NotifyIconOverflowWindow
    try:
        import platform
        build = int(platform.version().split('.')[2])
        if build >= 22000:
            print("[托盘拖拽] Win11 环境，无传统折叠小箭头机制，跳过。")
            return False
    except Exception:
        pass
    try:
        # 1. 查找任务栏和系统托盘
        taskbar = auto.PaneControl(ClassName="Shell_TrayWnd")
        if not taskbar.Exists(1, 0.5):
            return False

        tray_notify = taskbar.PaneControl(ClassName="TrayNotifyWnd")
        if not tray_notify.Exists(1, 0.5):
            return False

        # 2. 查找折叠小箭头 (Windows 10 的折叠箭头通常是 Button)
        chevron = tray_notify.ButtonControl(ClassName="Button")
        if not chevron.Exists(1, 0.2):
            print("[托盘拖拽] 未找到折叠小箭头，忽略。")
            return False

        # 点击打开溢出区
        chevron.Click()
        time.sleep(0.5)

        # 3. 查找溢出区域窗口
        overflow = auto.PaneControl(ClassName="NotifyIconOverflowWindow")
        if not overflow.Exists(2, 0.5):
            print("[托盘拖拽] 找不到溢出区窗口。")
            # 再次点击 chevron 尝试收起
            chevron.Click()
            return False

        # 4. 在溢出区中寻找微信图标
        wechat_btn = None
        for ctrl, _ in auto.WalkControl(overflow, maxDepth=10):
            cn = getattr(ctrl, "Name", "") or ""
            # 注意：某些系统上名字包含微信
            if "微信" in cn or "WeChat" in cn:
                wechat_btn = ctrl
                break

        if not wechat_btn:
            print("[托盘拖拽] 折叠区内未发现微信图标。")
            # 没找到，收起溢出区
            chevron.Click()
            return False

        print("[托盘拖拽] 发现微信被折叠，正在将其拖拽到主托盘...")

        # 5. 找到主托盘的一个安全放置位置（主托盘的工具栏）
        main_toolbar = tray_notify.ToolBarControl(ClassName="ToolbarWindow32")
        if main_toolbar.Exists(1, 0.1):
            target_rect = main_toolbar.BoundingRectangle
            drop_x = (target_rect.left + target_rect.right) // 2
            drop_y = (target_rect.top + target_rect.bottom) // 2
        else:
            # 备用方案，放托盘中间
            target_rect = tray_notify.BoundingRectangle
            drop_x = (target_rect.left + target_rect.right) // 2
            drop_y = (target_rect.top + target_rect.bottom) // 2

        # 6. 执行拖拽 (从微信图标位置拖拽到主托盘区域)
        wechat_rect = wechat_btn.BoundingRectangle
        start_x = (wechat_rect.left + wechat_rect.right) // 2
        start_y = (wechat_rect.top + wechat_rect.bottom) // 2

        old_pos = win32api.GetCursorPos()
        
        # 使用 auto.DragDrop 执行慢速平滑拖拽，Windows 能够识别为图标移动
        auto.DragDrop(start_x, start_y, drop_x, drop_y, moveSpeed=1.0)
        
        print("[托盘拖拽] 拖拽完成！")
        time.sleep(0.5)

        # 将鼠标恢复
        win32api.SetCursorPos(old_pos)

        # 确保溢出区已收起，如果没有自动收起（拖拽后可能没收起），再点一下 chevron
        if overflow.Exists(0.5, 0.1) and chevron.Exists(0.5, 0.1):
            chevron.Click()

        return True

    except Exception as e:
        print(f"[托盘拖拽] 执行异常: {e}")
        return False
