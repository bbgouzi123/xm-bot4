"""运行环境组件下载地址常量 + 各组件检测函数。"""
from __future__ import annotations

import os
import ctypes
import winreg
import logging

logger = logging.getLogger("EnvCheck")

# ── 下载地址（CDN 优先，微软官方兜底） ────────────────────────────────────────

# WebView2 Evergreen Bootstrapper（约 2 MB，安装后自动拉取完整运行时）
WEBVIEW2_DOWNLOAD_URL = (
    "https://msedge.sf.dl.delivery.mp.microsoft.com/filestreamingservice/files/"
    "35c7968d-2251-441b-be9f-e30fe4f3f8b0/MicrosoftEdgeWebview2Setup.exe"
)
WEBVIEW2_FALLBACK_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"

# .NET Framework 4.8 离线包（约 120 MB）
DOTNET_DOWNLOAD_URL = (
    "https://download.visualstudio.microsoft.com/download/pr/"
    "2d6bb6b2-226a-4baa-bdec-798822606ff1/8494001c276a4b96804cde7829c04d7f/"
    "ndp48-x86-x64-allos-enu.exe"
)
DOTNET_FALLBACK_URL = "https://dotnet.microsoft.com/en-us/download/dotnet-framework/net48"

# VC++ 2015-2022 x64 Redistributable（约 25 MB）
VCREDIST_DOWNLOAD_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
VCREDIST_FALLBACK_URL = (
    "https://learn.microsoft.com/zh-cn/cpp/windows/latest-supported-vc-redist"
)

# VB-Cable 虚拟音频驱动（约 2 MB）
VBCABLE_DOWNLOAD_URL = "https://xmcore.top/releases/xm-bot4/depends/VBCABLE_Driver_Pack45.zip"
VBCABLE_FALLBACK_URL = "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip"

DOWNLOAD_TIMEOUT = 300  # 秒


# ── 各组件检测函数 ────────────────────────────────────────────────────────────

def detect_webview2() -> bool:
    """检测 WebView2 运行时是否已安装（同时校验物理文件与注册表）。"""
    logger.debug("[环境自检] 开始排查 Edge WebView2 运行时状态...")
    # 1. 优先检查物理文件是否存在，若完全不存在则直接判定为未安装（防止注册表残留干扰）
    pf_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    pf_native = os.environ.get("ProgramFiles", r"C:\Program Files")
    local_app = os.environ.get("LocalAppData", "")
    paths = [
        os.path.join(pf_x86, "Microsoft", "EdgeWebView", "Application"),
        os.path.join(pf_native, "Microsoft", "EdgeWebView", "Application"),
        os.path.join(local_app, "Microsoft", "EdgeWebView", "Application")
    ]
    has_files = False
    found_paths = []
    for base in paths:
        if os.path.exists(base):
            try:
                for ver in os.listdir(base):
                    exe_p = os.path.join(base, ver, "msedgewebview2.exe")
                    if os.path.exists(exe_p):
                        has_files = True
                        found_paths.append((exe_p, ver))
            except Exception:
                pass

    if found_paths:
        logger.debug(f"[环境自检] 物理文件检测成功：共发现 {len(found_paths)} 个 WebView2 目录:")
        for exe_p, ver in found_paths:
            logger.debug(f"  - 路径: {exe_p} | 版本: {ver}")
    else:
        logger.warning("[环境自检] ❌ 未在 Program Files 或 LocalAppData 下检索到 msedgewebview2.exe 物理文件")

    if not has_files:
        return False

    # 2. 检查注册表
    wv2_guids = [
        "{F241C743-01ED-459E-9A96-41904791E885}",
        "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
        "{56EB18F8-B008-4CBD-B6D2-8C97FE7E9062}",
    ]
    for root_key in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
        root_name = "HKLM" if root_key == winreg.HKEY_LOCAL_MACHINE else "HKCU"
        for guid in wv2_guids:
            for prefix in [
                r"SOFTWARE\Microsoft\EdgeUpdate\Clients",
                r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients",
            ]:
                try:
                    key = winreg.OpenKey(root_key, f"{prefix}\\{guid}", 0, winreg.KEY_READ)
                    try:
                        pv, _ = winreg.QueryValueEx(key, "pv")
                        if pv and str(pv).strip():
                            winreg.CloseKey(key)
                            logger.debug(f"[环境自检] 注册表检测成功: {root_name}\\{prefix}\\{guid} 含有 pv={pv}")
                            return True
                    except Exception:
                        pass
                    winreg.CloseKey(key)
                except FileNotFoundError:
                    continue
    # 兜底：卸载列表
    for root in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
        root_name = "HKLM" if root == winreg.HKEY_LOCAL_MACHINE else "HKCU"
        for prefix in [
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        ]:
            try:
                key = winreg.OpenKey(
                    root, f"{prefix}\\Microsoft Edge WebView2 Runtime", 0, winreg.KEY_READ
                )
                try:
                    dv, _ = winreg.QueryValueEx(key, "DisplayVersion")
                    if dv and str(dv).strip():
                        winreg.CloseKey(key)
                        logger.debug(f"[环境自检] 卸载列表检测成功: {root_name}\\{prefix} 含有 DisplayVersion={dv}")
                        return True
                except Exception:
                    pass
                winreg.CloseKey(key)
            except FileNotFoundError:
                continue
    # 如果物理文件存在，但注册表因为权限或未注册等特殊情况没查到，依然信任物理文件
    logger.warning("[环境自检] ⚠️ 物理文件完好但注册表缺失或损坏，将继续以物理文件为准启动...")
    return True


def detect_dotnet_472() -> bool:
    """检测 .NET Framework 4.7.2+ 是否已安装（release >= 461808）。"""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full",
        )
        release, _ = winreg.QueryValueEx(key, "Release")
        winreg.CloseKey(key)
        return release >= 461808
    except Exception:
        return False


def detect_vcredist() -> bool:
    """检测 VC++ 2015-2022 x64 运行时是否已安装且未损坏（以 System32 物理文件完好为核心判断标准）。"""
    sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    # 核心 DLL，若缺失或损坏（大小为 0）则判定为未就绪
    for dll in ["msvcp140.dll", "vcruntime140.dll"]:
        p = os.path.join(sys32, dll)
        if not os.path.exists(p) or os.path.getsize(p) == 0:
            return False
    return True


def detect_vbcable() -> bool:
    """检测 VB-Cable 虚拟音频驱动是否已安装。"""
    keywords = ["vbcable", "vb-audio virtual cable", "vb-cable", "vb-audio cable", "virtual audio cable"]
    for root in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
        for prefix in [
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        ]:
            try:
                reg_key = winreg.OpenKey(root, prefix, 0, winreg.KEY_READ)
                i = 0
                while True:
                    try:
                        sub_name = winreg.EnumKey(reg_key, i)
                        try:
                            sub_key = winreg.OpenKey(reg_key, sub_name, 0, winreg.KEY_READ)
                            try:
                                dn, _ = winreg.QueryValueEx(sub_key, "DisplayName")
                                dn_str = str(dn).lower()
                                if any(k in dn_str for k in keywords):
                                    winreg.CloseKey(sub_key)
                                    winreg.CloseKey(reg_key)
                                    return True
                            except Exception:
                                pass
                            winreg.CloseKey(sub_key)
                        except Exception:
                            pass
                        i += 1
                    except OSError:
                        break
                winreg.CloseKey(reg_key)
            except Exception:
                continue
    return False


def find_vcredist_uninstall_string() -> str | None:
    vc_kws = ["Visual C++ 2015", "Visual C++ 2017", "Visual C++ 2019", "Visual C++ 2022"]
    for root in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
        for prefix in [
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        ]:
            try:
                reg_key = winreg.OpenKey(root, prefix, 0, winreg.KEY_READ)
                i = 0
                while True:
                    try:
                        sub_name = winreg.EnumKey(reg_key, i)
                        try:
                            sub_key = winreg.OpenKey(reg_key, sub_name, 0, winreg.KEY_READ)
                            try:
                                dn, _ = winreg.QueryValueEx(sub_key, "DisplayName")
                                if any(k in str(dn) for k in vc_kws) and ("x64" in str(dn) or "Redistributable" in str(dn)):
                                    us, _ = winreg.QueryValueEx(sub_key, "UninstallString")
                                    winreg.CloseKey(sub_key)
                                    winreg.CloseKey(reg_key)
                                    return str(us)
                            except Exception:
                                pass
                            winreg.CloseKey(sub_key)
                        except Exception:
                            pass
                        i += 1
                    except OSError:
                        break
                winreg.CloseKey(reg_key)
            except Exception:
                continue
    return None


def find_vbcable_uninstall_string() -> str | None:
    kws = ["VB-Audio Virtual Cable", "VB-Cable", "VB-Audio Cable"]
    for root in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
        for prefix in [
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        ]:
            try:
                reg_key = winreg.OpenKey(root, prefix, 0, winreg.KEY_READ)
                i = 0
                while True:
                    try:
                        sub_name = winreg.EnumKey(reg_key, i)
                        try:
                            sub_key = winreg.OpenKey(reg_key, sub_name, 0, winreg.KEY_READ)
                            try:
                                dn, _ = winreg.QueryValueEx(sub_key, "DisplayName")
                                if any(k in str(dn) for k in kws):
                                    us, _ = winreg.QueryValueEx(sub_key, "UninstallString")
                                    winreg.CloseKey(sub_key)
                                    winreg.CloseKey(reg_key)
                                    return str(us)
                            except Exception:
                                pass
                            winreg.CloseKey(sub_key)
                        except Exception:
                            pass
                        i += 1
                    except OSError:
                        break
                winreg.CloseKey(reg_key)
            except Exception:
                continue
    return None


def find_webview2_uninstall_cmd() -> list[str] | None:
    pf_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local_app = os.environ.get("LocalAppData", "")
    paths = [
        os.path.join(pf_x86, "Microsoft", "EdgeWebView", "Application"),
        os.path.join(local_app, "Microsoft", "EdgeWebView", "Application")
    ]
    for base in paths:
        if not os.path.exists(base):
            continue
        try:
            for ver in os.listdir(base):
                setup_path = os.path.join(base, ver, "Installer", "setup.exe")
                if os.path.exists(setup_path):
                    return [setup_path, "--uninstall", "--msi", "--system-level", "--force-uninstall"]
        except Exception:
            pass
    return None


