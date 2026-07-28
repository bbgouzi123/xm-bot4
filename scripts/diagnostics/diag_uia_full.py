"""
UIA 可访问性全量诊断脚本
========================
逐层排查微信 Qt Accessibility 无法激活的根因。

诊断项目:
  1. 系统注册表 QT_ACCESSIBILITY 环境变量
  2. 当前进程环境变量
  3. 微信进程检测 + 版本号
  4. 微信窗口类名 / 句柄 / 尺寸
  5. 微信进程的环境变量（是否包含 QT_ACCESSIBILITY）
  6. UIA 控件树深度遍历（最多 200 节点）
  7. 导航栏精确查找（AutomationId=main_tabbar / Name=导航）
  8. WM_GETOBJECT 强制刷新后重试
  9. 窗口微移 + 重绑 UIA 后重试
  10. DPI / 缩放信息

用法:
  cd products/xm-bot4/backend-python
  python diag_uia_full.py
"""
import os
import sys
import time
import ctypes
import ctypes.wintypes as wt
import subprocess
import json
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

# ═══════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════
PASS = "✅"
FAIL = "❌"
WARN = "⚠️"
INFO = "ℹ️"

report: list[str] = []

def log(icon: str, msg: str):
    line = f"  {icon} {msg}"
    print(line)
    report.append(line)

def section(title: str):
    sep = f"\n{'='*60}\n  {title}\n{'='*60}"
    print(sep)
    report.append(sep)


# ═══════════════════════════════════════════════
# 1. 注册表检查
# ═══════════════════════════════════════════════
section("1. 系统注册表 QT_ACCESSIBILITY")
try:
    import winreg
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_QUERY_VALUE)
    val, reg_type = winreg.QueryValueEx(key, "QT_ACCESSIBILITY")
    winreg.CloseKey(key)
    if val == "1":
        log(PASS, f"HKCU\\Environment\\QT_ACCESSIBILITY = '{val}' (type={reg_type})")
    else:
        log(FAIL, f"HKCU\\Environment\\QT_ACCESSIBILITY = '{val}'（应为 '1'）")
except FileNotFoundError:
    log(FAIL, "HKCU\\Environment 中未找到 QT_ACCESSIBILITY，需要注入！")
    log(INFO, "修复: 手动执行 setx QT_ACCESSIBILITY 1")
except Exception as e:
    log(FAIL, f"注册表读取异常: {e}")


# ═══════════════════════════════════════════════
# 2. 当前进程环境变量
# ═══════════════════════════════════════════════
section("2. 当前进程环境变量")
env_val = os.environ.get("QT_ACCESSIBILITY", "(未设置)")
if env_val == "1":
    log(PASS, f"os.environ['QT_ACCESSIBILITY'] = '{env_val}'")
else:
    log(WARN, f"os.environ['QT_ACCESSIBILITY'] = '{env_val}'")
    log(INFO, "当前 Python 进程自身的环境变量不影响微信，关键看微信进程自己的环境")


# ═══════════════════════════════════════════════
# 3. 微信进程检测
# ═══════════════════════════════════════════════
section("3. 微信进程检测")
try:
    import psutil
    wx_procs = [p for p in psutil.process_iter(["pid", "name", "exe", "create_time"])
                if p.info["name"] and p.info["name"].lower() in ("weixin.exe", "wechat.exe")]
    if not wx_procs:
        log(FAIL, "未找到微信进程 (Weixin.exe / WeChat.exe)")
        log(INFO, "请先启动微信再运行诊断")
    else:
        for p in wx_procs:
            log(PASS, f"PID={p.info['pid']}  exe={p.info['exe']}")
            # 文件版本
            try:
                import win32api
                info = win32api.GetFileVersionInfo(p.info["exe"], "\\")
                ms = info["FileVersionMS"]
                ls = info["FileVersionLS"]
                ver = f"{(ms>>16)&0xFFFF}.{ms&0xFFFF}.{(ls>>16)&0xFFFF}.{ls&0xFFFF}"
                log(INFO, f"微信文件版本: {ver}")
                # 兼容性判断
                v_tuple = ((ms>>16)&0xFFFF, ms&0xFFFF, (ls>>16)&0xFFFF, ls&0xFFFF)
                max_compat = (4, 1, 7, 99)
                if v_tuple <= max_compat:
                    log(PASS, f"版本在 UIA 兼容范围 (<= {'.'.join(map(str,max_compat))})")
                else:
                    log(FAIL, f"版本超出 UIA 兼容上限！({'.'.join(map(str,max_compat))})")
                    log(INFO, "微信 4.1.8+ 已封锁 Qt Accessibility 控件树")
            except Exception as e:
                log(WARN, f"版本检测失败: {e}")
except ImportError:
    log(WARN, "psutil 未安装，跳过进程检测")


# ═══════════════════════════════════════════════
# 4. 微信窗口枚举
# ═══════════════════════════════════════════════
section("4. 微信窗口枚举")
import win32gui
import win32process

wechat_windows = []

def _enum_cb(hwnd, _):
    try:
        cls = win32gui.GetClassName(hwnd)
        title = win32gui.GetWindowText(hwnd)
        if cls == "Qt51514QWindowIcon" and title in ("微信", "Weixin", "WeChat"):
            r = win32gui.GetWindowRect(hwnd)
            w, h = r[2] - r[0], r[3] - r[1]
            vis = win32gui.IsWindowVisible(hwnd)
            wechat_windows.append({
                "hwnd": hwnd,
                "class": cls,
                "title": title,
                "rect": r,
                "size": f"{w}x{h}",
                "visible": bool(vis),
            })
    except Exception:
        pass

win32gui.EnumWindows(_enum_cb, None)

if not wechat_windows:
    log(FAIL, "未找到微信窗口 (ClassName=Qt51514QWindowIcon, Title=微信)")
    # 尝试查找其他可能的类名
    log(INFO, "扩大搜索范围...")
    def _enum_broad(hwnd, _):
        try:
            title = win32gui.GetWindowText(hwnd)
            if title in ("微信", "Weixin", "WeChat") and win32gui.IsWindowVisible(hwnd):
                cls = win32gui.GetClassName(hwnd)
                r = win32gui.GetWindowRect(hwnd)
                w, h = r[2] - r[0], r[3] - r[1]
                log(WARN, f"发现窗口: hwnd={hwnd} class='{cls}' title='{title}' {w}x{h}")
                wechat_windows.append({"hwnd": hwnd, "class": cls, "title": title, "rect": r, "size": f"{w}x{h}", "visible": True})
        except Exception:
            pass
    win32gui.EnumWindows(_enum_broad, None)
    if not wechat_windows:
        log(FAIL, "广域搜索也未找到微信窗口")
else:
    for w in wechat_windows:
        vis = "可见" if w["visible"] else "不可见"
        log(PASS, f"hwnd={w['hwnd']} class='{w['class']}' title='{w['title']}' {w['size']} [{vis}]")

# 选取主窗口
main_hwnd = None
for w in wechat_windows:
    if w["visible"]:
        wh = int(w["size"].split("x")[0])
        ht = int(w["size"].split("x")[1])
        if wh >= 500 and ht >= 400:
            main_hwnd = w["hwnd"]
            break
if not main_hwnd and wechat_windows:
    main_hwnd = wechat_windows[0]["hwnd"]

if not main_hwnd:
    log(FAIL, "没有可用的主窗口句柄，后续诊断无法继续")
    section("诊断结束")
    sys.exit(1)

log(INFO, f"选定主窗口: hwnd={main_hwnd}")


# ═══════════════════════════════════════════════
# 5. 微信进程环境变量检查（关键！）
# ═══════════════════════════════════════════════
section("5. 微信进程环境变量检查（关键诊断项）")
try:
    _, pid = win32process.GetWindowThreadProcessId(main_hwnd)
    log(INFO, f"微信进程 PID: {pid}")

    # 使用 WMI 查询
    result = subprocess.run(
        ["powershell", "-Command",
         f"$p = Get-Process -Id {pid} -ErrorAction SilentlyContinue; "
         f"if ($p) {{ "
         f"  $env_block = (Get-CimInstance Win32_Process -Filter \"ProcessId=$($p.Id)\").CommandLine; "
         f"  Write-Output \"exe=$($p.Path)\"; "
         f"}} else {{ Write-Output 'process_not_found' }}"],
        capture_output=True, text=True, timeout=10
    )
    if result.stdout.strip():
        log(INFO, f"PowerShell: {result.stdout.strip()[:200]}")

    # 通过 NtQueryInformationProcess 读取进程环境块（最可靠的方法）
    # 简化方案：用 PowerShell 去读进程环境
    env_check = subprocess.run(
        ["powershell", "-Command",
         f"$h = [System.Diagnostics.Process]::GetProcessById({pid}); "
         f"try {{ "
         f"  $si = $h.StartInfo; "
         f"  $si.UseShellExecute = $false; "
         f"  Write-Output 'checked' "
         f"}} catch {{ Write-Output $_.Exception.Message }}"],
        capture_output=True, text=True, timeout=10
    )

    # 最实用的方法：通过 wmic 查看命令行
    wmic_result = subprocess.run(
        ["powershell", "-Command",
         f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine"],
        capture_output=True, text=True, timeout=10
    )
    if wmic_result.stdout.strip():
        log(INFO, f"微信命令行: {wmic_result.stdout.strip()[:300]}")

    # 最关键检测：微信的父进程（是否通过 cmd /c start 启动）
    try:
        wx_proc = psutil.Process(pid)
        parent = wx_proc.parent()
        if parent:
            log(INFO, f"微信父进程: PID={parent.pid} name={parent.name()} exe={parent.exe()}")
        else:
            log(INFO, "微信没有父进程（可能由 explorer 直接启动）")

        # 检查微信进程的实际环境变量
        log(INFO, "尝试读取微信进程的环境变量...")
        try:
            env_dict = wx_proc.environ()
            qt_acc = env_dict.get("QT_ACCESSIBILITY", "(未设置)")
            if qt_acc == "1":
                log(PASS, f"微信进程 QT_ACCESSIBILITY = '{qt_acc}'")
            else:
                log(FAIL, f"微信进程 QT_ACCESSIBILITY = '{qt_acc}'")
                log(INFO, "这是根本原因！微信进程启动时没有读到 QT_ACCESSIBILITY=1")
                log(INFO, "解决方案: 1) setx QT_ACCESSIBILITY 1 → 2) 重启微信")
        except psutil.AccessDenied:
            log(WARN, "权限不足，无法读取微信环境变量（需要管理员权限）")
            log(INFO, "建议: 以管理员身份运行此脚本")
        except Exception as e:
            log(WARN, f"读取环境变量失败: {e}")
    except Exception as e:
        log(WARN, f"进程分析失败: {e}")
except Exception as e:
    log(FAIL, f"进程检查异常: {e}")


# ═══════════════════════════════════════════════
# 6. DPI / 显示缩放
# ═══════════════════════════════════════════════
section("6. DPI / 显示缩放")
try:
    # SetProcessDPIAware
    ctypes.windll.user32.SetProcessDPIAware()
    # GetDpiForWindow
    try:
        dpi = ctypes.windll.user32.GetDpiForWindow(main_hwnd)
        scale = dpi / 96 * 100
        log(INFO, f"微信窗口 DPI: {dpi} (缩放={scale:.0f}%)")
        if dpi != 96:
            log(WARN, "非 100% 缩放可能影响 UIA 坐标计算，但不应影响控件树发现")
    except Exception:
        # Win10 1607 之前没有 GetDpiForWindow
        dpi = ctypes.windll.user32.GetDpiForSystem()
        log(INFO, f"系统 DPI: {dpi}")

    # 屏幕分辨率
    sm_cx = ctypes.windll.user32.GetSystemMetrics(0)  # SM_CXSCREEN
    sm_cy = ctypes.windll.user32.GetSystemMetrics(1)  # SM_CYSCREEN
    log(INFO, f"屏幕分辨率: {sm_cx}x{sm_cy}")
except Exception as e:
    log(WARN, f"DPI 检查异常: {e}")


# ═══════════════════════════════════════════════
# 7. UIA 控件树深度遍历
# ═══════════════════════════════════════════════
section("7. UIA 控件树遍历")
try:
    import comtypes
    comtypes.CoInitialize()
    import uiautomation as uia

    root = uia.ControlFromHandle(main_hwnd)
    if not root:
        log(FAIL, "uia.ControlFromHandle 返回 None")
    else:
        log(PASS, f"UIA Root: ControlType={root.ControlTypeName} Name='{root.Name}' Class='{root.ClassName}'")

        # 先发送 WM_GETOBJECT
        WM_GETOBJECT = 0x003D
        OBJID_CLIENT = 0xFFFFFFFC
        ctypes.windll.user32.SendMessageW(main_hwnd, WM_GETOBJECT, 0, OBJID_CLIENT)
        time.sleep(0.5)

        # 遍历控件树（最多 200 个节点，深度 5）
        nodes = []
        count = [0]

        def walk(ctrl, depth=0, max_depth=5):
            if depth > max_depth or count[0] >= 200:
                return
            try:
                ct = getattr(ctrl, "ControlTypeName", "") or ""
                cn = getattr(ctrl, "Name", "") or ""
                cc = getattr(ctrl, "ClassName", "") or ""
                aid = getattr(ctrl, "AutomationId", "") or ""
                node = {
                    "depth": depth,
                    "type": ct,
                    "name": cn[:50],
                    "class": cc,
                    "automationId": aid,
                }
                nodes.append(node)
                count[0] += 1

                indent = "  " * depth
                name_display = f"'{cn[:40]}'" if cn else "''"
                aid_display = f" aid='{aid}'" if aid else ""
                log(INFO, f"{indent}[{ct}] Name={name_display} Class='{cc}'{aid_display}")

                for child in ctrl.GetChildren():
                    walk(child, depth + 1, max_depth)
            except Exception as e:
                log(WARN, f"{'  ' * depth}遍历异常: {e}")

        walk(root)
        log(INFO, f"共遍历 {count[0]} 个节点")

        # 查找关键控件
        has_toolbar = any(n["type"] == "ToolBarControl" for n in nodes)
        has_main_tabbar = any(n["automationId"] == "main_tabbar" for n in nodes)
        has_nav_name = any("导航" in n["name"] for n in nodes)

        section("8. 关键控件查找结果")
        if has_main_tabbar:
            log(PASS, "找到 AutomationId='main_tabbar' 的控件")
        else:
            log(FAIL, "未找到 AutomationId='main_tabbar'")

        if has_nav_name:
            log(PASS, "找到 Name 包含 '导航' 的控件")
        else:
            log(FAIL, "未找到 Name 包含 '导航' 的控件")

        if has_toolbar:
            log(PASS, "控件树中存在 ToolBar 类型控件")
            for n in nodes:
                if n["type"] == "ToolBarControl":
                    log(INFO, f"  → Name='{n['name']}' AutomationId='{n['automationId']}'")
        else:
            log(FAIL, "控件树中不存在任何 ToolBar 类型控件")

        # 列出 depth=1 的所有直接子控件
        depth1 = [n for n in nodes if n["depth"] == 1]
        log(INFO, f"Root 直接子控件 ({len(depth1)} 个):")
        for n in depth1:
            log(INFO, f"  → [{n['type']}] Name='{n['name']}' Class='{n['class']}' aid='{n['automationId']}'")

        # 判断是否只有一个 Pane 子控件（典型的 Accessibility 未激活特征）
        if len(depth1) == 1 and depth1[0]["type"] == "PaneControl":
            log(WARN, "控件树只有 1 个 PaneControl 子节点 — 这是 Qt Accessibility 未激活的典型特征！")
            total_depth2 = [n for n in nodes if n["depth"] == 2]
            if len(total_depth2) == 0:
                log(FAIL, "Depth=2 层无任何控件 — 确认 Qt Accessibility 完全未激活")
                log(INFO, "")
                log(INFO, "══════════════════════════════════════")
                log(INFO, "根因诊断: Qt Accessibility 未激活")
                log(INFO, "══════════════════════════════════════")
                log(INFO, "微信启动时没有读到 QT_ACCESSIBILITY=1 环境变量。")
                log(INFO, "")
                log(INFO, "修复步骤:")
                log(INFO, "  1. 管理员 PowerShell 运行: setx QT_ACCESSIBILITY 1")
                log(INFO, "  2. 完全关闭微信 (包括托盘)")
                log(INFO, "  3. 注销 Windows 或重启电脑（确保环境变量对所有新进程生效）")
                log(INFO, "  4. 重新启动微信")
                log(INFO, "  5. 再次运行此脚本验证")
            else:
                log(WARN, f"Depth=2 有 {len(total_depth2)} 个控件，但没有 ToolBar — 可能是部分激活")

        # 额外尝试：窗口微移后重试
        section("9. 窗口微移 + 重绑 UIA 重试")
        log(INFO, "微移窗口 1px 触发 Qt 重绘...")
        try:
            rect = win32gui.GetWindowRect(main_hwnd)
            x, y, r, b = rect
            w, h = r - x, b - y
            win32gui.MoveWindow(main_hwnd, x, y, w + 1, h + 1, True)
            time.sleep(0.3)
            win32gui.MoveWindow(main_hwnd, x, y, w, h, True)
            time.sleep(0.5)

            # 重新发送 WM_GETOBJECT
            ctypes.windll.user32.SendMessageW(main_hwnd, WM_GETOBJECT, 0, OBJID_CLIENT)
            time.sleep(0.5)

            # 重新获取 root
            root2 = uia.ControlFromHandle(main_hwnd)
            if root2:
                children2 = root2.GetChildren()
                log(INFO, f"微移后 root 子控件数: {len(children2)}")
                for c in children2[:10]:
                    ct2 = getattr(c, "ControlTypeName", "")
                    cn2 = getattr(c, "Name", "")
                    aid2 = getattr(c, "AutomationId", "")
                    log(INFO, f"  → [{ct2}] Name='{cn2}' aid='{aid2}'")

                # 再次尝试查找导航栏
                tb = root2.ToolBarControl(AutomationId="main_tabbar")
                if tb.Exists(2, 0.5):
                    log(PASS, "微移后找到了 main_tabbar！")
                else:
                    tb2 = root2.ToolBarControl(Name="导航")
                    if tb2.Exists(2, 0.5):
                        log(PASS, "微移后找到了 Name='导航' 的 ToolBar！")
                    else:
                        log(FAIL, "微移后仍未找到导航栏")
        except Exception as e:
            log(WARN, f"微移重试异常: {e}")

except ImportError:
    log(FAIL, "uiautomation 未安装: pip install uiautomation")
except Exception as e:
    log(FAIL, f"UIA 诊断异常: {e}")
    import traceback
    traceback.print_exc()


# ═══════════════════════════════════════════════
# 10. 综合建议
# ═══════════════════════════════════════════════
section("10. 综合诊断总结")
log(INFO, f"诊断时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
log(INFO, f"主机名: {os.environ.get('COMPUTERNAME', 'unknown')}")
log(INFO, "")

# 保存报告
report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag_uia_report.txt")
with open(report_path, "w", encoding="utf-8") as f:
    f.write("\n".join(report))
log(INFO, f"诊断报告已保存: {report_path}")

print("\n" + "="*60)
print("  诊断完成！请将上述输出发给开发者分析。")
print("="*60)
