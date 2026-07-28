import uiautomation as auto
import time

def test_avatar_download():
    wechat = auto.WindowControl(ClassName='WeChatMainWndForPC')
    if not wechat.Exists(1):
        wechat = auto.WindowControl(Name='微信')
    
    pop = wechat.WindowControl(ClassName='mmui::ProfileUniquePop')
    if not pop.Exists(1):
        print("Please open the profile popup first!")
        return
        
    head_btn = pop.ButtonControl(ClassName='mmui::ContactHeadView', searchDepth=10)
    if head_btn.Exists(1):
        print("Found head_btn, clicking it...")
        head_btn.Click()
        time.sleep(1)
        
        # Now find the newly opened image preview window
        # In WeChat, this is usually a separate WindowControl with ClassName 'ImagePreviewWnd' or similar
        print("Finding preview window...")
        preview = auto.WindowControl(ClassName='ImagePreviewWnd')
        if not preview.Exists(1):
            # maybe it's something else
            print("ImagePreviewWnd not found, dumping all top-level windows...")
            for win in auto.GetRootControl().GetChildren():
                if win.ControlTypeName == 'WindowControl' and 'Image' in win.ClassName or 'Preview' in win.ClassName or 'mmui' in win.ClassName:
                    print(f"Candidate: Name={win.Name}, Class={win.ClassName}")
        else:
            print("Found ImagePreviewWnd!")
            # Find download button (usually Name="另存为..." or "下载" or "保存")
            save_btn = preview.ButtonControl(Name='另存为...')
            if not save_btn.Exists(1):
                 save_btn = preview.ButtonControl(Name='保存')
            
            if save_btn.Exists(1):
                print("Found Save button!")
            else:
                print("Save button not found.")
                
            preview.SendKeys('{Esc}')
    else:
        print("head_btn not found")

if __name__ == "__main__":
    test_avatar_download()
