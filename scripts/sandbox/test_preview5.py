import uiautomation as auto
import time
import sys, codecs

sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')

wechat = auto.WindowControl(ClassName='WeChatMainWndForPC')
if not wechat.Exists(1, 0):
    wechat = auto.WindowControl(Name='微信')

pop = wechat.WindowControl(ClassName='mmui::ProfileUniquePop')
print('Clicking head view...')
head = pop.ButtonControl(ClassName='mmui::ContactHeadView', searchDepth=10)
if head.Exists(1):
    head.Click()
    time.sleep(1)
    
    print("Enumerating to find preview window...")
    preview_win = None
    for win in auto.GetRootControl().GetChildren():
        if win.ControlTypeName == 'WindowControl' and ('Image' in win.ClassName or 'Preview' in win.ClassName or 'ContactProfile' in win.ClassName):
            print(f"TopWindow: class={win.ClassName}, name={win.Name}")
            preview_win = win
            break
            
    if preview_win and preview_win.Exists(1, 0):
        print(f'Found preview window: {preview_win.Name}')
        import ctypes
        ctypes.windll.user32.SendMessageW(preview_win.NativeWindowHandle, 0x003D, 0, -4)
        time.sleep(0.3)
        preview_win.Refind()
        
        for c, d in auto.WalkControl(preview_win, maxDepth=10):
            if c.ControlTypeName == 'ButtonControl':
                print(f'[BTN] {c.Name}')
        
        save_btn = preview_win.ButtonControl(Name='另存为...')
        if not save_btn.Exists(0.1):
            save_btn = preview_win.ButtonControl(Name='保存')
        if not save_btn.Exists(0.1):
            save_btn = preview_win.ButtonControl(Name='下载')
            
        if save_btn.Exists(0.1):
             print(f'FOUND SAVE BTN: {save_btn.Name}')
             save_btn.Click()
             time.sleep(1)
             
             # save dialog is usually `#32770` with Name "另存为" or "另存为..."
             dialog = auto.WindowControl(Name='另存为...')
             if dialog.Exists(1):
                 print('save dialog found!')
                 dialog.SendKeys('{Esc}')
        else:
            print("SAVE BTN NOT FOUND.")
        
        preview_win.SendKeys('{Esc}')
    else:
        print("No preview window")
        
    pop.SendKeys('{Esc}')
else:
    print("Head not found")
