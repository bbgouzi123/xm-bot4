# -*- coding: utf-8 -*-
import os
import sys

def setup_tray_impl(shell, window, product_name: str = "星码行空", icon_path: str = ""):
    """重写基类托盘方法：劫持『彻底退出』菜单项的回调，注入清理逻辑"""
    def _local_log(msg: str):
        try:
            import datetime
            appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
            log_path = os.path.join(appdata, "xm-bot4", "logs", "crash.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*60}\n[{datetime.datetime.now().isoformat()}]\n{msg}\n{'='*60}\n")
        except Exception:
            pass

    if getattr(shell, '_tray_icon', None) is not None:
        _local_log("[外壳] setup_tray: 托盘已存在，跳过重复初始化")
        return

    _local_log(f"[外壳] setup_tray 开始运行: product_name={product_name}, icon_path={icon_path}")
    try:
        import pystray
        from PIL import Image
    except ImportError as e:
        _local_log(f"[外壳] setup_tray 导入 pystray/PIL 失败: {e}，回退基类方法")
        super(type(shell), shell).setup_tray(window, product_name, icon_path)
        return

    try:
        # 准备图标
        if icon_path and os.path.exists(icon_path):
            img = Image.open(icon_path)
            # 🌟 特别保护：为了防止破坏 Windows 原生 ICO 文件的透明度通道与多帧尺寸结构，
            # 仅对非 ICO 格式的图像进行 RGBA 转换与缩放防备。
            if not icon_path.lower().endswith('.ico'):
                img = img.convert('RGBA')
                if hasattr(Image, 'Resampling'):
                    img = img.resize((64, 64), Image.Resampling.LANCZOS)
                else:
                    img = img.resize((64, 64), Image.LANCZOS)
        else:
            img = Image.new('RGB', (64, 64), color='white')
    except Exception as e:
        import traceback
        _local_log(f"[外壳] setup_tray 加载/转换图标图像失败:\n{traceback.format_exc()}")
        img = Image.new('RGB', (64, 64), color='white')

    try:
        shell.set_window(window)

        def on_show(icon, item):
            shell.raise_main_window()

        def on_exit_graceful(icon, item):
            """优雅退出回调"""
            shell._user_requested_close = True  # WebView2 崩溃检测标志
            try:
                icon.stop()
            except Exception:
                pass
            try:
                from src.utils.cleanup import graceful_cleanup
                graceful_cleanup()
            except Exception:
                pass
            # 托盘线程无法直接终止主进程，仍需 os._exit
            os._exit(0)

        menu = pystray.Menu(
            pystray.MenuItem(f'打开 {product_name}', on_show, default=True),
            pystray.MenuItem('彻底退出', on_exit_graceful),
        )

        shell._tray_ready = False
        icon = pystray.Icon("xm-app", img, product_name, menu)
        shell.set_tray(icon)
        shell._original_icon_path = icon_path

        import threading
        threading.Thread(target=icon.run, daemon=True).start()
        _local_log("[外壳] setup_tray: pystray 托盘后台线程已启动")

        def mark_ready():
            import time
            time.sleep(1.0)
            shell._tray_ready = True
            _local_log("[外壳] setup_tray: pystray 托盘启动完成 (_tray_ready=True)")
        threading.Thread(target=mark_ready, daemon=True).start()
    except Exception as e:
        import traceback
        _local_log(f"[外壳] setup_tray 初始化/启动托盘进程失败:\n{traceback.format_exc()}")
