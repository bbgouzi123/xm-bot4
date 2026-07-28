from __future__ import annotations
import sys
from pathlib import Path

class UpdaterMixin:
    @staticmethod
    def _xm_bot4_app_dir():
        """xm-bot4 应用根目录（frozen 用 exe 目录；开发环境为 backend-python 根目录）。"""
        if getattr(sys, 'frozen', False):
            return Path(sys.executable).parent
        return Path(__file__).resolve().parent.parent.parent

    def _build_updater(self, app_key: str, current_version: str):
        from xm_py_updater import XMUpdater
        app_dir = self._xm_bot4_app_dir()
        return XMUpdater(
            app_key=app_key,
            current_version=current_version,
            download_dir=app_dir / "update",
            target_dir=app_dir,
        )

    def check_update(self, app_key: str, current_version: str):
        """检查是否有新版本（供全局 @xm/updater 库调用）。"""
        import logging
        import requests as _requests
        logger = logging.getLogger(__name__)
        try:
            from xm_py_updater import XMUpdater
            updater = self._build_updater(app_key, current_version)
            data = updater.fetch_latest_sync()
            if not data or not data.get("version"):
                if not getattr(updater, "_last_store_reachable", True):
                    return {
                        "has_update": False,
                        "error": (
                            "无法从版本源获取元数据（本机 xm-store 未启动、网关 502 或响应异常）。"
                            "开发环境可执行 pnpm dev xm-store（默认 :42003），或设置 XM_STORE_URL / XM_STORE_USE_PROD=1。"
                        ),
                    }
                return {"has_update": False}

            latest_ver = str(data.get("version") or "").strip()
            if not latest_ver or XMUpdater.compare_versions(latest_ver, current_version) <= 0:
                return {"has_update": False}

            return {
                "has_update": True,
                "version": latest_ver,
                "changelog": data.get("changelog", "") or "",
                "is_forced": bool(data.get("force_update", False)),
                "download_url": data.get("download_url", "") or "",
            }
        except _requests.exceptions.ConnectionError:
            return {"has_update": False, "error": "无法连接版本服务器，请检查网络后重试。"}
        except Exception as e:
            logger.error(f"检查更新异常: {e}", exc_info=True)
            return {"has_update": False, "error": "检查更新时发生错误，请稍后再试。"}

    def start_download_update(self, version: str, url: str):
        """开启后台下载更新包任务（下载 → 解压 → 替换 → 重启，完全下沉到全局零件）。"""
        if getattr(self, "_downloading_version", None) == version:
            return True
        self._downloading_version = version

        import threading

        def download_task():
            import json
            try:
                from xm_py_updater import UpdateInfo
                updater = self._build_updater("xm-bot4", version)

                def on_progress(downloaded, total):
                    if self._window:
                        script = (
                            "if(window.__PyUpdaterCallback) "
                            f"window.__PyUpdaterCallback.onProgress({downloaded}, {total});"
                        )
                        try:
                            self._window.evaluate_js(script)
                        except Exception:
                            pass

                info = UpdateInfo(version=version, download_url=url)
                installer_path = updater.download_sync(info, on_progress=on_progress)
                if installer_path is None:
                    if self._window:
                        err = json.dumps("下载失败，请检查网络后重试。")
                        try:
                            self._window.evaluate_js(
                                f"if(window.__PyUpdaterCallback) window.__PyUpdaterCallback.onError({err});"
                            )
                        except Exception:
                            pass
                    return

                if self._window:
                    try:
                        self._window.evaluate_js(
                            "if(window.__PyUpdaterCallback) window.__PyUpdaterCallback.onFinished();"
                        )
                    except Exception:
                        pass
                # 开发环境（python main.py）切勿把发行 zip 覆盖到源码树，否则会散落成百上千
                # api-ms-win-*.dll 等运行库，污染 Git；正式升级仅在打包 exe 旁执行。
                if not getattr(sys, "frozen", False):
                    if self._window:
                        info = json.dumps(
                            "开发版已保存至 update/，未自动安装；完整升级请用安装包客户端。"
                        )
                        try:
                            self._window.evaluate_js(
                                "if(window.__PyUpdaterCallback && window.__PyUpdaterCallback.onInfo) "
                                f"window.__PyUpdaterCallback.onInfo({info});"
                            )
                        except Exception:
                            pass
                    return

                updater.install(installer_path, restart=True)

            except Exception as e:
                if self._window:
                    err = json.dumps(f"更新异常: {str(e)}")
                    try:
                        self._window.evaluate_js(
                            f"if(window.__PyUpdaterCallback) window.__PyUpdaterCallback.onError({err});"
                        )
                    except Exception:
                        pass
            finally:
                self._downloading_version = None

        threading.Thread(target=download_task, daemon=True).start()
        return True
