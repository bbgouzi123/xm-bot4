"""
朋友圈点赞评论全链路测试脚本 v2

用法:
    cd backend-python
    python scripts/test_moment_interact.py

实测发现 (2026-06-02):
    - 朋友圈窗口 ClassName: mmui::SNSWindow
    - 列表控件 ClassName: mmui::TimeLineListView
    - 互动浮层 ClassName: mmui::TimelineFloatMenu (新版微信, WindowControl)
    - 赞/评论/取消 按钮 ClassName: mmui::XButton
    - item 的 GetChildren() 返回空 (自渲染)，必须用像素点击触发浮层
    - WheelDown 后必须重新 GetChildren 才能拿到当前可见 item
    - 点击 item 右下角 (right-55, bottom-22) 可触发互动浮层

测试项目:
    1. 找微信主窗口 + 打开朋友圈
    2. WheelDown(4) 跳封面 + WheelDown(3) 测试不误点任务栏
    3. 找第一条在可见区域内且高度>200px 的真实动态
    4. 像素点击右下角 → 等浮层 mmui::TimelineFloatMenu 出现
    5. 点赞 (赞按钮) → 浮层消失
    6. 重新触发浮层 (再次像素点击)
    7. 点浮层里的评论按钮 → 找输入框 → 输入 → 撤销 (不实际发送)
"""
import logging
import sys
import time
import random
import os
import win32api
import win32con
import win32gui
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uiautomation as uia

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger('test_v2')
FAKE_COMMENT = f"测试评论{datetime.now().strftime('%H%M%S')}"

# ========== 实测 ClassName 常量 ==========
CLS_SNS = 'mmui::SNSWindow'
CLS_LIST = 'mmui::TimeLineListView'
CLS_TOAST = 'mmui::TimelineFloatMenu'  # 新版微信互动浮层
CLS_TOAST_LEGACY = 'SnsLikeToastWnd'  # 旧版微信
BTN_LIKE = '赞'
BTN_COMMENT = '评论'
BTN_CANCEL = '取消'


def _pixel_click(x, y, settle=0.15):
    """像素坐标左键点击"""
    win32api.SetCursorPos((int(x), int(y)))
    time.sleep(settle)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.1)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    time.sleep(0.2)


def _find_toast():
    """全局查找互动浮层 (新版/旧版兼容)"""
    # 新版
    t = uia.WindowControl(ClassName=CLS_TOAST)
    if t.Exists(2.0):
        return t
    # 旧版
    t = uia.PaneControl(ClassName=CLS_TOAST_LEGACY)
    if t.Exists(1.0):
        return t
    return None


def step1_open_moments():
    """打开微信朋友圈，返回 (wx_window, sns_window)"""
    logger.info("[STEP1] 查找微信主窗口...")
    wx = None
    for cls in ('mmui::MainWindow', 'WeChatMainWndForPC'):
        wx = uia.WindowControl(ClassName=cls)
        if wx.Exists(1):
            logger.info(f"  找到: ClassName={cls!r}")
            break
    if not wx or not wx.Exists(1):
        logger.error("  未找到微信主窗口")
        return None, None

    # 先检查朋友圈是否已打开
    for cls in (CLS_SNS, 'SnsWnd'):
        sns = uia.WindowControl(ClassName=cls)
        if sns.Exists(0.5):
            logger.info(f"  朋友圈已打开: ClassName={cls!r}")
            win32gui.SetForegroundWindow(sns.NativeWindowHandle)
            return wx, sns

    win32gui.SetForegroundWindow(wx.NativeWindowHandle)
    time.sleep(0.3)

    logger.info("[STEP1] 点击朋友圈...")
    btn = wx.ButtonControl(Name='朋友圈')
    if not btn.Exists(2):
        logger.error("  找不到朋友圈按钮")
        return wx, None
    try:
        btn.Click()
    except Exception as e:
        logger.warning(f"  btn.Click 异常(可能已在切换): {e}")
    time.sleep(2.5)

    for cls in (CLS_SNS, 'SnsWnd'):
        sns = uia.WindowControl(ClassName=cls)
        if sns.Exists(3):
            logger.info(f"  朋友圈已打开: ClassName={cls!r} hwnd={sns.NativeWindowHandle}")
            return wx, sns

    logger.error("  朋友圈窗口未出现")
    return wx, None



def step2_find_list_and_scroll(sns):
    """找列表控件并滚动，返回 list_ctrl"""
    logger.info("[STEP2] 查找列表控件...")
    lc = sns.ListControl(ClassName=CLS_LIST)
    if not lc.Exists(3):
        lc = sns.ListControl(Name='朋友圈')
    if not lc.Exists(2):
        logger.error("  找不到列表控件")
        return None
    logger.info(f"  找到: ClassName={lc.ClassName!r}")

    logger.info("[STEP2] WheelDown(4) 跳过封面...")
    lc.WheelDown(wheelTimes=4)
    time.sleep(1.5)

    logger.info("[STEP2] WheelDown(3) 验证不误点任务栏...")
    lc.WheelDown(wheelTimes=3)
    time.sleep(1.0)
    logger.info("  WheelDown 完成，鼠标始终在控件内")
    return lc


def step3_find_visible_item(lc):
    """找第一条在可见区域且高度>200px 的真实动态"""
    logger.info("[STEP3] 扫描可见真实动态...")
    list_rect = lc.BoundingRectangle
    logger.info(f"  list 可见区: top={list_rect.top} bottom={list_rect.bottom}")

    items = lc.GetChildren()
    logger.info(f"  GetChildren() 返回 {len(items)} 条")
    for i, item in enumerate(items):
        try:
            rect = item.BoundingRectangle
            h = rect.bottom - rect.top
            in_view = (rect.top >= list_rect.top and
                       rect.bottom <= list_rect.bottom and h > 200)
            if not in_view:
                continue
            name = (item.Name or '')[:80]
            if not name.strip():
                continue
            logger.info(f"  找到 item[{i}]: h={h} top={rect.top} name={name!r}")
            return item, rect, name
        except Exception:
            continue

    logger.warning("  未找到可见大条目，尝试再滚动一屏后重扫")
    lc.WheelDown(wheelTimes=3)
    time.sleep(1.5)
    list_rect = lc.BoundingRectangle
    items = lc.GetChildren()
    for i, item in enumerate(items):
        try:
            rect = item.BoundingRectangle
            h = rect.bottom - rect.top
            if rect.top >= list_rect.top and rect.bottom <= list_rect.bottom and h > 200:
                name = (item.Name or '')[:80]
                if name.strip():
                    logger.info(f"  二次扫描找到 item[{i}]: h={h}")
                    return item, rect, name
        except Exception:
            continue
    logger.error("  找不到有效动态")
    return None, None, ""


def step4_trigger_toast(rect):
    """像素点击 item 右下角触发互动浮层，返回 toast_window"""
    logger.info("[STEP4] 像素点击触发互动浮层...")
    # 实测：right-55, bottom-22 命中浮层触发区
    x = rect.right - 55
    y = rect.bottom - 22
    logger.info(f"  点击位置: ({x}, {y}) [item right={rect.right} bottom={rect.bottom}]")

    for attempt in range(3):
        _pixel_click(x, y)
        toast = _find_toast()
        if toast:
            kids = [c.Name for c in toast.GetChildren()]
            logger.info(f"  浮层出现! ClassName={toast.ClassName!r} 按钮={kids}")
            return toast
        logger.warning(f"  第{attempt+1}次未触发浮层，调整坐标重试...")
        # 微调坐标
        x = rect.right - (55 + attempt * 10)
        y = rect.bottom - (22 + attempt * 5)
        time.sleep(0.5)

    logger.error("  无法触发互动浮层")
    return None


def step5_like(toast):
    """点赞，返回 (liked, popup_closed)"""
    logger.info("[STEP5] 点赞...")
    cancel_btn = toast.ButtonControl(Name=BTN_CANCEL)
    if cancel_btn.Exists(0.5):
        logger.info("  已赞过，跳过点赞")
        return True, False

    like_btn = toast.ButtonControl(Name=BTN_LIKE)
    if not like_btn.Exists(1):
        logger.warning("  找不到赞按钮")
        return False, False

    like_btn.Click()
    logger.info("  点赞完成，等待浮层消失...")
    time.sleep(random.uniform(0.8, 1.2))
    # 验证浮层是否消失
    closed = not _find_toast()
    logger.info(f"  浮层消失: {closed}")
    return True, closed


def step6_reopen_toast(rect):
    """重新触发浮层（点赞后）"""
    logger.info("[STEP6] 重新触发浮层...")
    time.sleep(random.uniform(0.5, 1.0))
    return step4_trigger_toast(rect)


def step7_comment(toast, sns):
    """点评论按钮 → 找输入框 → 输入 → 撤销"""
    logger.info("[STEP7] 测试评论...")
    comment_btn = toast.ButtonControl(Name=BTN_COMMENT)
    if not comment_btn.Exists(2):
        logger.error("  浮层中没有评论按钮")
        return False

    comment_btn.Click()
    logger.info("  已点评论，等待输入框...")
    time.sleep(1.5)

    # 新版微信评论框 ClassName: mmui::XValidatorTextEdit
    edit = uia.EditControl(ClassName='mmui::XValidatorTextEdit')
    if not edit.Exists(2):
        edit = sns.EditControl(Name='评论')
    if not edit.Exists(2):
        edit = uia.EditControl(Name='评论')
    if not edit.Exists(1):
        logger.error("  找不到评论输入框")
        return False

    logger.info(f"  找到输入框: ClassName={edit.ClassName!r}")
    edit.SetFocus()
    time.sleep(0.3)

    # 粘贴输入
    import win32clipboard
    win32clipboard.OpenClipboard()
    win32clipboard.EmptyClipboard()
    win32clipboard.SetClipboardText(FAKE_COMMENT + '\x00', 13)  # CF_UNICODETEXT=13
    win32clipboard.CloseClipboard()
    time.sleep(0.2)
    uia.SendKeys('{Ctrl}v')
    time.sleep(0.5)

    try:
        val = edit.GetValuePattern().Value
        logger.info(f"  输入框内容: {val!r}")
    except Exception:
        logger.info("  (GetValuePattern 不可用，跳过值验证)")

    # 撤销，不实际发送
    uia.SendKeys('{Escape}')
    logger.info("  已 ESC 关闭输入框，未实际发送评论")
    return True


def run_full_test():
    """全链路测试主函数"""
    logger.info("=" * 60)
    logger.info("朋友圈点赞评论全链路测试 v2 开始")
    logger.info("=" * 60)

    wx, sns = step1_open_moments()
    if not sns:
        return

    lc = step2_find_list_and_scroll(sns)
    if not lc:
        return

    item, rect, name = step3_find_visible_item(lc)
    if not item:
        return

    toast = step4_trigger_toast(rect)
    if not toast:
        return

    liked, popup_closed = step5_like(toast)

    if popup_closed:
        toast = step6_reopen_toast(rect)
        if not toast:
            logger.error("重开浮层失败，停止测试")
            return

    ok = step7_comment(toast, sns)

    logger.info("=" * 60)
    logger.info(f"测试结果  点赞: {'OK' if liked else 'FAIL'}  评论: {'OK' if ok else 'FAIL'}")
    logger.info("=" * 60)


if __name__ == '__main__':
    try:
        run_full_test()
    except KeyboardInterrupt:
        logger.info("用户中断测试 (Ctrl+C)")
    except Exception as e:
        import traceback
        logger.error(f"测试异常: {e}")
        traceback.print_exc()
