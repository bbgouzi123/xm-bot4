"""
完整控件树属性转储 — 用原生 IUIAutomation 接口
==============================================
上一轮发现 Qt 暴露了 UI 结构但 Name/AutomationId 为空。
这次打印每个节点的所有可用属性，确定到底暴露了什么。
"""
import sys
import time
import ctypes
import os

sys.stdout.reconfigure(encoding="utf-8")

def log(msg):
    print(msg)

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

# ControlType 名称映射
CT_NAMES = {
    50000: "Button", 50001: "Calendar", 50002: "CheckBox", 50003: "ComboBox",
    50004: "Edit", 50005: "Hyperlink", 50006: "Image", 50007: "ListItem",
    50008: "List", 50009: "Menu", 50010: "MenuBar", 50011: "MenuItem",
    50012: "ProgressBar", 50013: "RadioButton", 50014: "ScrollBar",
    50015: "Slider", 50016: "Spinner", 50017: "StatusBar", 50018: "Tab",
    50019: "TabItem", 50020: "Text", 50021: "ToolBar", 50022: "ToolTip",
    50023: "Tree", 50024: "TreeItem", 50025: "Custom", 50026: "Group",
    50027: "Thumb", 50028: "DataGrid", 50029: "DataItem", 50030: "Document",
    50031: "SplitButton", 50032: "Window", 50033: "Pane", 50034: "Header",
    50035: "HeaderItem", 50036: "Table", 50037: "TitleBar", 50038: "Separator",
    50039: "SemanticZoom", 50040: "AppBar",
}

import comtypes
import comtypes.client
comtypes.CoInitialize()

from comtypes.gen import UIAutomationClient as UIA

uia = comtypes.CoCreateInstance(
    comtypes.GUID("{FF48DBA4-60EF-4201-AA87-54103EEF594E}"),
    interface=UIA.IUIAutomation,
    clsctx=comtypes.CLSCTX_INPROC_SERVER,
)

element = uia.ElementFromHandle(main_hwnd)
if not element:
    log("❌ ElementFromHandle 返回 None")
    sys.exit(1)

walker = uia.RawViewWalker
node_count = [0]

def dump_element(el, depth=0, max_depth=5):
    """递归转储元素的所有属性"""
    if depth > max_depth or node_count[0] > 300:
        return
    node_count[0] += 1

    indent = "  " * depth
    try:
        name = el.CurrentName or ""
        ct = el.CurrentControlType
        ct_name = CT_NAMES.get(ct, f"Unknown({ct})")
        aid = el.CurrentAutomationId or ""
        cls = el.CurrentClassName or ""
        hwnd_val = el.CurrentNativeWindowHandle
        pid = el.CurrentProcessId

        # BoundingRectangle
        try:
            rect = el.CurrentBoundingRectangle
            rect_str = f"({rect.left},{rect.top},{rect.right},{rect.bottom}) {rect.right-rect.left}x{rect.bottom-rect.top}"
        except Exception:
            rect_str = "(N/A)"

        # IsEnabled / IsOffscreen
        try:
            enabled = el.CurrentIsEnabled
            offscreen = el.CurrentIsOffscreen
        except Exception:
            enabled = "?"
            offscreen = "?"

        # AcceleratorKey / AccessKey
        try:
            acc_key = el.CurrentAcceleratorKey or ""
            access_key = el.CurrentAccessKey or ""
        except Exception:
            acc_key = ""
            access_key = ""

        # LocalizedControlType
        try:
            local_type = el.CurrentLocalizedControlType or ""
        except Exception:
            local_type = ""

        # HelpText
        try:
            help_text = el.CurrentHelpText or ""
        except Exception:
            help_text = ""

        # FrameworkId
        try:
            framework = el.CurrentFrameworkId or ""
        except Exception:
            framework = ""

        # ItemType
        try:
            item_type = el.CurrentItemType or ""
        except Exception:
            item_type = ""

        log(f"{indent}[{ct_name}] Name='{name[:60]}' AID='{aid}' Class='{cls}'")
        log(f"{indent}  rect={rect_str} hwnd={hwnd_val} pid={pid}")
        log(f"{indent}  enabled={enabled} offscreen={offscreen} framework='{framework}'")
        if local_type:
            log(f"{indent}  localType='{local_type}'")
        if help_text:
            log(f"{indent}  helpText='{help_text[:100]}'")
        if acc_key or access_key:
            log(f"{indent}  accKey='{acc_key}' accessKey='{access_key}'")
        if item_type:
            log(f"{indent}  itemType='{item_type}'")

    except Exception as e:
        log(f"{indent}[读取属性异常: {e}]")
        return

    # 遍历子节点
    try:
        child = walker.GetFirstChildElement(el)
        while child and node_count[0] <= 300:
            dump_element(child, depth + 1, max_depth)
            child = walker.GetNextSiblingElement(child)
    except Exception as e:
        log(f"{indent}  [子节点遍历异常: {e}]")


log(f"微信 hwnd={main_hwnd}")
log(f"{'='*70}")
log(f"UIA 控件树完整属性转储 (最多 300 节点, 深度 5)")
log(f"{'='*70}")

dump_element(element)

log(f"\n共转储 {node_count[0]} 个节点")

# 特别检查：ToolBar 类型
log(f"\n{'='*70}")
log("查找 ToolBar 类型控件...")
log(f"{'='*70}")

def find_type(el, target_type, depth=0, max_depth=6):
    """递归查找指定 ControlType 的控件"""
    if depth > max_depth:
        return
    try:
        ct = el.CurrentControlType
        if ct == target_type:
            name = el.CurrentName or ""
            aid = el.CurrentAutomationId or ""
            log(f"  ✅ 找到 ToolBar: Name='{name}' AID='{aid}' depth={depth}")
    except Exception:
        return
    try:
        child = walker.GetFirstChildElement(el)
        while child:
            find_type(child, target_type, depth + 1, max_depth)
            child = walker.GetNextSiblingElement(child)
    except Exception:
        pass

find_type(element, 50021)  # 50021 = ToolBar
