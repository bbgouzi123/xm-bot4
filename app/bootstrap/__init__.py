"""xm-bot4 引导与启动组装模块。"""
import os
import sys
import time
import threading
import traceback
from pathlib import Path

# 对外兼容导出（保持 Facade API 契约不变）
from app.bootstrap.server import register_app, flush_cloud_before_exit

from app.bootstrap.env_check import check_runtime_environment
from app.bootstrap.server import start_server, _server_started, _server_error
from app.bootstrap.loading import get_loading_html, transition_to_app
from app import constants

from app.bootstrap.dll_patch import apply_dll_patch
apply_dll_patch()

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


from app.bootstrap.single_instance import check_single_instance


def main():
    """xm-bot4 启动主入口。"""
    is_dev = "--dev" in sys.argv
    desktop_debug = "--f12" in sys.argv or constants.XM_PACKAGED_WITH_F12
    no_gui = "--no-gui" in sys.argv

    # ─── 0.5. GPU 与 Sandbox 沙箱故障自愈与自适应配置 ───
    disable_gpu = "--disable-gpu" in sys.argv
    disable_sandbox = "--no-sandbox" in sys.argv
    app_data = os.environ.get("APPDATA", os.path.expanduser("~"))
    config_dir = os.path.join(app_data, "xm-bot4")
    config_path = os.path.join(config_dir, "config.json")
    
    try:
        import json
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                if not disable_gpu and cfg.get("disable_gpu") is True:
                    disable_gpu = True
                if not disable_sandbox and cfg.get("disable_sandbox") is True:
                    disable_sandbox = True
    except Exception:
        pass

    # 针对超级管理员 (Built-in Administrator) 的兼容性自动兜底自愈：
    # 超级管理员账户且彻底禁用了 UAC (EnableLUA=0) 时，Chromium 沙箱会静默挂起。
    # 我们自动为其兜底激活无沙箱模式。
    if not disable_sandbox:
        try:
            import ctypes
            is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
            if is_admin:
                import getpass
                if getpass.getuser().lower() == "administrator":
                    disable_sandbox = True
                    print("[启动] 💡 探测到系统当前正以 Built-in Administrator 超级管理员账号运行，将自动关闭 WebView2 沙箱以防止 Chromium 初始化死锁...")
        except Exception:
            pass

    browser_args = []
    if disable_gpu:
        print("[启动] ⚠️ 正在关闭 WebView2 硬件加速以保证显卡兼容性...")
        browser_args.extend(["--disable-gpu", "--disable-gpu-rasterization"])
    if disable_sandbox:
        print("[启动] ⚠️ 正在关闭 WebView2 沙箱保护以防止超级管理员安全特权冲突造成的启动卡死...")
        browser_args.append("--no-sandbox")

    if browser_args:
        os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = " ".join(browser_args)

    # 1. Windows 环境自检（WebView2、.NET 4.7.2+、VC++）
    if not check_runtime_environment():
        sys.exit(1)

    # 1.5 Windows 单实例互斥锁与冲突检测 (防止多开打架强杀)
    if not check_single_instance():
        if not no_gui and getattr(sys, 'frozen', False):
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0,
                    "xm-bot4 智能助理已在运行中。\n\n请在系统托盘或任务栏中查找并双击打开已运行的实例，请勿重复启动多个实例。",
                    "xm-bot4 - 程序已在运行",
                    0x40  # MB_ICONINFORMATION
                )
            except Exception:
                pass
        else:
            print("[启动] 检测到另一个智能助理实例已在运行中，本实例温和退出，防止相互抢占端口")
        sys.exit(0)

    # 2. 异步启动本地 FastAPI 后端服务器
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    # 3. 运行 GUI 壳或纯服务器模式
    if not no_gui:
        try:
            import webview
            
            from app.bootstrap.webview_patch import apply_webview_patches
            apply_webview_patches()

            from app.webview_api import WebviewApi

            # 加载 Logo Base64，支持 SVG 格式以实现完美的无白色底盘透明效果
            logo_b64 = ""
            try:
                import urllib.parse
                logo_svg = """<svg t="1773079234089" class="icon" viewBox="0 0 1024 1024" version="1.1" xmlns="http://www.w3.org/2000/svg" p-id="7399" xmlns:xlink="http://www.w3.org/1999/xlink" width="256" height="256"><path d="M512 38.641509a96.603774 96.603774 0 0 1 28.981132 188.763774V386.415094h-57.962264v-159.009811A96.642415 96.642415 0 0 1 512 38.641509z" fill="#333C50" p-id="7400"></path><path d="M164.226415 463.698113a154.566038 154.566038 0 0 1 154.566038-154.566038h386.415094a154.566038 154.566038 0 0 1 154.566038 154.566038v347.773585a154.566038 154.566038 0 0 1-154.566038 154.566038h-386.415094a154.566038 154.566038 0 0 1-154.566038-154.566038V463.698113z" fill="#64edac" p-id="7401"></path><path d="M589.476226 712.433509a28.981132 28.981132 0 0 1 38.525585 43.297812L608.603774 734.188679a2292.388226 2292.388226 0 0 1 19.359396 21.561963l-0.057962 0.057962-0.115925 0.096604-0.25117 0.193207-0.618264 0.579623a106.070943 106.070943 0 0 1-8.153358 6.066717c-5.255245 3.535698-12.751698 7.998792-22.412076 12.365283-19.494642 8.771623-47.683623 17.040906-84.354415 17.040905-36.690113 0-64.859774-8.269283-84.335094-17.040905a150.798491 150.798491 0 0 1-22.450717-12.365283 106.070943 106.070943 0 0 1-8.134038-6.086038l-0.637585-0.540981-0.231849-0.212528-0.115925-0.096604-0.057962-0.057962S395.998189 755.731321 415.396226 734.188679l-19.398037 21.542642a29.000453 29.000453 0 0 1 38.641509-43.181887v-0.038642l-0.115924-0.077283-0.057963-0.077283 0.367095 0.309132c0.463698 0.386415 1.410415 1.101283 2.801509 2.02868 2.801509 1.893434 7.399849 4.675623 13.795019 7.535094 12.732377 5.738264 32.845283 11.959547 60.570566 11.959547 27.705962 0 47.838189-6.221283 60.570566-11.940226 6.375849-2.898113 11.01283-5.660981 13.775698-7.535095 1.410415-0.966038 2.337811-1.680906 2.82083-2.048l0.347774-0.309132-0.038642 0.077283zM898.415094 483.018868a96.603774 96.603774 0 0 1 96.603774 96.603774v115.924528a96.603774 96.603774 0 0 1-96.603774 96.603773V483.018868zM125.584906 792.150943a96.603774 96.603774 0 0 1-96.603774-96.603773v-115.924528a96.603774 96.603774 0 0 1 96.603774-96.603774v309.132075z" fill="#333C50" p-id="7402"></path><path d="M338.113208 579.622642a57.962264 38.641509 90 1 0 77.283018 0 57.962264 38.641509 90 1 0-77.283018 0Z" fill="#333C50" p-id="7403"></path><path d="M608.603774 579.622642a57.962264 38.641509 90 1 0 77.283018 0 57.962264 38.641509 90 1 0-77.283018 0Z" fill="#333C50" p-id="7404"></path></svg>"""
                logo_b64 = f"data:image/svg+xml;utf8,{urllib.parse.quote(logo_svg)}"
            except Exception:
                pass

            # 获取当前产品版本号
            try:
                from app.paths import xm_bot4_splash_app_version
                version_str = xm_bot4_splash_app_version()
                if not version_str:
                    version_str = "v1.0.0"
            except Exception:
                version_str = "v1.0.0"
            if not version_str.startswith("v"):
                version_str = f"v{version_str}"
            _splash_ver_html = f'<div class="app-version">{version_str}</div>'

            # 动态合成秒开预加载页 HTML（已完美修复普通大括号转义隐患）
            loading_html = get_loading_html(logo_b64, _splash_ver_html)

            js_api = WebviewApi()
            # 跳转到含 /xm-bot4/ base 路径的前端入口，与 vite.config.ts base: '/xm-bot4/' 及 XmRouter base='/xm-bot4' 保持一致
            # 若跳转到根 / 会导致 SolidJS Router 匹配不到任何路由，登录后出现空白页
            final_start_url = constants.BOT4_FRONTEND_ENTRY

            # 设置隔离的 WebView2 用户数据目录，避免权限或多实例冲突
            app_data = os.environ.get("APPDATA", os.path.expanduser("~"))
            storage_path = os.path.join(app_data, "xm-bot4", "WebView2")

            # ── WebView2 数据目录损坏自愈 ─────────────────────────────────────────
            # 问题根因：进程意外崩溃后 WebView2 会留下 SingletonLock 等排他锁文件。
            # 下次启动时 WebView2 运行时尝试独占访问该目录，若锁文件是遗留孤儿则卡死
            # 初始化，导致前端页面永远无法渲染，用户看到的就是永久停滞的启动加载界面。
            # 修复策略：发现孤儿锁文件 → 立即清除整个 WebView2 目录 → 让其干净重建。
            try:
                import shutil as _shutil
                _wv2_lock_indicators = [
                    os.path.join(storage_path, "EBWebView", "SingletonLock"),
                    os.path.join(storage_path, "EBWebView", "SingletonSocket"),
                    os.path.join(storage_path, "EBWebView", "SingletonCookie"),
                    os.path.join(storage_path, "SingletonLock"),
                ]
                _wv2_corrupted = False
                for _lock_path in _wv2_lock_indicators:
                    if os.path.exists(_lock_path):
                        try:
                            # 能以读写方式打开 = 孤儿锁文件（没有进程持有），可以清理
                            with open(_lock_path, "r+b"):
                                pass
                            _wv2_corrupted = True
                        except (PermissionError, OSError):
                            # 被真实进程持有，不清理
                            pass
                        break

                if _wv2_corrupted and os.path.exists(storage_path):
                    print("[启动] ⚠️ 检测到 WebView2 数据目录存在孤儿锁文件，正在自动清理以防启动卡死...")
                    try:
                        _shutil.rmtree(storage_path, ignore_errors=True)
                        print("[启动] ✅ WebView2 数据目录已清理，将重新初始化")
                    except Exception as _rm_e:
                        print(f"[启动] ⚠️ 清理 WebView2 目录失败（将继续启动）: {_rm_e}")
            except Exception:
                pass  # 自愈检测失败不影响正常启动流程

            os.makedirs(storage_path, exist_ok=True)
            os.environ["WEBVIEW2_USER_DATA_FOLDER"] = storage_path


            # 创建 pywebview 窗口
            window = webview.create_window(
                'xm-bot4' if not is_dev else 'xm-bot4 [开发模式]',
                html=loading_html,
                width=1200, height=800,
                min_size=(900, 600),
                frameless=True,
                resizable=True,
                background_color='#FFFFFF',
                easy_drag=False,
                js_api=js_api
            )

            # 事件绑定与托盘控制
            js_api.bind_window_events(window)
            ghost_mgr = js_api._ensure_snake_ghost()
            ghost_mgr.set_main_window(window, '', js_api)

            # 启动异步健康探针线程，后端就绪后引导 window 跳转
            probe_thread = threading.Thread(
                target=transition_to_app,
                args=(window, is_dev, desktop_debug, final_start_url),
                daemon=True
            )
            probe_thread.start()

            # 设置托盘图标（延迟到窗口完全显示后初始化，防止与 WebView2 启动期的 COM 初始化产生冲突导致 RPC_E_DISCONNECTED）
            _base_dir = str(BACKEND_ROOT)
            _icon_ico = os.path.join(_base_dir, 'assets', 'logo.ico')
            _icon_png = os.path.join(_base_dir, 'assets', 'logo.png')
            _icon = _icon_ico if os.path.exists(_icon_ico) else _icon_png
            
            # 系统托盘图标优先使用 ICO 格式，以保证 Windows 托盘原生图标的透明通道与多尺寸缩放质量（昨天此格式可正常显示）
            _tray_icon_path = _icon

            def _local_log(msg: str):
                try:
                    import datetime
                    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
                    log_path = os.path.join(appdata, "xm-bot4", "logs", "crash.log")
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"\n{'='*60}\n[{datetime.datetime.now().isoformat()}]\n{msg}\n{'='*60}\n")
                except Exception:
                    pass

            def init_tray_on_shown():
                try:
                    _local_log("[外壳] init_tray_on_shown 触发，开始初始化托盘图标...")
                    js_api.setup_tray(window, product_name='xm-bot4', icon_path=_tray_icon_path)
                except Exception as e:
                    import traceback
                    _local_log(f"[外壳] 延迟设置托盘图标异常:\n{traceback.format_exc()}")
            
            window.events.shown += init_tray_on_shown

            # 🌟 双重防线：部分 Windows 环境下 webview2 窗口 shown 事件可能不会被稳定触发，
            # 引入 3.5 秒延迟的后台备份线程，如托盘仍未初始化则强制唤起，确保 100% 显示。
            def tray_backup_thread():
                time.sleep(3.5)
                if not getattr(js_api, '_tray_ready', False):
                    try:
                        _local_log("[外壳] 检测到 shown 事件未触发或托盘未就绪，通过备份线程强制启动托盘图标...")
                        js_api.setup_tray(window, product_name='xm-bot4', icon_path=_tray_icon_path)
                    except Exception as e:
                        import traceback
                        _local_log(f"[外壳] 备份线程初始化托盘异常:\n{traceback.format_exc()}")

            threading.Thread(target=tray_backup_thread, daemon=True, name="TrayBackupThread").start()

            # 设置 Windows 高清任务栏原生图标
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("xmcore.xmai.bot4.hd")
                from app.bootstrap.webview_patch import setup_native_icon
                setup_native_icon(js_api, _icon_ico)
            except Exception:
                pass

            # ─── 注册 .NET/WinForms 异常捕获，防范 COM/WebView2 崩溃 ───
            from app.bootstrap.clr_patch import setup_clr_exception_hook
            setup_clr_exception_hook()

            # 启动 webview 消息循环
            try:
                from main import _write_crash_log
                _write_crash_log("即将执行 webview.start() 进入 GUI 主循环")
            except Exception:
                pass
            webview.start(gui='edgechromium', debug=desktop_debug)
            # 如果执行到这里，说明 webview 主循环已退出（窗口被关闭）
            
            # ===== WebView2 崩溃检测与故障自愈 =====
            # 判断退出是否由用户主动关闭触发（PywebviewShell.close_app 会设置标志位）
            _user_closed = getattr(js_api, '_user_requested_close', False)
            try:
                from main import _write_crash_log
                if _user_closed:
                    _write_crash_log("webview.start() 已返回：用户主动关闭窗口，程序正常退出")
                else:
                    _write_crash_log(
                        "webview.start() 已返回：非用户主动关闭！"
                        "可能原因：WebView2 渲染进程崩溃 / Edge 内核 OOM / GPU 驱动异常"
                    )
                    # 【自愈机制】自动在本地 config.json 中写入 disable_gpu=true，保证下一次启动时自动绕过 GPU 硬件加速
                    try:
                        import json
                        cfg_data = {}
                        os.makedirs(config_dir, exist_ok=True)
                        if os.path.exists(config_path):
                            try:
                                with open(config_path, "r", encoding="utf-8") as f:
                                    cfg_data = json.load(f)
                            except Exception:
                                pass
                        cfg_data["disable_gpu"] = True
                        with open(config_path, "w", encoding="utf-8") as f:
                            json.dump(cfg_data, f, ensure_ascii=False, indent=2)
                        _write_crash_log("[自愈] 已在 config.json 写入 disable_gpu=true，下次启动将自动禁用 GPU 硬件加速。")
                    except Exception as e_cfg:
                        _write_crash_log(f"[自愈] 写入配置异常: {e_cfg}")

                    # 上报异常退出到 xm-sentinel（含本地日志文件附件）
                    try:
                        from xm_py_server.sentinel import report_crash_with_logs
                        import time as _t
                        report_crash_with_logs(
                            message="WebView2 主循环异常退出（非用户关闭）",
                            stack_trace="webview.start() returned unexpectedly without user close action",
                            context={"stage": "webview_main_loop", "user_closed": False},
                            source="backend",
                            max_lines_per_file=600,
                        )
                        # 给后台上传线程 6 秒完成上传再继续
                        _t.sleep(6)
                    except Exception:
                        pass
                    # 打包环境下弹窗提示用户
                    if getattr(sys, 'frozen', False):
                        try:
                            import ctypes as _ct
                            _ct.windll.user32.MessageBoxW(
                                0,
                                "程序界面意外关闭，可能由系统资源不足或显卡驱动问题导致。\n\n"
                                "请重新启动程序。如频繁出现此问题，请联系技术支持。\n\n"
                                "日志文件：%APPDATA%/xm-bot4/logs/crash.log",
                                "xm-bot4 - 界面异常退出",
                                0x30  # MB_ICONWARNING
                            )
                        except Exception:
                            pass
            except Exception:
                pass
        except ImportError:
            print("[提示] pywebview 未安装，使用纯服务器模式")
            start_server()
        except Exception as e:
            print(f"[致命] 桌面壳初始化异常，回退纯服务器模式: {e}")
            traceback.print_exc()
            # 💡 server_thread 已经在后台异步启动并运行，此处不需要重复调用 start_server()，只需保持主线程阻塞即可！
            try:
                while server_thread.is_alive():
                    server_thread.join(timeout=1.0)
            except KeyboardInterrupt:
                pass
    else:
        # headless 模式，阻塞在主线程以保持服务存活
        try:
            while server_thread.is_alive():
                server_thread.join(timeout=1.0)
        except KeyboardInterrupt:
            pass
