import uiautomation as uia
import time

def test():
    wechat = uia.WindowControl(ClassName="WeChatMainWndForPC")
    if not wechat.Exists(3):
        print("Wechat not open")
        return
    wechat.SwitchToThisWindow()

    # Find contact list
    contacts_list = wechat.ListControl(ClassName="ContactList", searchDepth=4) or wechat.ListControl()
    if not contacts_list.Exists(3):
        print("List not found")
        return

    items = contacts_list.GetChildren()
    for item in items:
        name = item.Name or ""
        if name and "联系" not in name and len(name) > 1:
            print(f"Found {name}")
            # Try to find avatar
            avatar = item.ImageControl()
            if not avatar.Exists(0):
                avatar = item.ButtonControl()
            if avatar.Exists(0):
                print(f"Capturing avatar for {name} to {name}.png...")
                try:
                    avatar.CaptureToImage(f"{name}.png")
                    print("Success!")
                except Exception as e:
                    print(f"Failed: {e}")
            break

if __name__ == "__main__":
    test()
