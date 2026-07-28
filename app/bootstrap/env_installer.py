"""运行环境组件自动下载与静默安装执行器。"""
from __future__ import annotations

import os
import sys
import time
import ctypes
import threading
import subprocess
import tempfile
import webbrowser
import urllib.request
from typing import Callable

from app.bootstrap.env_consts import DOWNLOAD_TIMEOUT


# ── 下载 ─────────────────────────────────────────────────────────────────────

def download_with_progress(
    url: str,
    dest_path: str,
    progress_cb: Callable[[int, str], None] | None = None,
    fallback_url: str = "",
) -> bool:
    """
    下载文件到 dest_path。progress_cb(percent, speed_str)。
    先试主 URL，失败后试 fallback_url。返回是否成功。
    """
    def _do(target: str) -> bool:
        try:
            req = urllib.request.Request(
                target,
                headers={"User-Agent": "Mozilla/5.0 xm-bot4-env-installer/1.0"},
            )
            with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
                total = int(resp.headers.get("Content-Length", 0) or 0)
                downloaded = 0
                start_t = time.time()
                with open(dest_path, "wb") as f:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_cb and total > 0:
                            pct = min(99, int(downloaded * 100 / total))
                            elapsed = max(0.1, time.time() - start_t)
                            speed = downloaded / elapsed / 1024
                            speed_s = (
                                f"{speed/1024:.1f} MB/s" if speed > 1024 else f"{speed:.0f} KB/s"
                            )
                            progress_cb(pct, speed_s)
            if progress_cb:
                progress_cb(100, "下载完成")
            return True
        except Exception as e:
            print(f"[下载失败] {target}: {e}")
            return False

    if _do(url):
        return True
    if fallback_url and fallback_url != url:
        print("[下载] 主源失败，尝试备用地址...")
        return _do(fallback_url)
    return False


# ── 安装 ─────────────────────────────────────────────────────────────────────

def run_installer(installer_path: str, silent_args: list[str]) -> int:
    """静默执行安装程序，返回退出码（0=成功, 3010=需重启）。"""
    try:
        cmd = [installer_path] + silent_args
        print(f"[安装] 执行: {' '.join(cmd)}")
        result = subprocess.run(cmd, timeout=600, creationflags=subprocess.CREATE_NO_WINDOW)
        print(f"[安装] 退出码: {result.returncode}")
        return result.returncode
    except subprocess.TimeoutExpired:
        print("[安装] 超时（10 分钟）")
        return -1
    except Exception as e:
        print(f"[安装] 执行失败: {e}")
        return -2


# ── 弹窗交互 ─────────────────────────────────────────────────────────────────

def ask_auto_install(component_name: str, size_hint: str) -> bool:
    """询问用户是否自动下载安装，返回 True=同意。"""
    msg = (
        f"检测到本机缺少【{component_name}】运行时组件，\n"
        f"这是程序正常运行的必需环境。\n\n"
        f"📦 安装包大小：约 {size_hint}\n"
        f"⚡ 将在后台静默安装，安装完成前请勿关闭程序。\n\n"
        f"是否立即自动下载并安装？\n"
        f"（点击「否」将打开官方页面手动安装）"
    )
    result = ctypes.windll.user32.MessageBoxW(0, msg, "xm-bot4 - 环境自检", 4 | 32 | 8192)
    return result == 6  # IDYES


def notify_installing(component_name: str) -> threading.Thread:
    """在独立线程显示"正在安装"弹窗（不阻塞主线程）。"""
    msg = (
        f"正在自动安装【{component_name}】...\n\n"
        f"请稍候，安装完成后程序将自动继续启动。"
    )
    t = threading.Thread(
        target=lambda: ctypes.windll.user32.MessageBoxW(
            0, msg, "xm-bot4 - 正在安装", 0 | 64 | 8192
        ),
        daemon=True,
    )
    t.start()
    return t


def close_notify_window():
    """关闭"正在安装"提示弹窗。"""
    try:
        hwnd = ctypes.windll.user32.FindWindowW(None, "xm-bot4 - 正在安装")
        if hwnd:
            ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
    except Exception:
        pass


def notify_success(component_name: str, need_restart: bool) -> bool:
    """安装成功提示，返回是否触发了重启。"""
    if need_restart:
        msg = (
            f"【{component_name}】已成功安装！\n\n"
            f"⚠️ 需要重启计算机才能完成配置。\n"
            f"是否立即重启？（点击「否」程序将退出）"
        )
        res = ctypes.windll.user32.MessageBoxW(0, msg, "xm-bot4 - 安装完成", 4 | 48 | 8192)
        if res == 6:
            subprocess.run(
                ["shutdown", "/r", "/t", "5", "/c", "xm-bot4 环境安装完成，即将重启"],
                check=False,
            )
        return True
    ctypes.windll.user32.MessageBoxW(
        0,
        f"【{component_name}】已成功安装！\n程序将自动重新启动以应用环境配置。",
        "xm-bot4 - 安装完成",
        0 | 64 | 8192,
    )
    return False


def notify_failed(component_name: str, fallback_url: str):
    """安装失败时引导用户手动安装。"""
    msg = (
        f"【{component_name}】自动安装失败。\n\n"
        f"请手动前往官方页面下载安装后重新启动程序。\n"
        f"是否打开官方下载页面？"
    )
    res = ctypes.windll.user32.MessageBoxW(0, msg, "xm-bot4 - 安装失败", 4 | 16 | 8192)
    if res == 6 and fallback_url:
        webbrowser.open(fallback_url)


# ── 完整单组件流程 ────────────────────────────────────────────────────────────

def auto_install_component(
    name: str,
    size_hint: str,
    download_url: str,
    fallback_url: str,
    filename: str,
    silent_args: list[str],
    recheck_fn: Callable[[], bool],
) -> bool:
    """
    完整的"检测→询问→下载→安装→验证"流程。
    返回 True = 组件已就绪，False = 仍缺失（调用方应终止启动）。
    """
    if recheck_fn():
        return True

    print(f"[环境自检] ⚠️ 缺失组件: {name}")

    if not ask_auto_install(name, size_hint):
        webbrowser.open(fallback_url)
        ctypes.windll.user32.MessageBoxW(
            0, f"请手动安装【{name}】后重新启动程序。", "xm-bot4 - 需手动安装", 0 | 48 | 8192
        )
        return False

    tmp_dir = tempfile.mkdtemp(prefix="xm_bot4_env_")
    installer_path = os.path.join(tmp_dir, filename)
    print(f"[下载] 开始下载 {name}...")

    def _progress(pct: int, speed: str):
        if pct % 10 == 0:
            print(f"[下载] {name} {pct}% ({speed})")

    ok = download_with_progress(download_url, installer_path, _progress, fallback_url)

    if not ok or not os.path.exists(installer_path):
        print(f"[下载] ❌ {name} 下载失败")
        notify_failed(name, fallback_url)
        _cleanup(tmp_dir)
        return False

    print(f"[安装] ✅ 下载完成，开始静默安装 {name}...")
    notify_installing(name)
    exit_code = run_installer(installer_path, silent_args)
    close_notify_window()
    _cleanup(tmp_dir)

    # 0=成功, 3010/1641=需要重启
    if exit_code in (0, 3010, 1641):
        need_restart = exit_code in (3010, 1641)
        if recheck_fn() or (time.sleep(5) or recheck_fn()):  # type: ignore[func-returns-value]
            print(f"[安装] ✅ {name} 安装成功")
            if notify_success(name, need_restart):
                sys.exit(0)
            
            # 不需要重启系统，则自动重启程序自身，确保新环境完全生效且干净启动
            try:
                import subprocess
                if getattr(sys, "frozen", False):
                    # 打包环境下，重新拉起 exe 并传递相同参数
                    subprocess.Popen([sys.executable] + sys.argv[1:])
                else:
                    # 源码开发环境下，重新拉起 python 脚本
                    subprocess.Popen([sys.executable] + sys.argv)
            except Exception as e:
                print(f"[启动] 自动重启失败: {e}")
                return True  # 自动重启失败则兜底在当前进程继续启动
                
            sys.exit(0)
        # 安装成功但注册表未刷新，提示需重启
        ctypes.windll.user32.MessageBoxW(
            0,
            f"【{name}】已安装，但需要重启计算机才能生效。\n程序将退出，请重启后再启动。",
            "xm-bot4 - 需要重启",
            0 | 48 | 8192,
        )
        sys.exit(0)
    else:
        print(f"[安装] ❌ {name} 安装失败，退出码: {exit_code}")
        notify_failed(name, fallback_url)
        return False


def _cleanup(tmp_dir: str):
    try:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        pass
