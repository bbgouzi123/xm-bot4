import uiautomation as uia
import sys

def main():
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:
        pass

    wechat_win = uia.WindowControl(ClassName="WeChatMainWndForPC")

    if not wechat_win:
        print("WeChat window not found")
        return

    contacts_list = wechat_win.ListControl(Name="联系人")
    if not contacts_list.Exists(3):
        contacts_list = wechat_win.TreeControl(Name="联系人")
        if not contacts_list.Exists(1):
            print("Contacts list not found")
            return

    print("Found contacts list. Dumping...")
    try:
        items = contacts_list.GetChildren()
    except Exception as e:
        print(f"Error getting children: {e}")
        return

    found = False
    count = 0
    for item in items:
        try:
            name = (item.Name or "").strip()
            if "新的朋友" in name:
                found = True
            
            if found:
                print(f"\n[{count}] ITEM: '{name}'")
                print(f"  Type: {item.ControlTypeName}, Class: {item.ClassName}")
                for child, depth in uia.WalkControl(item, maxDepth=5):
                    cname = (child.Name or "").strip()
                    ctype = child.ControlTypeName
                    cclass = child.ClassName
                    indent = "  " * (depth + 1)
                    # print details
                    print(f"{indent}- [{ctype}] (Class: {cclass}) Name: '{cname}'")
                    
                count += 1
                if count >= 4:
                     break
        except Exception as e:
            print(f"Item error: {e}")

if __name__ == "__main__":
    main()
