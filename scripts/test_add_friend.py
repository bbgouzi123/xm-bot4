import logging
import sys
import time
import os
import win32gui

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger('test_add_friend')

# 模拟 driver 接口
class MockDriver:
    def __init__(self, hwnd):
        self.hwnd = hwnd

    def is_connected(self):
        return True

    def SwitchToThisWindow(self):
        try:
            import win32gui
            win32gui.ShowWindow(self.hwnd, 1) # SW_SHOWNORMAL
            win32gui.SetForegroundWindow(self.hwnd)
        except Exception as e:
            logger.warning(f"SwitchToThisWindow exception: {e}")

def find_wechat_hwnd():
    results = []
    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            cls = win32gui.GetClassName(hwnd)
            if title == "微信" or cls in ('mmui::MainWindow', 'WeChatMainWndForPC', 'Qt51514QWindowIcon'):
                results.append(hwnd)
    win32gui.EnumWindows(cb, None)
    return results[0] if results else None

def run_test():
    hwnd = find_wechat_hwnd()
    if not hwnd:
        logger.error("未找到微信主窗口，请先打开微信并登录！")
        return

    logger.info(f"找到微信窗口: hwnd={hwnd}")
    driver = MockDriver(hwnd)
    
    from src.uia.add_friend import AddFriendEngine
    engine = AddFriendEngine(driver)
    
    target_num = "13800000000"
    logger.info(f"开始测试添加好友：{target_num}")
    res = engine.add_new_friend(
        wxid=target_num,
        remark="测试RPA备注",
        tags="测试标签1",
        verify_message="您好，我是系统自动获客机器人测试"
    )
    logger.info(f"加好友测试执行结果: {res}")

if __name__ == '__main__':
    run_test()
