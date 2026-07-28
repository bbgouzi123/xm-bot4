"""
UIA 修复脚本 — 完全重启微信并验证 Qt Accessibility
==================================================

步骤：
1. 杀死所有微信相关进程（包括子进程 WeChatAppEx）
2. 确认所有进程退出
3. 清理 COM 缓存（可能干扰 UIA）
4. 手动设置注册表 QT_ACCESSIBILITY=1 + 广播
5. 通过 cmd /c start 启动微信（确保环境变量继承）
6. 等待窗口出现后检测控件树
"""
import os
import sys
import time
import ctypes
import subprocess
import signal

sys.stdout.reconfigure(encoding="utf-8")

def log(msg):
    print(f"[修复] {msg}")


# ═══════════════════════════════════════════════
# Step 1: 杀死所有微信进程
# ═══════════════════════════════════════════════
log("Step 1: 终止所有微信进程...")

procs_to_kill = ["Weixin.exe", "WeChatAppEx.exe", "WeChatApp.exe", "WeChatPlayer.exe", "WechatBrowser.exe"]
for pname in procs_to_kill:
    try:
        result = subprocess.run(
            ["taskkill", "/F", "/IM", pname],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            log(f"  已终止 {pname}")
    except Exception:
        pass

log("等待 5 秒确保全部退出...")
time.sleep(5)

# 再检查一次
import psutil
remaining = [p for p in psutil.process_iter(["name"])
             if p.info["name"] and p.info["name"].lower() in [n.lower() for n in procs_to_kill]]
if remaining:
    log(f"  还有 {len(remaining)} 个进程未退出，强制终止...")
    for p in remaining:
        try:
            p.kill()
        except Exception:
            pass
    time.sleep(3)
else:
    log("  ✓ 所有微信进程已退出")


# ═══════════════════════════════════════════════
# Step 2: 确认环境变量
# ═══════════════════════════════════════════════
log("Step 2: 确认环境变量...")

# 设置注册表
import winreg
try:
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE)
    winreg.SetValueEx(key, "QT_ACCESSIBILITY", 0, winreg.REG_SZ, "1")
    val, _ = winreg.QueryValueEx(key, "QT_ACCESSIBILITY")
    winreg.CloseKey(key)
    log(f"  注册表 QT_ACCESSIBILITY = '{val}' ✓")
except Exception as e:
    log(f"  注册表写入异常: {e}")

# 设置当前进程
os.environ["QT_ACCESSIBILITY"] = "1"

# 广播环境变量变更
HWND_BROADCAST = 0xFFFF
WM_SETTINGCHANGE = 0x001A
SMTO_ABORTIFHUNG = 0x0002
ctypes.windll.user32.SendMessageTimeoutW(
    HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", SMTO_ABORTIFHUNG, 5000, None
)
log("  已广播环境变量变更到 explorer ✓")
time.sleep(2)


# ═══════════════════════════════════════════════
# Step 3: 启动微信
# ═══════════════════════════════════════════════
log("Step 3: 启动微信...")

weixin_path = None
try:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    backend_dir = os.path.abspath(os.path.join(script_dir, "..", ".."))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    from src.utils.wechat_launcher import get_wechat_path
    weixin_path = get_wechat_path()
except Exception as e:
    log(f"导入全局微信定位工具失败: {e}")

if not weixin_path:
    log("❌ 找不到 Weixin.exe！")
    sys.exit(1)

log(f"  微信路径: {weixin_path}")

# 使用 cmd /c start 启动微信，确保子进程继承环境变量
subprocess.Popen(
    f'cmd /c start "" "{weixin_path}"',
    shell=True,
    creationflags=subprocess.CREATE_NO_WINDOW,
)
log("  已通过 cmd /c start 启动")


# ═══════════════════════════════════════════════
# Step 4: 等待微信窗口出现
# ═══════════════════════════════════════════════
log("Step 4: 等待微信窗口...")

import win32gui
import win32process

main_hwnd = None
for i in range(30):
    time.sleep(1)
    windows = []
    def _cb(hwnd, _):
        try:
            cls = win32gui.GetClassName(hwnd)
            title = win32gui.GetWindowText(hwnd)
            if cls == "Qt51514QWindowIcon" and title in ("微信", "Weixin", "WeChat"):
                r = win32gui.GetWindowRect(hwnd)
                w, h = r[2] - r[0], r[3] - r[1]
                vis = win32gui.IsWindowVisible(hwnd)
                windows.append((hwnd, w, h, vis))
        except Exception:
            pass
    win32gui.EnumWindows(_cb, None)

    visible = [(h, w, ht) for h, w, ht, vis in windows if vis]
    if visible:
        best = max(visible, key=lambda x: x[1] * x[2])
        main_hwnd = best[0]
        log(f"  ✓ 微信窗口已出现 hwnd={main_hwnd} {best[1]}x{best[2]} (等待{i+1}秒)")
        break
    if (i+1) % 5 == 0:
        log(f"  等待中... ({i+1}秒)")

if not main_hwnd:
    log("❌ 等待微信窗口超时")
    sys.exit(1)


# ═══════════════════════════════════════════════
# Step 5: 处理登录（如需要）
# ═══════════════════════════════════════════════
log("Step 5: 检查是否需要登录...")

# 判断窗口大小
rect = win32gui.GetWindowRect(main_hwnd)
w, h = rect[2] - rect[0], rect[3] - rect[1]

if w < 500 or h < 400:
    log(f"  窗口较小 ({w}x{h})，可能是登录界面")
    log("  请手动扫码或点击「进入微信」...")
    log("  等待主界面出现（最多60秒）...")

    for i in range(60):
        time.sleep(1)
        windows = []
        def _cb2(hwnd, _):
            try:
                cls = win32gui.GetClassName(hwnd)
                title = win32gui.GetWindowText(hwnd)
                if cls == "Qt51514QWindowIcon" and title in ("微信", "Weixin", "WeChat"):
                    r = win32gui.GetWindowRect(hwnd)
                    w, h = r[2] - r[0], r[3] - r[1]
                    vis = win32gui.IsWindowVisible(hwnd)
                    if vis and w >= 500 and h >= 400:
                        windows.append((hwnd, w, h))
            except Exception:
                pass
        win32gui.EnumWindows(_cb2, None)
        if windows:
            best = max(windows, key=lambda x: x[1] * x[2])
            main_hwnd = best[0]
            log(f"  ✓ 主界面已出现 hwnd={main_hwnd} {best[1]}x{best[2]}")
            break
        if (i+1) % 10 == 0:
            log(f"  等待中... ({i+1}秒)")
    else:
        log("❌ 等待主界面超时")
        sys.exit(1)

# 额外等待微信完全加载
log("  等待微信完全加载 (5秒)...")
time.sleep(5)


# ═══════════════════════════════════════════════
# Step 6: 验证微信进程环境变量
# ═══════════════════════════════════════════════
log("Step 6: 验证微信进程环境变量...")
try:
    _, pid = win32process.GetWindowThreadProcessId(main_hwnd)
    wx_proc = psutil.Process(pid)
    try:
        env_dict = wx_proc.environ()
        qt_acc = env_dict.get("QT_ACCESSIBILITY", "(未设置)")
        log(f"  微信 PID={pid} QT_ACCESSIBILITY = '{qt_acc}'")
        if qt_acc != "1":
            log("  ❌ 环境变量仍未生效！可能需要注销 Windows 重新登录")
    except psutil.AccessDenied:
        log("  ⚠ 权限不足，无法读取环境变量")
except Exception as e:
    log(f"  异常: {e}")


# ═══════════════════════════════════════════════
# Step 7: UIA 控件树验证
# ═══════════════════════════════════════════════
log("Step 7: UIA 控件树验证...")

try:
    import comtypes
    comtypes.CoInitialize()
    import uiautomation as uia

    # 先置前
    ctypes.windll.user32.SetForegroundWindow(main_hwnd)
    time.sleep(0.5)

    # 发送 WM_GETOBJECT
    WM_GETOBJECT = 0x003D
    OBJID_CLIENT = 0xFFFFFFFC
    ctypes.windll.user32.SendMessageW(main_hwnd, WM_GETOBJECT, 0, OBJID_CLIENT)
    time.sleep(1)

    root = uia.ControlFromHandle(main_hwnd)
    if not root:
        log("  ❌ ControlFromHandle 返回 None")
        sys.exit(1)

    children = root.GetChildren()
    log(f"  Root 子控件数: {len(children)}")

    if len(children) <= 1:
        log("  ⚠ 子控件过少，尝试微移窗口...")
        rect = win32gui.GetWindowRect(main_hwnd)
        x, y, r, b = rect
        w, h = r - x, b - y
        win32gui.MoveWindow(main_hwnd, x, y, w + 1, h + 1, True)
        time.sleep(0.5)
        win32gui.MoveWindow(main_hwnd, x, y, w, h, True)
        time.sleep(1)
        ctypes.windll.user32.SendMessageW(main_hwnd, WM_GETOBJECT, 0, OBJID_CLIENT)
        time.sleep(1)

        root = uia.ControlFromHandle(main_hwnd)
        children = root.GetChildren()
        log(f"  微移后子控件数: {len(children)}")

    # 遍历所有子控件
    count = 0
    found_toolbar = False
    for ctrl, depth in uia.WalkControl(root, maxDepth=4):
        count += 1
        if count > 100:
            break
        ct = getattr(ctrl, "ControlTypeName", "") or ""
        cn = getattr(ctrl, "Name", "") or ""
        aid = getattr(ctrl, "AutomationId", "") or ""
        indent = "  " * (depth + 1)
        log(f"  {indent}[{ct}] '{cn}' aid='{aid}'")

        if ct == "ToolBarControl" or aid == "main_tabbar" or "导航" in cn:
            log(f"  {'='*40}")
            log(f"  ✅✅✅ 找到导航栏 → [{ct}] Name='{cn}' AutomationId='{aid}'")
            log(f"  {'='*40}")
            found_toolbar = True

    log(f"  共遍历 {count} 个控件")

    if found_toolbar:
        log("")
        log("═══════════════════════════════════════")
        log("✅ 修复成功！Qt Accessibility 已激活！")
        log("═══════════════════════════════════════")
        log("现在可以正常启动 xm-bot4 了。")
    else:
        log("")
        log("═══════════════════════════════════════")
        log("❌ 仍然未激活 Qt Accessibility")
        log("═══════════════════════════════════════")
        log("建议:")
        log("  1. 注销 Windows 并重新登录（不是锁屏，是注销！）")
        log("  2. 重新启动微信")
        log("  3. 如果注销后仍不行，尝试重启电脑")
        log("  4. 确认微信没有被安全软件拦截（如火绒、360）")

except Exception as e:
    log(f"  ❌ UIA 验证异常: {e}")
    import traceback
    traceback.print_exc()
