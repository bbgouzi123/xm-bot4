import uiautomation as auto
import time
wechat = auto.WindowControl(ClassName='WeChatMainWndForPC')
if not wechat.Exists(1, 0):
    wechat = auto.WindowControl(Name='微信')

pop = wechat.WindowControl(ClassName='mmui::ProfileUniquePop')
print('Please ensure your WeChat window is open and the ProfileUniquePop is open.')
print('Clicking head view now...')

if not pop.Exists(1, 0):
    print("pop not found!")
else:
    head = pop.ButtonControl(ClassName='mmui::ContactHeadView', searchDepth=10)
    if head.Exists(1):
        head.Click(simulateMove=False)
        print("Clicked it! Waiting 2 seconds for Image Preview UI to load...")
        time.sleep(2)
        
        # Dump all mmui windows
        print("--- mmui Windows ---")
        for win in auto.GetRootControl().GetChildren():
            if win.ControlTypeName == 'WindowControl' and 'mmui' in win.ClassName:
                print(f"TopWindow: class={win.ClassName}, name={win.Name}")
                # check if this is the image preview by looking for buttons
                save_btn = win.ButtonControl(Name="另存为...")
                if not save_btn.Exists(0.1):
                    save_btn = win.ButtonControl(Name="保存")
                if not save_btn.Exists(0.1):
                    save_btn = win.ButtonControl(Name="下载")
                    
                if save_btn.Exists(0.1):
                    print(f"  --> FOUND SAVE BUTTON inside {win.ClassName}!")
                else:
                    # just list buttons
                    buttons = win.GetChildren()
                    for b in buttons:
                        if b.ControlTypeName == 'ButtonControl':
                            print(f"  btn: {b.Name}")
    else:
        print("Head view not found.")
