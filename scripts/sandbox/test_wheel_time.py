import sys
import time
import uiautomation as uia

sys.stdout.reconfigure(encoding='utf-8')

wechat = uia.WindowControl(ClassName='mmui::MainWindow')
contacts_list = wechat.ListControl(ClassName="mmui::StickyHeaderRecyclerListView")

print("Start WheelUp test...")
t0 = time.time()
for i in range(15):
    t_wheel = time.time()
    contacts_list.WheelUp(wheelTimes=12)
    print(f"WheelUp {i} took {time.time() - t_wheel:.3f}s")
    time.sleep(0.05)
print(f"Total time for WheelUp loop: {time.time() - t0:.2f}s")
