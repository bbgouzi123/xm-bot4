import os
import sys
import time
import json
import urllib.request
import urllib.parse
from app import constants
from app.bootstrap.server import _server_started, _server_error


def _get_loading_template() -> str:
    """获取与当前脚本同目录的 loading.html 模板内容"""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    template_path = os.path.join(current_dir, "loading.html")
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        print(f"[外壳] 无法读取 loading.html 模板: {e}")
        # 如果读取失败，返回一个最基本的 HTML 文本进行白屏兜底，防止崩溃
        return "<html><body><p>System Loading...</p></body></html>"


def get_loading_html(logo_b64: str, splash_ver_html: str) -> str:
    """由模版动态合成秒开预加载页 HTML 字符串"""
    template = _get_loading_template()
    return template.replace("{logo_b64}", logo_b64).replace("{_splash_ver_html}", splash_ver_html)



def transition_to_app(window, is_dev: bool, desktop_debug: bool, final_start_url: str):
    """在后台线程中异步探测 Vite vs 生产环境就绪，并引导跳转"""
    # 建立无代理的 urllib opener 防止本地环回接口请求被系统代理劫持
    try:
        proxy_handler = urllib.request.ProxyHandler({})
        opener = urllib.request.build_opener(proxy_handler)
    except Exception as e:
        print(f"[外壳] 创建无代理 opener 异常，将回退使用默认 urlopen: {e}")
        opener = urllib.request

    # A. 探测 Vite (仅开发环境)
    should_probe_vite = not getattr(sys, 'frozen', False) and is_dev
    if should_probe_vite:
        # Vite 开启了 base: '/xm-bot4/' 路由前缀，如果直接探测根路径 '/' 会触发 404
        # 从而导致开发阶段每次都卡死探针 15 秒。这里改为精确探测带前缀的真实前端入口。
        _probe_url = f'{constants.VITE_DEV_ORIGIN}/xm-bot4/'
        for _ in range(150):
            try:
                resp = opener.open(_probe_url, timeout=0.2)
                if resp.getcode() == 200:
                    final_start_url = constants.VITE_DEV_ORIGIN
                    print(f"[外壳] ✅ 发现 Vite 开发环境，最终将跳转至: {final_start_url}")
                    break
            except Exception:
                pass
            time.sleep(0.1)
    
    if final_start_url != constants.VITE_DEV_ORIGIN:
        if not should_probe_vite:
            print(f"[外壳] 📦 生产环境模式，将使用内置打包界面: {final_start_url}")
        else:
            print(f"[外壳] 📦 未发现开发环境，最终将使用内置打包界面: {final_start_url}")

    # B. 等待 FastAPI 后端完全就绪
    print("[外壳] ⏳ 等待 FastAPI 后端完全就绪...")
    if not _server_started.wait(timeout=15):
        if _server_error:
            print(f"[外壳] ❌ HTTP 服务器启动失败: {_server_error}")
        else:
            print("[外壳] ❌ HTTP 服务器线程未能在 15 秒内启动，可能在端口清理或模块导入中卡死")
    
    health_ok = False
    for i in range(450):  # 450 * 0.2s = 90s
        if _server_error:
            print(f"[外壳] ❌ HTTP 服务器已崩溃（{_server_error}），停止等待")
            break
        try:
            resp = opener.open(f'{constants.BOT4_LOCAL_ORIGIN}/api/health', timeout=1)
            if resp.getcode() == 200:
                data = json.loads(resp.read().decode('utf-8'))
                if data.get('ready'):
                    print("[外壳] 🎉 所有环境已就绪！")
                    
                    # 成功接管后，复位清零启动引导日志，防止文件无限膨胀
                    try:
                        import os
                        _appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
                        _log_dir = os.path.join(_appdata, "xm-bot4", "logs")
                        for _log_name in ["crash.log", "early_boot.log"]:
                            _fpath = os.path.join(_log_dir, _log_name)
                            if os.path.exists(_fpath):
                                with open(_fpath, "w", encoding="utf-8") as _f_clear:
                                    pass
                    except Exception:
                        pass

                    load_url = final_start_url
                    if desktop_debug and load_url.startswith(constants.BOT4_LOCAL_ORIGIN):
                        sep = '&' if ('?' in load_url) else '?'
                        tkq = urllib.parse.quote(constants.ANTI_DEBUG_BYPASS_TK, safe='')
                        load_url = f"{load_url}{sep}tk={tkq}&xm_desktop_dbg=1"
                    time.sleep(0.1)  # 100ms 缓冲，防止 JS Bridge 未挂载
                    jump_ok = False
                    for attempt in range(3):
                        try:
                            window.evaluate_js(f"window.location.replace('{load_url}');")
                            jump_ok = True
                            break
                        except Exception as e:
                            print(f"[外壳] evaluate_js 跳转失败 (尝试 {attempt+1}/3): {e}")
                            time.sleep(0.1)
                    # 🌟 [Win10兜底] 等待800ms验证URL是否真的跳转（部分Win10 WebView2会静默丢弃evaluate_js）
                    time.sleep(0.8)
                    try:
                        cur = window.evaluate_js("window.location.href")
                        if cur and "xm-bot4" not in str(cur):
                            print(f"[外壳] ⚠️ evaluate_js跳转被静默丢弃（当前:{cur}），降级load_url")
                            jump_ok = False
                    except Exception:
                        pass

                    if not jump_ok:
                        print("[外壳] evaluate_js 最终跳转失败，回退使用 load_url")
                        try:
                            window.load_url(load_url)
                        except Exception as e:
                            print(f"[外壳] load_url 致命错误: {e}")
                    health_ok = True
                    break
        except Exception:
            pass
        if i > 0 and i % 150 == 0:
            print(f"[外壳] ⏳ 仍在等待后端就绪... (已等 {i * 0.2:.0f}s)")
        time.sleep(0.2)
    
    if not health_ok:
        import os
        err_detail = _server_error if _server_error else "等待后端就绪超时 (90秒)"
        print(f"[外壳] ❌ 启动失败: {err_detail}")
        if getattr(sys, 'frozen', False):
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0, 
                    f"程序启动失败！\n\n原因: {err_detail}\n\n建议步骤:\n1. 检查端口 {constants.BOT4_PORT} 是否被其他代理或安全软件禁用\n2. 尝试关闭杀毒软件后右键【以管理员身份运行】\n3. 查看详细日志：%APPDATA%/xm-bot4/logs/crash.log",
                    "xm-bot4 - 启动失败",
                    0x10  # MB_ICONERROR
                )
            except Exception:
                pass
        # 强制关闭主窗口并退出进程，避免白屏卡死
        try:
            window.destroy()
        except Exception:
            pass
        os._exit(1)
