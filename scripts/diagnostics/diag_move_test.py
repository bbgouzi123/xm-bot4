"""
验证修复方案 — 窗口位置 + uiautomation 库搜索行为
=================================================

上一轮诊断发现:
  ✅ 原生 IUIAutomation COM 接口可以看到完整控件树 (34 节点)
  ❌ uiautomation 库只看到 1 个子节点
  ⚠️  微信窗口坐标全为负数 (-736,0,-8,1024) — 完全在屏幕外！

本脚本验证:
  1. 移动窗口到屏幕内 → uiautomation 是否恢复
  2. uiautomation 库的 TreeWalker 类型差异测试
  3. GetChildren vs WalkControl 对比
"""
import sys
import time
import ctypes

sys.stdout.reconfigure(encoding="utf-8")

def log(msg):
    print(f"[测试] {msg}")

def section(title):
    print(f"\n{'='*60}\n  {title}\n{'='*60}")


import win32gui

# 找微信窗口
wechat_windows = []
def _cb(hwnd, _):
    try:
        cls = win32gui.GetClassName(hwnd)
        title = win32gui.GetWindowText(hwnd)
        if cls == "Qt51514QWindowIcon" and title in ("微信", "Weixin", "WeChat"):
            if win32gui.IsWindowVisible(hwnd):
                r = win32gui.GetWindowRect(hwnd)
                w, h = r[2] - r[0], r[3] - r[1]
                wechat_windows.append((hwnd, w, h, r))
    except Exception:
        pass
win32gui.EnumWindows(_cb, None)

if not wechat_windows:
    log("❌ 未找到微信窗口")
    sys.exit(1)

best = max(wechat_windows, key=lambda x: x[1]*x[2])
main_hwnd = best[0]
orig_rect = best[3]
log(f"微信 hwnd={main_hwnd}  原始位置: {orig_rect}  尺寸: {best[1]}x{best[2]}")


# ═══════════════════════════════════════════════
# Step 1: 先在原始位置测试 uiautomation
# ═══════════════════════════════════════════════
section("Step 1: 原始位置 — uiautomation 测试")

import threading

def test_uia_lib(label: str):
    """在新线程中测试 uiautomation 库"""
    result = {"children": 0, "types": [], "found_nav": False}

    def _work():
        try:
            import comtypes
            comtypes.CoInitialize()
            import uiautomation as uia

            root = uia.ControlFromHandle(main_hwnd)
            if not root:
                log(f"  [{label}] ControlFromHandle 返回 None")
                return

            children = root.GetChildren()
            result["children"] = len(children)
            for c in children[:10]:
                ct = getattr(c, 'ControlTypeName', '') or ''
                cn = getattr(c, 'Name', '') or ''
                result["types"].append(f"{ct}('{cn}')")

            log(f"  [{label}] GetChildren → {result['children']} 个: {' | '.join(result['types'][:5])}")

            # 尝试 FindFirst 搜索
            tb = root.ToolBarControl(AutomationId="main_tabbar")
            if tb.Exists(2, 0.5):
                result["found_nav"] = True
                log(f"  [{label}] ✅ 找到 main_tabbar ToolBar!")
            else:
                log(f"  [{label}] ❌ ToolBarControl(AutomationId='main_tabbar') 未找到")

            # 尝试 Name 搜索
            if not result["found_nav"]:
                tb2 = root.ToolBarControl(Name="导航")
                if tb2.Exists(2, 0.5):
                    result["found_nav"] = True
                    log(f"  [{label}] ✅ 找到 Name='导航' ToolBar!")
                else:
                    log(f"  [{label}] ❌ ToolBarControl(Name='导航') 也未找到")

            # 尝试 WalkControl 遍历
            toolbar_count = 0
            ctrl_count = 0
            for ctrl, depth in uia.WalkControl(root, maxDepth=6):
                ctrl_count += 1
                ct = getattr(ctrl, 'ControlTypeName', '') or ''
                cn = getattr(ctrl, 'Name', '') or ''
                if ct == 'ToolBarControl':
                    toolbar_count += 1
                    log(f"  [{label}] WalkControl 发现 ToolBar: Name='{cn}'")
                if ctrl_count > 200:
                    break
            log(f"  [{label}] WalkControl 总遍历: {ctrl_count} 个, ToolBar: {toolbar_count} 个")

        except Exception as e:
            log(f"  [{label}] 异常: {e}")
            import traceback
            traceback.print_exc()

    t = threading.Thread(target=_work, daemon=True)
    t.start()
    t.join(timeout=20)
    return result


result_before = test_uia_lib("原位置")


# ═══════════════════════════════════════════════
# Step 2: 移动窗口到屏幕内
# ═══════════════════════════════════════════════
section("Step 2: 移动微信窗口到屏幕内")

# 检查窗口是否在屏幕外
sm_cx = ctypes.windll.user32.GetSystemMetrics(0)
sm_cy = ctypes.windll.user32.GetSystemMetrics(1)
log(f"屏幕分辨率: {sm_cx}x{sm_cy}")

x, y, r, b = orig_rect
is_offscreen = (r <= 0 or x >= sm_cx or b <= 0 or y >= sm_cy)

if is_offscreen:
    log(f"⚠ 微信窗口在屏幕外!  ({x},{y})-({r},{b})")
    # 移动到屏幕中心
    w = r - x
    h = b - y
    new_x = max(0, (sm_cx - w) // 2)
    new_y = max(0, (sm_cy - h) // 2)
    log(f"移动到 ({new_x}, {new_y}) ...")
    win32gui.MoveWindow(main_hwnd, new_x, new_y, w, h, True)
    time.sleep(1)

    # 验证新位置
    new_rect = win32gui.GetWindowRect(main_hwnd)
    log(f"新位置: {new_rect}")

    # 置前
    ctypes.windll.user32.SetForegroundWindow(main_hwnd)
    time.sleep(0.5)
else:
    log(f"微信窗口在屏幕内: ({x},{y})-({r},{b})")


# ═══════════════════════════════════════════════
# Step 3: 移动后重新测试 uiautomation
# ═══════════════════════════════════════════════
section("Step 3: 移动后 — uiautomation 测试")

# 等待 Qt 重绘
time.sleep(2)

# 发送 WM_GETOBJECT 刷新
WM_GETOBJECT = 0x003D
OBJID_CLIENT = 0xFFFFFFFC
ctypes.windll.user32.SendMessageW(main_hwnd, WM_GETOBJECT, 0, OBJID_CLIENT)
time.sleep(1)

result_after = test_uia_lib("移动后")


# ═══════════════════════════════════════════════
# Step 4: 恢复原位（如果用户需要）
# ═══════════════════════════════════════════════
section("诊断结论")

if result_after.get("found_nav") and not result_before.get("found_nav"):
    log("🎯 根因确认: 微信窗口在屏幕外导致 uiautomation 库无法遍历控件树！")
    log("")
    log("修复方案:")
    log("  在 startup_flow.py 的 find_nav_toolbar() 前，")
    log("  确保微信窗口在屏幕可见区域内。")
    log("")
    log("具体代码: 在 ensure_wechat_ready() 的「⑥ 确保窗口在最前面」后，")
    log("  检查窗口 rect, 如果 offscreen 就移回屏幕内。")
elif result_after.get("found_nav") and result_before.get("found_nav"):
    log("✅ 移动前后都可以找到导航栏，问题可能已修复。")
elif not result_after.get("found_nav") and not result_before.get("found_nav"):
    log("❌ 移动到屏幕内后仍然找不到导航栏。")
    log("问题不在窗口位置，可能是 uiautomation 库版本问题。")
    log("建议: pip install uiautomation==2.0.15 (降级到稳定版)")
else:
    log("🤔 异常情况，需要进一步分析。")

log(f"\n移动前子控件数: {result_before.get('children', 0)}")
log(f"移动后子控件数: {result_after.get('children', 0)}")
