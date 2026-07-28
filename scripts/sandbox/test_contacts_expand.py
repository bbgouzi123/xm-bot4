import sys
import time
import uiautomation as uia

sys.stdout.reconfigure(encoding='utf-8')

def test_expand():
    print("====== Start Test ======")
    start_time = time.time()
    
    wechat = uia.WindowControl(ClassName='mmui::MainWindow')
    if not wechat.Exists(1):
        wechat = uia.WindowControl(ClassName='WeChatMainWndForPC')

    contacts_list = wechat.ListControl(ClassName="mmui::StickyHeaderRecyclerListView")
    if not contacts_list.Exists(1):
        print("contacts_list not found!")
        return

    print(f"[{time.time() - start_time:.2f}s] Window found, checking contacts_list...")

    # Optional: ensure we are at top
    scroll_ptn = contacts_list.GetScrollPattern()
    if scroll_ptn:
        scroll_ptn.SetScrollPercent(-1, 0.0)

    print(f"[{time.time() - start_time:.2f}s] Scrolled to top.")

    items = contacts_list.GetChildren()
    print(f"[{time.time() - start_time:.2f}s] Got {len(items)} children.")
    
    contact_item = None
    for item in items:
        name = (item.Name or "").strip()
        if name.startswith("联系人"):
            contact_item = item
            break
            
    if not contact_item:
        print("Contact group not found.")
        return

    print(f"[{time.time() - start_time:.2f}s] Found contact item: {contact_item.Name}")
    
    idx = items.index(contact_item)
    is_collapsed = (idx >= len(items) - 2)
    
    print(f"[{time.time() - start_time:.2f}s] Index: {idx}, Total: {len(items)} => is_collapsed: {is_collapsed}")
    
    if is_collapsed:
        print("Trying to click...")
        rect = contact_item.BoundingRectangle
        if not rect:
            print("No BoundingRectangle!")
            return
            
        x = (rect.left + rect.right) // 2
        y = (rect.top + rect.bottom) // 2
        print(f"[{time.time() - start_time:.2f}s] Computed physical center: ({x}, {y})")

        # Try UIA absolute click
        print("Method 1: uia.Click(x, y)")
        click_start = time.time()
        uia.Click(x, y)
        print(f"[{time.time() - start_time:.2f}s] uia.Click took {time.time() - click_start:.2f}s")
        
        time.sleep(1)
        new_items = contacts_list.GetChildren()
        print(f"After Method 1, Total items: {len(new_items)}")

        # Method 2: ctypes win32api click
        if len(new_items) <= idx + 2:
            print("Method 1 probably failed to expand. Trying Method 2 (Win32 API).")
            import win32api, win32con
            old = win32api.GetCursorPos()
            win32api.SetCursorPos((x, y))
            time.sleep(0.1)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            time.sleep(0.05)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            time.sleep(0.1)
            win32api.SetCursorPos(old)
            
            time.sleep(1)
            new_items_2 = contacts_list.GetChildren()
            print(f"After Method 2, Total items: {len(new_items_2)}")

if __name__ == "__main__":
    test_expand()
