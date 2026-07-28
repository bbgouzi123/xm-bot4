"""
COM 层 UIA 诊断脚本
=====================
讲述人能识别微信但 uiautomation 库看不到 → 问题在 COM 层。

排查方向：
  1. Python 位数 vs 微信位数（32/64 不匹配会导致跨进程 UIA 失败）
  2. comtypes 生成缓存是否损坏
  3. 不同 COM 初始化方式（STA / MTA）
  4. 直接用原生 Windows UIA COM API 绕过 uiautomation 库
  5. IUIAutomation 接口直接调用
"""
import os
import sys
import time
import ctypes
import struct
import subprocess
import platform

sys.stdout.reconfigure(encoding="utf-8")

def log(msg):
    print(f"[诊断] {msg}")

def section(title):
    print(f"\n{'='*60}\n  {title}\n{'='*60}")


# ═══════════════════════════════════════════════
# 1. Python 位数 vs 微信位数
# ═══════════════════════════════════════════════
section("1. Python 与微信进程位数对比")

python_bits = struct.calcsize("P") * 8
log(f"Python 位数: {python_bits}-bit")
log(f"Python 路径: {sys.executable}")
log(f"Python 版本: {sys.version}")
log(f"平台: {platform.machine()}")

# 获取微信进程位数
import win32gui
import win32process
import psutil

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
    log("❌ 未找到微信窗口，请先启动微信")
    sys.exit(1)

main_hwnd = max(wechat_windows, key=lambda x: x[1]*x[2])[0]
log(f"微信主窗口: hwnd={main_hwnd}")

_, wx_pid = win32process.GetWindowThreadProcessId(main_hwnd)
log(f"微信 PID: {wx_pid}")

# 检测微信进程位数
try:
    kernel32 = ctypes.windll.kernel32
    hProcess = kernel32.OpenProcess(0x0400, False, wx_pid)  # PROCESS_QUERY_INFORMATION
    if hProcess:
        is_wow64 = ctypes.c_int(0)
        kernel32.IsWow64Process(hProcess, ctypes.byref(is_wow64))
        kernel32.CloseHandle(hProcess)
        wechat_bits = 32 if is_wow64.value else 64
        log(f"微信位数: {wechat_bits}-bit (IsWow64={is_wow64.value})")

        if python_bits != wechat_bits:
            log(f"❌ 位数不匹配！Python={python_bits}bit, 微信={wechat_bits}bit")
            log(f"   这是 UIA 跨进程访问失败的常见原因！")
            log(f"   解决方案: 安装 {wechat_bits}-bit 版本的 Python")
        else:
            log(f"✅ 位数匹配: 均为 {python_bits}-bit")
    else:
        log(f"⚠ 无法打开微信进程 (error={kernel32.GetLastError()})")
except Exception as e:
    log(f"位数检测异常: {e}")


# ═══════════════════════════════════════════════
# 2. comtypes 缓存检查
# ═══════════════════════════════════════════════
section("2. comtypes 缓存检查")

try:
    import comtypes
    cache_dir = os.path.join(comtypes.__path__[0], "gen")
    if os.path.exists(cache_dir):
        cache_files = os.listdir(cache_dir)
        log(f"comtypes 缓存目录: {cache_dir}")
        log(f"缓存文件数: {len(cache_files)}")

        # 检查有没有 UIAutomation 相关的缓存
        uia_cache = [f for f in cache_files if "UIAutomation" in f or "Automat" in f.lower()]
        if uia_cache:
            log(f"UIA 相关缓存: {uia_cache[:5]}")
        else:
            log("未找到 UIA 相关缓存文件")
    else:
        log(f"comtypes 缓存目录不存在: {cache_dir}")
except Exception as e:
    log(f"comtypes 检查异常: {e}")


# ═══════════════════════════════════════════════
# 3. 不同 COM 初始化方式测试
# ═══════════════════════════════════════════════
section("3. COM 初始化方式测试")

import threading

def test_sta():
    """STA 模式（单线程套间）"""
    try:
        import comtypes
        comtypes.CoInitialize()   # STA
        import uiautomation as uia
        root = uia.ControlFromHandle(main_hwnd)
        if root:
            children = root.GetChildren()
            count = len(children)
            names = []
            for c in children:
                ct = getattr(c, 'ControlTypeName', '') or ''
                cn = getattr(c, 'Name', '') or ''
                names.append(f"{ct}('{cn}')")
            log(f"  STA 模式: root 子控件数={count} → {' | '.join(names[:5])}")
            return count
        else:
            log("  STA 模式: ControlFromHandle 返回 None")
            return 0
    except Exception as e:
        log(f"  STA 模式异常: {e}")
        return -1

def test_mta():
    """MTA 模式（多线程套间）"""
    try:
        import comtypes
        comtypes.CoInitializeEx(0x0)  # COINIT_MULTITHREADED = 0
        import uiautomation as uia
        root = uia.ControlFromHandle(main_hwnd)
        if root:
            children = root.GetChildren()
            count = len(children)
            names = []
            for c in children:
                ct = getattr(c, 'ControlTypeName', '') or ''
                cn = getattr(c, 'Name', '') or ''
                names.append(f"{ct}('{cn}')")
            log(f"  MTA 模式: root 子控件数={count} → {' | '.join(names[:5])}")
            return count
        else:
            log("  MTA 模式: ControlFromHandle 返回 None")
            return 0
    except Exception as e:
        log(f"  MTA 模式异常: {e}")
        return -1

# 在新线程中测试 STA
log("测试 STA 线程...")
sta_result = [0]
t1 = threading.Thread(target=lambda: sta_result.__setitem__(0, test_sta()))
t1.start()
t1.join(timeout=10)

# 在新线程中测试 MTA
log("测试 MTA 线程...")
mta_result = [0]
t2 = threading.Thread(target=lambda: mta_result.__setitem__(0, test_mta()))
t2.start()
t2.join(timeout=10)


# ═══════════════════════════════════════════════
# 4. 直接用原生 Windows UIA COM 接口
# ═══════════════════════════════════════════════
section("4. 原生 IUIAutomation COM 接口直接调用")

def test_raw_uia():
    """绕过 uiautomation 库，直接调用 Windows UIA COM"""
    try:
        import comtypes
        comtypes.CoInitialize()
        from comtypes import GUID
        import ctypes
        from ctypes import wintypes

        # IUIAutomation CLSID 和 IID
        CLSID_CUIAutomation = GUID("{FF48DBA4-60EF-4201-AA87-54103EEF594E}")
        IID_IUIAutomation = GUID("{30CBE57D-D9D0-452A-AB13-7AC5AC4825EE}")

        # 创建 IUIAutomation 实例
        uia_com = comtypes.CoCreateInstance(
            CLSID_CUIAutomation,
            interface=None,
            clsctx=comtypes.CLSCTX_INPROC_SERVER,
        )
        log(f"  IUIAutomation COM 对象创建成功: {type(uia_com)}")

        # 尝试获取 Root Element
        # 使用 QueryInterface 获取 IUIAutomation 接口
        try:
            # 直接通过 comtypes 的自动生成来获取方法
            root_element = uia_com.GetRootElement()
            if root_element:
                name = root_element.CurrentName
                log(f"  根元素: Name='{name}'")

                # 尝试从 hwnd 获取元素
                element = uia_com.ElementFromHandle(main_hwnd)
                if element:
                    name = element.CurrentName
                    ctype = element.CurrentControlType
                    log(f"  微信元素: Name='{name}' ControlType={ctype}")

                    # 获取子元素
                    try:
                        # TreeWalker
                        walker = uia_com.RawViewWalker
                        child = walker.GetFirstChildElement(element)
                        child_count = 0
                        while child:
                            cn = child.CurrentName
                            ct = child.CurrentControlType
                            aid = child.CurrentAutomationId
                            log(f"    子元素: Name='{cn}' ControlType={ct} AutomationId='{aid}'")
                            child_count += 1
                            child = walker.GetNextSiblingElement(child)
                            if child_count > 20:
                                break
                        log(f"  总共找到 {child_count} 个子元素")

                        if child_count > 1:
                            log("  ✅ 原生 COM 接口可以看到控件树！")
                            return True
                        else:
                            log("  ❌ 原生 COM 接口也只看到很少的子元素")
                            return False
                    except Exception as e:
                        log(f"  子元素遍历异常: {e}")
                else:
                    log("  ❌ ElementFromHandle 返回 None")
            else:
                log("  ❌ GetRootElement 返回 None")
        except Exception as e:
            log(f"  COM 调用异常: {e}")
            import traceback
            traceback.print_exc()

    except Exception as e:
        log(f"  原生 COM 创建异常: {e}")
        import traceback
        traceback.print_exc()
    return False

raw_result = test_raw_uia()


# ═══════════════════════════════════════════════
# 5. 使用 UIAutomationCore.dll 直接调用
# ═══════════════════════════════════════════════
section("5. 通过 ctypes 直接加载 UIAutomationCore.dll")

try:
    # 检查 DLL 是否存在
    sys32 = os.path.join(os.environ["SYSTEMROOT"], "System32")
    uiacore_path = os.path.join(sys32, "UIAutomationCore.dll")
    if os.path.exists(uiacore_path):
        size_mb = os.path.getsize(uiacore_path) / (1024*1024)
        log(f"✅ UIAutomationCore.dll 存在: {uiacore_path} ({size_mb:.1f} MB)")
    else:
        log(f"❌ UIAutomationCore.dll 不存在: {uiacore_path}")

    # 检查 SysWOW64 版本
    syswow = os.path.join(os.environ["SYSTEMROOT"], "SysWOW64")
    uiacore_wow = os.path.join(syswow, "UIAutomationCore.dll")
    if os.path.exists(uiacore_wow):
        size_mb2 = os.path.getsize(uiacore_wow) / (1024*1024)
        log(f"✅ SysWOW64 版本也存在: {uiacore_wow} ({size_mb2:.1f} MB)")
    else:
        log(f"ℹ SysWOW64 版本不存在（正常，如果 Python 是 64-bit 就不需要）")

except Exception as e:
    log(f"DLL 检查异常: {e}")


# ═══════════════════════════════════════════════
# 6. 清理 comtypes 缓存后重试
# ═══════════════════════════════════════════════
section("6. 清理 comtypes gen 缓存后重试")

def test_after_cache_clear():
    try:
        import comtypes
        cache_dir = os.path.join(comtypes.__path__[0], "gen")
        if os.path.exists(cache_dir):
            import shutil
            # 只备份不删除，先尝试重命名
            backup = cache_dir + "_backup_" + str(int(time.time()))
            try:
                shutil.copytree(cache_dir, backup)
                # 删除 gen 目录下所有 .py 文件（保留 __init__.py）
                for f in os.listdir(cache_dir):
                    if f.endswith('.py') and f != '__init__.py':
                        os.remove(os.path.join(cache_dir, f))
                    elif f.endswith('.pyc'):
                        os.remove(os.path.join(cache_dir, f))
                log(f"  已清理 comtypes/gen/ 缓存（备份到 {os.path.basename(backup)}）")
            except Exception as e:
                log(f"  清理缓存失败: {e}")
                return

            # 清理 __pycache__
            pycache = os.path.join(cache_dir, "__pycache__")
            if os.path.exists(pycache):
                shutil.rmtree(pycache, ignore_errors=True)

        # 在新进程中测试（确保干净的 comtypes 环境）
        test_code = f"""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
import comtypes
comtypes.CoInitialize()
import uiautomation as uia
root = uia.ControlFromHandle({main_hwnd})
if root:
    children = root.GetChildren()
    print(f"子控件数: {{len(children)}}")
    for c in children[:10]:
        ct = getattr(c, 'ControlTypeName', '') or ''
        cn = getattr(c, 'Name', '') or ''
        aid = getattr(c, 'AutomationId', '') or ''
        print(f"  [{{ct}}] Name='{{cn}}' aid='{{aid}}'")
    if len(children) > 1:
        print("RESULT:SUCCESS")
    else:
        print("RESULT:FAIL")
else:
    print("RESULT:FAIL - ControlFromHandle returned None")
"""
        result = subprocess.run(
            [sys.executable, "-c", test_code],
            capture_output=True, text=True, timeout=15,
            env={**os.environ, "QT_ACCESSIBILITY": "1"}
        )
        output = result.stdout.strip()
        log(f"  子进程输出:")
        for line in output.split("\n"):
            log(f"    {line}")

        if "RESULT:SUCCESS" in output:
            log("  ✅ 清理缓存后可以看到控件树了！")
        else:
            log("  ❌ 清理缓存后仍然无法看到控件树")
            if result.stderr:
                log(f"  stderr: {result.stderr[:300]}")

    except Exception as e:
        log(f"  缓存清理测试异常: {e}")
        import traceback
        traceback.print_exc()

test_after_cache_clear()


# ═══════════════════════════════════════════════
# 7. uiautomation 库版本
# ═══════════════════════════════════════════════
section("7. 依赖库版本信息")

try:
    import uiautomation
    log(f"uiautomation 版本: {uiautomation.VERSION}")
except Exception:
    try:
        import pkg_resources
        ver = pkg_resources.get_distribution("uiautomation").version
        log(f"uiautomation 版本: {ver}")
    except Exception:
        log("无法获取 uiautomation 版本")

try:
    import comtypes
    log(f"comtypes 版本: {comtypes.__version__}")
except Exception:
    log("无法获取 comtypes 版本")

try:
    import pywin32_system32
    log(f"pywin32 路径: {pywin32_system32.__path__}")
except Exception:
    pass

# pip list 中相关包
result = subprocess.run(
    [sys.executable, "-m", "pip", "list", "--format=columns"],
    capture_output=True, text=True, timeout=10
)
for line in result.stdout.split("\n"):
    lower = line.lower()
    if any(k in lower for k in ["uiautomation", "comtypes", "pywin32", "pywinauto"]):
        log(f"  {line.strip()}")


# ═══════════════════════════════════════════════
# 总结
# ═══════════════════════════════════════════════
section("诊断总结")
log(f"Python: {python_bits}-bit {sys.version.split()[0]}")
log(f"微信 hwnd: {main_hwnd}, PID: {wx_pid}")
log("讲述人可以识别 = Qt Accessibility 已激活（确认）")
log("")
log("如果上面所有测试都是 1 个子控件，问题出在:")
log("  A) Python 与微信位数不匹配 (最常见)")
log("  B) comtypes COM 缓存损坏")
log("  C) UIA COM 注册问题")
log("")
log("请将此输出发给开发者。")
