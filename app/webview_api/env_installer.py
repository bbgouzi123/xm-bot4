from __future__ import annotations
import os
import sys
import tempfile
import threading
import json
import logging
import subprocess
import winreg
import ctypes
from app.bootstrap.env_consts import (
    WEBVIEW2_DOWNLOAD_URL, WEBVIEW2_FALLBACK_URL,
    DOTNET_DOWNLOAD_URL, DOTNET_FALLBACK_URL,
    VCREDIST_DOWNLOAD_URL, VCREDIST_FALLBACK_URL,
    VBCABLE_DOWNLOAD_URL, VBCABLE_FALLBACK_URL,
    detect_webview2, detect_dotnet_472, detect_vcredist, detect_vbcable,
    find_webview2_uninstall_cmd, find_vcredist_uninstall_string, find_vbcable_uninstall_string
)
from app.bootstrap.env_installer import download_with_progress, run_installer

logger = logging.getLogger(__name__)

class EnvInstallerMixin:
    def get_env_status(self) -> dict:
        """获取系统关键环境依赖的当前检测状态"""
        try:
            return {
                "webview2": bool(detect_webview2()),
                "dotnet": bool(detect_dotnet_472()),
                "vcredist": bool(detect_vcredist()),
                "vbcable": bool(detect_vbcable())
            }
        except Exception as e:
            logger.error(f"获取运行环境状态失败: {e}")
            return {
                "webview2": False,
                "dotnet": False,
                "vcredist": False,
                "vbcable": False
            }

    def start_install_dependency(self, key: str) -> bool:
        """后台启动指定环境依赖组件的静默下载和安装，并在前端推送进度回调"""
        if getattr(self, "_installing_deps", None) is None:
            self._installing_deps = set()

        if key in self._installing_deps:
            return False

        config_map = {
            "webview2": {
                "name": "WebView2 运行时",
                "download_url": WEBVIEW2_DOWNLOAD_URL,
                "fallback_url": WEBVIEW2_FALLBACK_URL,
                "filename": "MicrosoftEdgeWebview2Setup.exe",
                "silent_args": ["/install"],
                "recheck_fn": detect_webview2
            },
            "dotnet": {
                "name": ".NET Framework 4.8",
                "download_url": DOTNET_DOWNLOAD_URL,
                "fallback_url": DOTNET_FALLBACK_URL,
                "filename": "ndp48-x86-x64-allos-enu.exe",
                "silent_args": ["/q", "/norestart"],
                "recheck_fn": detect_dotnet_472
            },
            "vcredist": {
                "name": "Visual C++ 2015-2022 运行时 (x64)",
                "download_url": VCREDIST_DOWNLOAD_URL,
                "fallback_url": VCREDIST_FALLBACK_URL,
                "filename": "vc_redist.x64.exe",
                "silent_args": ["/quiet", "/norestart"],
                "recheck_fn": detect_vcredist
            },
            "vbcable": {
                "name": "VB-Cable 虚拟音频驱动",
                "download_url": VBCABLE_DOWNLOAD_URL,
                "fallback_url": VBCABLE_FALLBACK_URL,
                "filename": "VBCABLE_Driver_Pack45.zip",
                "silent_args": ["-i", "-h"],
                "recheck_fn": detect_vbcable
            }
        }

        cfg = config_map.get(key)
        if not cfg:
            return False

        self._installing_deps.add(key)

        def worker_task():
            try:
                self._send_installer_status(key, "downloading")
                tmp_dir = tempfile.mkdtemp(prefix="xm_bot4_env_api_")
                dest_path = os.path.join(tmp_dir, cfg["filename"])

                def progress_cb(pct: int, speed: str):
                    self._send_installer_progress(key, pct, speed)

                ok = download_with_progress(
                    cfg["download_url"],
                    dest_path,
                    progress_cb,
                    cfg["fallback_url"]
                )

                if not ok or not os.path.exists(dest_path):
                    logger.error(f"[EnvInstaller] {cfg['name']} 下载失败")
                    self._send_installer_status(key, "failed")
                    self._cleanup_dir(tmp_dir)
                    return

                self._send_installer_status(key, "installing")
                if key == "vbcable":
                    import zipfile
                    extract_dir = os.path.join(tmp_dir, "vbcable_extracted")
                    os.makedirs(extract_dir, exist_ok=True)
                    try:
                        with zipfile.ZipFile(dest_path, 'r') as zip_ref:
                            zip_ref.extractall(extract_dir)
                        exe_path = os.path.join(extract_dir, "VBCABLE_Setup_x64.exe")
                    except Exception as zip_err:
                        logger.error(f"[EnvInstaller] 解压 VBCable 失败: {zip_err}")
                        self._send_installer_status(key, "failed")
                        self._cleanup_dir(tmp_dir)
                        return
                    exit_code = run_installer(exe_path, cfg["silent_args"])
                else:
                    exit_code = run_installer(dest_path, cfg["silent_args"])
                self._cleanup_dir(tmp_dir)

                if exit_code in (0, 3010, 1641):
                    recheck = cfg["recheck_fn"]
                    if recheck() or (recheck()):
                        if exit_code in (3010, 1641):
                            self._send_installer_status(key, "need_restart")
                        else:
                            self._send_installer_status(key, "success")
                    else:
                        self._send_installer_status(key, "need_restart")
                else:
                    logger.error(f"[EnvInstaller] {cfg['name']} 安装失败，退出码: {exit_code}")
                    self._send_installer_status(key, "failed")

            except Exception as e:
                logger.error(f"[EnvInstaller] 自动安装任务异常: {e}", exc_info=True)
                self._send_installer_status(key, "failed")
            finally:
                if key in self._installing_deps:
                    self._installing_deps.remove(key)

        threading.Thread(target=worker_task, daemon=True).start()
        return True

    def start_uninstall_dependency(self, key: str) -> bool:
        """后台启动指定环境依赖的静默卸载任务，并向前端推送回调"""
        if getattr(self, "_uninstalling_deps", None) is None:
            self._uninstalling_deps = set()

        if key in self._uninstalling_deps:
            return False

        self._uninstalling_deps.add(key)

        def uninstall_worker():
            try:
                self._send_installer_status(key, "uninstalling")
                success = False

                if key == "webview2":
                    cmd = find_webview2_uninstall_cmd()
                    if cmd:
                        logger.info(f"[EnvUninstall] 运行 WebView2 静默卸载: {cmd}")
                        res = subprocess.run(cmd, timeout=300, creationflags=subprocess.CREATE_NO_WINDOW)
                        success = (res.returncode == 0)
                    else:
                        logger.warning("[EnvUninstall] 未找到 WebView2 卸载 setup.exe，尝试通过控制面板引导")
                        self._guide_manual_optional_features("WebView2 运行时需要通过系统控制面板卸载。")
                        success = False
                elif key == "vcredist":
                    un_str = find_vcredist_uninstall_string()
                    if un_str:
                         success = self._run_uninstall_string(un_str)
                    else:
                        logger.warning("[EnvUninstall] 未在注册表中找到 VC++ 运行时的卸载指令")
                        success = False
                elif key == "vbcable":
                    un_str = find_vbcable_uninstall_string()
                    if un_str:
                         success = self._run_uninstall_string(un_str)
                    else:
                        logger.warning("[EnvUninstall] 未在注册表中找到 VB-Cable 的卸载指令")
                        success = False
                elif key == "dotnet":
                    # .NET Framework 属于 Windows 系统内置核心组件，强行卸载风险极大
                    # 引导用户通过 OptionalFeatures.exe 面板关闭
                    self._guide_manual_optional_features(
                        "【.NET Framework 4.8】是 Windows 核心组件。\n\n"
                        "卸载或关闭此组件可能导致部分 UIA 自动化程序及系统级服务瘫痪。\n"
                        "点击确定将为您打开系统的「启用或关闭 Windows 功能」面板，请在其中手动关闭。"
                    )
                    success = False

                # 刷新检测状态
                recheck_fn = (
                    detect_webview2 if key == "webview2"
                    else (detect_dotnet_472 if key == "dotnet"
                    else (detect_vbcable if key == "vbcable"
                    else detect_vcredist))
                )
                if not recheck_fn():
                    self._send_installer_status(key, "idle")
                else:
                    self._send_installer_status(key, "failed" if not success else "need_restart")

            except Exception as e:
                logger.error(f"[EnvUninstall] 卸载任务执行异常: {e}", exc_info=True)
                self._send_installer_status(key, "failed")
            finally:
                if key in self._uninstalling_deps:
                    self._uninstalling_deps.remove(key)

        threading.Thread(target=uninstall_worker, daemon=True).start()
        return True

    def _guide_manual_optional_features(self, msg: str):
        try:
            ctypes.windll.user32.MessageBoxW(0, msg, "xm-bot4 - 系统组件卸载引导", 0 | 48 | 8192)
            subprocess.Popen(["OptionalFeatures.exe"], shell=True)
        except Exception as e:
            logger.error(f"启动 Windows Features 面板失败: {e}")

    def _run_uninstall_string(self, uninstall_str: str) -> bool:
        import shlex
        try:
            args = shlex.split(uninstall_str)
            if not args:
                return False
            cmd = args[0]
            params = args[1:]

            if "msiexec" in cmd.lower():
                new_params = []
                for p in params:
                    if p.upper().startswith("/I"):
                        new_params.append("/X")
                    elif p.upper().startswith("/M"):
                        new_params.append(p)
                    else:
                        new_params.append(p)
                if "/qn" not in [p.lower() for p in new_params]:
                    new_params.extend(["/qn", "/norestart"])
                args = [cmd] + new_params
            else:
                if "/uninstall" not in [p.lower() for p in params] and "--uninstall" not in [p.lower() for p in params]:
                    args.append("/uninstall")
                args.extend(["/quiet", "/norestart"])

            logger.info(f"[EnvUninstall] 智能改写卸载命令并执行: {args}")
            res = subprocess.run(args, timeout=300, creationflags=subprocess.CREATE_NO_WINDOW)
            return res.returncode == 0
        except Exception as e:
            logger.error(f"[EnvUninstall] 执行卸载命令行失败: {e}")
            return False

    def _send_installer_status(self, key: str, status: str):
        if not self._window:
            return
        script = (
            "if(window.__PyEnvInstallerCallback && window.__PyEnvInstallerCallback.onStatus) "
            f"window.__PyEnvInstallerCallback.onStatus('{key}', '{status}');"
        )
        try:
            self._window.evaluate_js(script)
        except Exception:
            pass

    def _send_installer_progress(self, key: str, pct: int, speed: str):
        if not self._window:
            return
        speed_esc = json.dumps(speed)
        script = (
            "if(window.__PyEnvInstallerCallback && window.__PyEnvInstallerCallback.onProgress) "
            f"window.__PyEnvInstallerCallback.onProgress('{key}', {pct}, {speed_esc});"
        )
        try:
            self._window.evaluate_js(script)
        except Exception:
            pass

    def _cleanup_dir(self, path: str):
        try:
            import shutil
            shutil.rmtree(path, ignore_errors=True)
        except Exception:
            pass
