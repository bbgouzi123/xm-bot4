"""运行环境统一安装器图形界面组件。"""
from __future__ import annotations

import os
import sys
import time
import ctypes
import webbrowser
import tempfile
import threading
import subprocess

from app.bootstrap.env_installer import download_with_progress, run_installer, _cleanup


def update_ui_safe(widget, **kwargs):
    try:
        widget.after(0, lambda: widget.config(**kwargs))
    except Exception:
        pass


def update_progress_safe(lbl, pb, text, color, value):
    try:
        lbl.after(0, lambda: [
            lbl.config(text=text, fg=color),
            pb.config(value=value)
        ])
    except Exception:
        pass


def run_unified_installer(components: list[dict], missing: list[dict]) -> bool:
    """运行统一的依赖下载安装图形界面。"""
    import tkinter as tk
    from tkinter import ttk

    # 1. 询问用户是否同意自动安装
    msg = (
        "检测到本机缺少运行程序必需的底层依赖组件：\n"
        + "\n".join(f"  • {c['name']} (大小: {c['size_hint']})" for c in missing)
        + "\n\n程序将自动下载并静默配置这些环境，完成后会自动启动。\n\n是否立即开始？"
    )
    res = ctypes.windll.user32.MessageBoxW(0, msg, "xm-bot4 - 环境依赖配置", 4 | 32 | 8192)
    if res != 6:
        for c in missing:
            if c["fallback_url"]:
                webbrowser.open(c["fallback_url"])
        return False

    success = [False]
    reboot_required = [False]
    is_cancelled = [False]

    def background_work(root, ui_rows, lbl_info, btn_action):
        tmp_dir = tempfile.mkdtemp(prefix="xm_bot4_env_")
        try:
            for comp in missing:
                if is_cancelled[0]:
                    return
                
                name, filename, download_url, fallback_url, silent_args, recheck_fn = (
                    comp["name"], comp["filename"], comp["download_url"],
                    comp["fallback_url"], comp["silent_args"], comp["recheck_fn"]
                )
                row_ui = ui_rows[comp["key"]]
                
                update_progress_safe(row_ui["lbl_status"], row_ui["pb"], "正在准备...", "#3B82F6", 0)
                update_ui_safe(row_ui["pb"], style="blue.Horizontal.TProgressbar")
                update_ui_safe(lbl_info, text=f"正在下载 {name}...", fg="#475569")
                
                installer_path = os.path.join(tmp_dir, filename)
                
                def _progress_cb(pct: int, speed: str):
                    if is_cancelled[0]:
                        raise RuntimeError("Cancelled")
                    update_progress_safe(row_ui["lbl_status"], row_ui["pb"], f"下载中 ({pct}%) {speed}", "#3B82F6", pct)
                
                ok = download_with_progress(download_url, installer_path, _progress_cb, fallback_url)
                if is_cancelled[0]:
                    return
                if not ok or not os.path.exists(installer_path):
                    update_progress_safe(row_ui["lbl_status"], row_ui["pb"], "❌ 下载失败", "#EF4444", 0)
                    update_ui_safe(lbl_info, text=f"{name} 下载失败！", fg="#EF4444")
                    _handle_failure(root, comp, btn_action)
                    return
                
                update_progress_safe(row_ui["lbl_status"], row_ui["pb"], "正在安装...", "#3B82F6", 100)
                update_ui_safe(row_ui["pb"], style="blue.Horizontal.TProgressbar")
                update_ui_safe(lbl_info, text=f"正在配置 {name}...", fg="#475569")
                
                exit_code = run_installer(installer_path, silent_args)
                if is_cancelled[0]:
                    return
                
                if exit_code in (0, 3010, 1641):
                    if exit_code in (3010, 1641):
                        reboot_required[0] = True
                    is_ok = False
                    for _ in range(5):
                        if is_cancelled[0]:
                            return
                        if recheck_fn():
                            is_ok = True
                            break
                        time.sleep(1)
                    
                    if is_ok:
                        update_progress_safe(row_ui["lbl_status"], row_ui["pb"], "✅ 安装成功", "#10B981", 100)
                        update_ui_safe(row_ui["pb"], style="green.Horizontal.TProgressbar")
                    else:
                        if reboot_required[0]:
                            update_progress_safe(row_ui["lbl_status"], row_ui["pb"], "⚠️ 需重启电脑", "#F59E0B", 100)
                            update_ui_safe(row_ui["pb"], style="green.Horizontal.TProgressbar")
                        else:
                            update_progress_safe(row_ui["lbl_status"], row_ui["pb"], "❌ 校验失败", "#EF4444", 100)
                            update_ui_safe(lbl_info, text=f"{name} 校验失败！", fg="#EF4444")
                            _handle_failure(root, comp, btn_action)
                            return
                else:
                    update_progress_safe(row_ui["lbl_status"], row_ui["pb"], "❌ 安装失败", "#EF4444", 100)
                    update_ui_safe(lbl_info, text=f"{name} 安装失败！", fg="#EF4444")
                    _handle_failure(root, comp, btn_action)
                    return
            
            success[0] = True
            if reboot_required[0]:
                update_ui_safe(lbl_info, text="安装完成！部分组件需要重启电脑。", fg="#F59E0B")
                msg = (
                    "系统依赖环境已成功安装！\n\n"
                    "⚠️ 部分组件（如 .NET Framework）需要重启计算机才能完成配置。\n"
                    "是否立即重启计算机？（点击「否」程序将退出）"
                )
                res = ctypes.windll.user32.MessageBoxW(0, msg, "xm-bot4 - 安装完成", 4 | 48 | 8192)
                if res == 6:
                    subprocess.run(["shutdown", "/r", "/t", "5", "/c", "xm-bot4 环境安装完成，即将重启"], check=False)
                update_ui_safe(root, command=root.destroy)
            else:
                update_ui_safe(lbl_info, text="配置成功！程序将自动重新启动...", fg="#10B981")
                time.sleep(2.5)
                try:
                    root.after(0, root.destroy)
                except Exception:
                    pass
        finally:
            _cleanup(tmp_dir)

    def _handle_failure(root, comp, btn_action):
        try:
            root.after(0, lambda: [
                btn_action.config(
                    text="手动下载", bg="#EF4444", fg="#FFFFFF",
                    activebackground="#DC2626", activeforeground="#FFFFFF",
                    command=lambda: [webbrowser.open(comp["fallback_url"]), root.destroy()]
                ),
                btn_action.bind("<Enter>", lambda e: btn_action.config(bg="#DC2626")),
                btn_action.bind("<Leave>", lambda e: btn_action.config(bg="#EF4444")),
                btn_action.pack(side=tk.RIGHT)
            ])
        except Exception:
            pass

    # 计算动态高度：基础 175px，每个缺失项增加 55px
    win_h = 175 + len(missing) * 55

    root = tk.Tk()
    root.title("xm-bot4 - 系统依赖配置")
    root.geometry(f"520x{win_h}")
    root.configure(bg="#FFFFFF")
    root.attributes("-topmost", True)
    root.resizable(False, False)

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    style = ttk.Style()
    style.theme_use('clam')
    style.configure("blue.Horizontal.TProgressbar", thickness=8, troughcolor="#F1F5F9", background="#3B82F6", borderwidth=0)
    style.configure("green.Horizontal.TProgressbar", thickness=8, troughcolor="#F1F5F9", background="#10B981", borderwidth=0)
    style.configure("gray.Horizontal.TProgressbar", thickness=8, troughcolor="#F1F5F9", background="#E2E8F0", borderwidth=0)

    main_frame = tk.Frame(root, bg="#FFFFFF", padx=25, pady=20)
    main_frame.pack(fill=tk.BOTH, expand=True)

    tk.Label(main_frame, text="正在配置系统运行环境", font=("Microsoft YaHei", 14, "bold"), fg="#0F172A", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 2))
    tk.Label(main_frame, text="首次启动需确保以下底层依赖已就绪，完成后程序将自动启动。", font=("Microsoft YaHei", 9), fg="#64748B", bg="#FFFFFF").pack(anchor=tk.W, pady=(0, 20))

    ui_rows = {}
    for comp in missing:
        row_frame = tk.Frame(main_frame, bg="#FFFFFF", pady=5)
        row_frame.pack(fill=tk.X)
        tk.Label(row_frame, text=comp["name"], font=("Microsoft YaHei", 9, "bold"), fg="#334155", bg="#FFFFFF").pack(side=tk.LEFT)
        
        lbl_status = tk.Label(row_frame, text="等待配置...", font=("Microsoft YaHei", 9), fg="#64748B", bg="#FFFFFF")
        lbl_status.pack(side=tk.RIGHT)

        pb = ttk.Progressbar(main_frame, orient="horizontal", length=470, mode="determinate", style="gray.Horizontal.TProgressbar")
        pb["value"] = 0
        pb.pack(fill=tk.X, pady=(0, 8))
        
        ui_rows[comp["key"]] = {"lbl_status": lbl_status, "pb": pb}

    tk.Frame(main_frame, height=1, bg="#E2E8F0").pack(fill=tk.X, pady=(12, 12))
    bottom_frame = tk.Frame(main_frame, bg="#FFFFFF")
    bottom_frame.pack(fill=tk.X)

    lbl_info = tk.Label(bottom_frame, text="正在初始化...", font=("Microsoft YaHei", 9), fg="#475569", bg="#FFFFFF")
    lbl_info.pack(side=tk.LEFT)

    btn_action = tk.Button(bottom_frame, text="退出程序", font=("Microsoft YaHei", 9), bg="#F1F5F9", fg="#475569",
                           activebackground="#E2E8F0", activeforeground="#1E293B", relief=tk.FLAT, bd=0, padx=15, pady=5, command=root.destroy)
    btn_action.bind("<Enter>", lambda e: btn_action.config(bg="#E2E8F0", fg="#1E293B") if btn_action["bg"] == "#F1F5F9" else None)
    btn_action.bind("<Leave>", lambda e: btn_action.config(bg="#F1F5F9", fg="#475569") if btn_action["bg"] == "#E2E8F0" else None)
    btn_action.pack_forget()

    def on_close():
        is_cancelled[0] = True
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", on_close)

    threading.Thread(target=background_work, args=(root, ui_rows, lbl_info, btn_action), daemon=True).start()

    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_width()) // 2
    y = (root.winfo_screenheight() - root.winfo_height()) // 2
    root.geometry(f"+{x}+{y}")
    root.mainloop()

    if success[0] and not reboot_required[0]:
        try:
            if getattr(sys, "frozen", False):
                subprocess.Popen([sys.executable] + sys.argv[1:])
            else:
                subprocess.Popen([sys.executable] + sys.argv)
        except Exception as e:
            print(f"[启动] 自动重启失败: {e}")
            return True
        sys.exit(0)

    return success[0]
