import uiautomation as auto
import time
import os

def test_capture():
    try:
        # Find WeChat Window
        hwnd = auto.WindowControl(ClassName='WeChatMainWndForPC')
        if not hwnd.Exists(1, 0):
            hwnd = auto.WindowControl(Name='微信')

        if not hwnd.Exists(1, 0):
            print("Wechat window not found.")
            return
        
        print("Wechat window found.")

        # Find Profile Pop-up
        info_window = auto.WindowControl(ClassName="mmui::ProfileUniquePop")
        if not info_window.Exists(1, 1):
            print("Please open the profile pop-up (click on your avatar). Waiting 5 seconds...")
            time.sleep(5)
            
        if info_window.Exists(1, 1):
            import ctypes
            ctypes.windll.user32.SendMessageW(info_window.NativeWindowHandle, 0x003D, 0, -4)
            time.sleep(0.3)
            info_window.Refind()
            
            head_btn = info_window.ButtonControl(ClassName='mmui::ContactHeadView', searchDepth=10)
            if head_btn.Exists(1):
                name = head_btn.Name
                print(f"Found head view for: {name}")
                
                os.makedirs("test_avatars", exist_ok=True)
                path = os.path.abspath(f"test_avatars/avatar.png")
                # Need to use standard method for bounded screenshot
                head_btn.CaptureToImage(path)
                print(f"Captured to {path}")
            else:
                print("ContactHeadView not found.")
        else:
            print("Profile pop-up still not found.")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_capture()
