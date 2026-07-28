import os
import sys

# 注入环境变量，确保能够刺透微信的 UI 树！
os.environ["QT_ACCESSIBILITY"] = "1"
try:
    import ctypes
    ctypes.windll.kernel32.SetEnvironmentVariableW("QT_ACCESSIBILITY", "1")
except Exception:
    pass

import uiautomation as uia

def dump_new_friends():
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:
        pass

    uia.SetGlobalSearchTimeout(5)
    wechat = uia.WindowControl(ClassName="WeChatMainWndForPC")
    if not wechat.Exists(3):
        print("未找到微信窗口，请确保微信已在前台打开并登录！")
        return
        
    contacts_list = wechat.ListControl(Name="联系人") or wechat.TreeControl(Name="联系人")
    if not contacts_list.Exists(3):
        print("未找到左侧联系人树！请在微信点击进入【通讯录】页面。")
        return
        
    print("正在打印【新的朋友】下的节点结构...")
    count = 0
    with open("dump_result.txt", "w", encoding="utf-8") as f:
        for item in contacts_list.GetChildren():
            name = (item.Name or "").strip()
            # "新的朋友" 下方的名片文字大概率包含联系人名或类似文字
            # 不直接判断 "新的朋友"，而是把前面几个元素统统拉出来
            if "群聊" in name:
                break
            
            if name and name != "新的朋友":
                f.write(f"\n[{count}] 根组件名称: '{name}'\n")
                f.write(f"    控件类型: {item.ControlTypeName}, 类名: {item.ClassName}\n")
                f.write("    ===== 子节点树 =====\n")
                
                # 递归打印内部结构
                for child, depth in uia.WalkControl(item, maxDepth=6):
                    if child == item:
                        continue
                    indent = "  " * depth
                    cname = (child.Name or "").strip()
                    ctype = child.ControlTypeName
                    cclass = child.ClassName
                    f.write(f"{indent}- [{ctype}] '{cname}' (Class: {cclass})\n")
                    
                count += 1
                if count >= 3:
                    break
                    
    print("\n✅ 测试脚本执行完毕！请把 backend-python/dump_result.txt 文件的内容发给我分析！")

if __name__ == "__main__":
    dump_new_friends()
