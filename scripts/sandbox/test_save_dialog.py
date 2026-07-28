import uiautomation as auto
import subprocess
import time

def test_save_dialog():
    subprocess.Popen(['notepad.exe'])
    time.sleep(1)
    notepad = auto.WindowControl(ClassName='Notepad')
    if not notepad.Exists(1):
         notepad = auto.WindowControl(Name='无标题 - 记事本')
    
    # open save dialog
    notepad.SendKeys('{Ctrl}s')
    time.sleep(1)
    
    dialog = auto.WindowControl(ClassName='#32770')
    if dialog.Exists(1):
         print("Found Save As Dialog")
         for edit in dialog.GetChildren():
             for e, d in auto.WalkControl(dialog, maxDepth=10):
                 if e.ControlTypeName == 'EditControl':
                     print(f"EditControl found: Name='{e.Name}', AutomationId='{e.AutomationId}', ClassName='{e.ClassName}'")
         dialog.SendKeys('{Esc}')
    
    notepad.SendKeys('{Alt}{F4}')

if __name__ == '__main__':
    test_save_dialog()
