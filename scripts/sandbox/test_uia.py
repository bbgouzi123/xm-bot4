import sys
import uiautomation as uia

sys.stdout.reconfigure(encoding='utf-8')

wechat = uia.WindowControl(ClassName='mmui::MainWindow')
if not wechat.Exists(1):
    wechat = uia.WindowControl(ClassName='WeChatMainWndForPC')

contacts_list = wechat.ListControl(ClassName="mmui::StickyHeaderRecyclerListView")
if contacts_list.Exists(1):
    scroll_ptn = contacts_list.GetScrollPattern()
    if scroll_ptn:
        scroll_ptn.SetScrollPercent(-1, 0.0)

    items = contacts_list.GetChildren()
    for item in items:
        name = (item.Name or "").strip()
        if name.startswith("联系人"):
            print(f"Found {name}")
            print(f"Index: {items.index(item)}, Total: {len(items)}")
            break
