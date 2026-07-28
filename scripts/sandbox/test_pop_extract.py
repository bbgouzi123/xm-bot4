import uiautomation as auto
import ctypes
import time
import sys
import codecs

def test():
    w = auto.ControlFromHandle(264954)
    if not w:
        print("Wechat not found via HWND")
        return
        
    pop = auto.WindowControl(ClassName='mmui::ProfileUniquePop')
    
    if pop.Exists(1, 0.5):
        try:
            OBJID_CLIENT = -4
            WM_GETOBJECT = 0x003D
            hwnd_popup = pop.NativeWindowHandle
            if hwnd_popup:
                ctypes.windll.user32.SendMessageW(hwnd_popup, WM_GETOBJECT, 0, OBJID_CLIENT)
                time.sleep(0.3)
                pop.Refind()
        except:
            pass

        real_nick_name = None
        wxid = None
        for c, d in auto.WalkControl(pop, maxDepth=12):
            if c.ClassName == 'mmui::ContactHeadView' and not real_nick_name:
                name_text = c.Name
                if name_text and name_text.strip():
                    real_nick_name = name_text.strip()
                    
            elif c.ControlTypeName == 'TextControl':
                text = c.Name
                if text and ("微信号：" in text or "微信号:" in text):
                    next_c = c.GetNextSiblingControl()
                    if next_c and next_c.Name:
                        wxid = next_c.Name.strip()
            
            if real_nick_name and wxid:
                break
                
        # output to console directly (avoiding unicode error)
        try:
            sys.stdout = codecs.getwriter("gbk")(sys.stdout.detach(), errors="replace")
        except:
            pass
            
        print(f"Extracted Nickname: {real_nick_name}")
        print(f"Extracted WXID: {wxid}")
        
    import os
    os._exit(0)

if __name__ == "__main__":
    test()
