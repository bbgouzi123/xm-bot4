"""
多框架对比诊断 — 找出哪个 UIA 客户端能正常工作
================================================

1. uiautomation 库 (当前用的)
2. pywinauto UIA backend
3. comtypes 直接 IUIAutomation 接口（带正确的 QueryInterface）
4. 系统 Inspect.exe 路径提示
"""
import sys
import time
import ctypes
import os

sys.stdout.reconfigure(encoding="utf-8")

def log(msg):
    print(f"[对比] {msg}")

def section(title):
    print(f"\n{'='*60}\n  {title}\n{'='*60}")


# 找微信窗口
import win32gui
wechat_windows = []
def _cb(hwnd, _):
    try:
        cls = win32gui.GetClassName(hwnd)
        title = win32gui.GetWindowText(hwnd)
        if cls == "Qt51514QWindowIcon" and title in ("微信", "Weixin", "WeChat"):
            if win32gui.IsWindowVisible(hwnd):
                r = win32gui.GetWindowRect(hwnd)
                w, h = r[2] - r[0], r[3] - r[1]
                wechat_windows.append((hwnd, w, h))
    except Exception:
        pass
win32gui.EnumWindows(_cb, None)
if not wechat_windows:
    log("❌ 未找到微信窗口")
    sys.exit(1)
main_hwnd = max(wechat_windows, key=lambda x: x[1]*x[2])[0]
log(f"微信 hwnd={main_hwnd}")


# ═══════════════════════════════════════════════
# 方案 A: pywinauto UIA backend
# ═══════════════════════════════════════════════
section("方案 A: pywinauto UIA backend")
try:
    from pywinauto import Desktop, Application
    from pywinauto.uia_defines import IUIA

    # 直接通过 hwnd 连接
    app = Application(backend="uia").connect(handle=main_hwnd)
    win = app.window(handle=main_hwnd)

    log(f"窗口: {win.window_text()}")
    log(f"控件类型: {win.element_info.control_type}")

    children = win.children()
    log(f"子控件数: {len(children)}")
    for i, child in enumerate(children[:15]):
        ct = child.element_info.control_type
        cn = child.window_text()
        aid = child.element_info.automation_id
        log(f"  [{i}] type={ct} name='{cn}' aid='{aid}'")

    # 更深层级
    if len(children) > 1:
        log("✅ pywinauto 可以看到控件树！")
        # 尝试找导航栏
        try:
            toolbar = win.child_window(auto_id="main_tabbar")
            if toolbar.exists(timeout=2):
                log(f"✅ 找到 main_tabbar: {toolbar.window_text()}")
        except Exception:
            log("  main_tabbar 未找到，遍历寻找 ToolBar...")
            for desc in win.descendants(depth=3):
                ct = desc.element_info.control_type
                if ct == "ToolBar":
                    log(f"  ✅ 找到 ToolBar: name='{desc.window_text()}' aid='{desc.element_info.automation_id}'")
    else:
        log("❌ pywinauto 也只看到很少子控件")

except Exception as e:
    log(f"pywinauto 异常: {e}")
    import traceback
    traceback.print_exc()


# ═══════════════════════════════════════════════
# 方案 B: comtypes 直接用 IUIAutomation 接口
# ═══════════════════════════════════════════════
section("方案 B: comtypes IUIAutomation (正确 QI)")
try:
    import comtypes
    import comtypes.client

    # 使用 comtypes.client.CreateObject 自动处理 QI
    uia = comtypes.client.CreateObject(
        "{FF48DBA4-60EF-4201-AA87-54103EEF594E}",  # CLSID_CUIAutomation
        interface=None,
    )
    log(f"COM 对象: {type(uia)}")

    # 生成类型库包装
    try:
        from comtypes.gen import UIAutomationClient as UIA
        log(f"UIAutomationClient 类型库已加载")

        # 重新创建并 QI 到正确的接口
        uia2 = comtypes.CoCreateInstance(
            comtypes.GUID("{FF48DBA4-60EF-4201-AA87-54103EEF594E}"),
            interface=UIA.IUIAutomation,
            clsctx=comtypes.CLSCTX_INPROC_SERVER,
        )
        log(f"IUIAutomation 接口: {type(uia2)}")

        element = uia2.ElementFromHandle(main_hwnd)
        if element:
            name = element.CurrentName
            ct = element.CurrentControlType
            log(f"微信元素: Name='{name}' ControlType={ct}")

            # TreeWalker
            walker = uia2.RawViewWalker
            child = walker.GetFirstChildElement(element)
            count = 0
            while child:
                cn = child.CurrentName or ""
                ctype = child.CurrentControlType
                caid = child.CurrentAutomationId or ""
                log(f"  子元素: Name='{cn}' Type={ctype} AID='{caid}'")
                count += 1

                # 递归一层
                grandchild = walker.GetFirstChildElement(child)
                gc_count = 0
                while grandchild and gc_count < 5:
                    gcn = grandchild.CurrentName or ""
                    gct = grandchild.CurrentControlType
                    gaid = grandchild.CurrentAutomationId or ""
                    log(f"    孙元素: Name='{gcn}' Type={gct} AID='{gaid}'")
                    gc_count += 1
                    grandchild = walker.GetNextSiblingElement(grandchild)

                child = walker.GetNextSiblingElement(child)
                if count > 20:
                    break

            log(f"总子元素数: {count}")
            if count > 1:
                log("✅ 原生 IUIAutomation 可以看到控件树！")
            else:
                log("❌ 原生 IUIAutomation 也只看到很少子元素")
        else:
            log("❌ ElementFromHandle 返回 None")

    except ImportError:
        log("UIAutomationClient 类型库未生成，尝试手动生成...")
        comtypes.client.GetModule("UIAutomationCore.dll")
        log("请重新运行脚本")

except Exception as e:
    log(f"comtypes 直接调用异常: {e}")
    import traceback
    traceback.print_exc()


# ═══════════════════════════════════════════════
# 方案 C: 另一台电脑的 uiautomation 和 comtypes 版本
# ═══════════════════════════════════════════════
section("方案 C: 关键对比信息")
log("请在另一台电脑（能正常工作的）上运行:")
log("  python -c \"import uiautomation; print(uiautomation.VERSION)\"")
log("  python -c \"import comtypes; print(comtypes.__version__)\"")
log("  python --version")
log("  python -c \"import struct; print(struct.calcsize('P')*8, 'bit')\"")
log("")
log("如果版本不同，很可能是 comtypes 版本差异导致的。")
log("常见修复: pip install comtypes==1.3.1  (降级 comtypes)")
