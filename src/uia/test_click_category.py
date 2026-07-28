import uiautomation as uia
import time

def test_click_category():
    categories = ["新的朋友", "群聊", "公众号", "服务号", "企业微信联系人", "我的企业", "联系人"]
    
    wechat = uia.WindowControl(ClassName="WeChatMainWndForPC")
    if not wechat.Exists(3):
        print("WeChat not found!")
        return
        
    wechat.SwitchToThisWindow()
    contacts_btn = wechat.ButtonControl(Name="通讯录")
    if contacts_btn.Exists(1):
        contacts_btn.Click()
    time.sleep(1)
        
    contacts_list = wechat.ListControl(ClassName="ContactList", searchDepth=4) or wechat.ListControl()
    if not contacts_list.Exists(3):
        print("ContactList not found!")
        return

    # Go to top
    try:
        scroll_ptn = contacts_list.GetScrollPattern()
        if scroll_ptn:
            scroll_ptn.SetScrollPercent(-1, 0.0)
    except Exception:
        pass
        
    contacts_list.SetFocus()
    uia.SendKeys('{HOME}')
    time.sleep(1)

    print("Found Children in ContactList:")
    items = contacts_list.GetChildren()
    item_names = [item.Name for item in items]
    print(item_names)

    for cat in categories:
        found = False
        for item in items:
            name = item.Name or ""
            if name.startswith(cat) or name == cat:
                found = True
                print(f"[{cat}] Found element: Name='{name}', ClassName='{item.ClassName}', ExpandCollapsePattern: {bool(item.GetExpandCollapsePattern())}")
                
                # Check collapse state
                ec_pattern = item.GetExpandCollapsePattern()
                if ec_pattern:
                    state = ec_pattern.ExpandCollapseState
                    print(f"  - ExpandCollapseState: {state} (0=Collapsed, 1=Expanded)")
                
                # Try clicking
                print(f"  - Attempting to click...")
                try:
                    item.Click()
                    print(f"  - Clicked successfully.")
                except Exception as e:
                    print(f"  - Click failed: {e}")
                time.sleep(1)
                break
        if not found:
            print(f"[{cat}] NOT FOUND IN TOP LIST")

if __name__ == "__main__":
    test_click_category()
