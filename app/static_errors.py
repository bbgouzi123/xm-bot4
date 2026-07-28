"""Static dist mount + SPA 404 + fallback HTML."""
from __future__ import annotations

import os
import sys
import urllib.parse

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.paths import BACKEND_ROOT

_dist_candidates: list[str] = []
_dist_mounted = False

# 从全局环境变量获取微信二维码链接
WECHAT_QR_URL = os.getenv('VITE_XM_WECHAT_QR_URL', 'https://u.wechat.com/MJ_7mGCjdtfQawfHfDOdRX0?s=3')
WECHAT_QR_ENCODED = urllib.parse.quote(WECHAT_QR_URL, safe='')

ERROR_PAGE_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>系统加载提示 - xm-bot4</title>
    <style>
        body { margin: 0; padding: 0; font-family: 'Inter', system-ui, -apple-system, sans-serif; background: #f8fafc; color: #1e293b; display: flex; align-items: center; justify-content: center; height: 100vh; overflow: hidden; -webkit-app-region: drag; user-select: none; }
        /* 背景点缀网格 */
        body::before { content: ""; position: absolute; inset: 0; background-image: linear-gradient(#e2e8f0 1px, transparent 1px), linear-gradient(90deg, #e2e8f0 1px, transparent 1px); background-size: 32px 32px; opacity: 0.5; z-index: 0; }
        
        .container { position: relative; z-index: 10; text-align: center; background: #ffffff; border: 1px solid rgba(0, 0, 0, 0.05); border-radius: 24px; padding: 48px; width: 440px; box-shadow: 0 20px 40px -10px rgba(0, 0, 0, 0.08); -webkit-app-region: no-drag; }
        .icon { width: 64px; height: 64px; margin: 0 auto 24px; border-radius: 20px; background: #eff6ff; display: flex; align-items: center; justify-content: center; color: #3b82f6; animation: float 3s ease-in-out infinite; box-shadow: 0 8px 16px -4px rgba(59, 130, 246, 0.2); }
        h1 { font-size: 24px; margin: 0 0 12px 0; font-weight: 700; color: #0f172a; letter-spacing: -0.5px; }
        p { color: #64748b; font-size: 15px; line-height: 1.6; margin: 0 0 32px 0; }
        
        .actions { display: flex; gap: 12px; justify-content: center; }
        button { background: #f1f5f9; border: 1px solid transparent; color: #475569; padding: 12px 24px; border-radius: 12px; font-size: 14px; font-weight: 600; cursor: pointer; transition: all 0.2s ease; display: inline-flex; align-items: center; gap: 8px; }
        button.primary { background: #3b82f6; color: white; box-shadow: 0 4px 12px rgba(59, 130, 246, 0.25); }
        button:hover { transform: translateY(-1px); }
        button.primary:hover { background: #2563eb; box-shadow: 0 6px 16px rgba(59, 130, 246, 0.35); }
        button:not(.primary):hover { background: #e2e8f0; color: #1e293b; }
        
        @keyframes float { 0% { transform: translateY(0px); } 50% { transform: translateY(-6px); } 100% { transform: translateY(0px); } }
        
        /* 微信客服弹窗样式 */
        .modal-overlay { display: none; position: fixed; inset: 0; z-index: 1000; align-items: center; justify-content: center; }
        .modal-bg { position: absolute; inset: 0; background: rgba(0,0,0,0.4); backdrop-filter: blur(4px); }
        .modal-card { position: relative; z-index: 1010; border-radius: 24px; overflow: hidden; box-shadow: 0 25px 50px -12px rgba(0,0,0,0.25); background: linear-gradient(180deg, #ffffff, #f8fafb); max-width: 360px; width: 90vw; animation: modalIn 0.3s cubic-bezier(0.16, 1, 0.3, 1); }
        @keyframes modalIn { from { opacity: 0; transform: scale(0.95) translateY(10px); } to { opacity: 1; transform: scale(1) translateY(0); } }
    </style>
</head>
<body>
    <div class="container">
        <div class="icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="width: 32px; height: 32px;"><path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
        </div>
        <h1>界面资源未就绪</h1>
        <p>
            未能呈现应用交互界面，请检查前端服务状态。<br><br>
            这通常是因为开发环境服务尚未启动完成，或已上线的客户端丢失了构建打包的静态网页资源。
        </p>
        <div class="actions">
            <button class="primary" onclick="window.location.reload()">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>
                重新载入
            </button>
            <button onclick="showModal()">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M8.691 2.188C3.891 2.188 0 5.476 0 9.534c0 2.22 1.17 4.217 3.002 5.588a.75.75 0 0 1 .306.614l-.04 1.423a.75.75 0 0 0 1.072.686l1.7-.785a.75.75 0 0 1 .504-.044c.71.178 1.454.268 2.147.268.363 0 .72-.023 1.07-.067a5.567 5.567 0 0 1-.204-1.482c0-3.455 3.274-6.255 7.312-6.255.225 0 .447.012.667.034C16.854 5.39 13.17 2.188 8.691 2.188zm-2.75 4.138a1.125 1.125 0 1 1 0 2.25 1.125 1.125 0 0 1 0-2.25zm5.5 0a1.125 1.125 0 1 1 0 2.25 1.125 1.125 0 0 1 0-2.25z"/><path d="M23.598 14.779c0-3.18-3.104-5.76-6.931-5.76-3.828 0-6.932 2.58-6.932 5.76 0 3.18 3.104 5.76 6.932 5.76.67 0 1.316-.082 1.92-.234a.75.75 0 0 1 .488.03l1.347.622a.563.563 0 0 0 .804-.515l-.03-1.12a.563.563 0 0 1 .23-.46c1.37-1.07 2.172-2.64 2.172-4.083zm-9.566-.937a.938.938 0 1 1 0-1.875.938.938 0 0 1 0 1.875zm5.269 0a.938.938 0 1 1 0-1.875.938.938 0 0 1 0 1.875z"/></svg>
                技术支持
            </button>
            <button onclick="closeApp()">退出重连</button>
        </div>
    </div>

    <!-- 微信客服弹窗 -->
    <div id="contactModal" class="modal-overlay">
        <div class="modal-bg" onclick="closeModal()"></div>
        <div class="modal-card">
            <!-- 关闭按钮 -->
            <button onclick="closeModal()" style="position: absolute; top: 16px; right: 16px; width: 32px; height: 32px; border-radius: 16px; background: #f3f4f6; color: #9ca3af; border: none; font-size: 14px; font-weight: bold; cursor: pointer; padding: 0; display: flex; align-items: center; justify-content: center; box-shadow: none;">✕</button>
            <div style="background: linear-gradient(135deg, #07C160, #06AD56); height: 6px;"></div>
            <div style="display: flex; flex-direction: column; align-items: center; padding: 32px;">
                <div style="width: 56px; height: 56px; border-radius: 16px; display: flex; align-items: center; justify-content: center; margin-bottom: 16px; background: linear-gradient(135deg, #07C160, #06AD56);">
                    <svg viewBox="0 0 24 24" width="28" height="28" fill="white">
                        <path d="M8.691 2.188C3.891 2.188 0 5.476 0 9.534c0 2.22 1.17 4.217 3.002 5.588a.75.75 0 0 1 .306.614l-.04 1.423a.75.75 0 0 0 1.072.686l1.7-.785a.75.75 0 0 1 .504-.044c.71.178 1.454.268 2.147.268.363 0 .72-.023 1.07-.067a5.567 5.567 0 0 1-.204-1.482c0-3.455 3.274-6.255 7.312-6.255.225 0 .447.012.667.034C16.854 5.39 13.17 2.188 8.691 2.188zm-2.75 4.138a1.125 1.125 0 1 1 0 2.25 1.125 1.125 0 0 1 0-2.25zm5.5 0a1.125 1.125 0 1 1 0 2.25 1.125 1.125 0 0 1 0-2.25z"/>
                        <path d="M23.598 14.779c0-3.18-3.104-5.76-6.931-5.76-3.828 0-6.932 2.58-6.932 5.76 0 3.18 3.104 5.76 6.932 5.76.67 0 1.316-.082 1.92-.234a.75.75 0 0 1 .488.03l1.347.622a.563.563 0 0 0 .804-.515l-.03-1.12a.563.563 0 0 1 .23-.46c1.37-1.07 2.172-2.64 2.172-4.083zm-9.566-.937a.938.938 0 1 1 0-1.875.938.938 0 0 1 0 1.875zm5.269 0a.938.938 0 1 1 0-1.875.938.938 0 0 1 0 1.875z"/>
                    </svg>
                </div>
                <h3 style="font-size: 20px; font-weight: bold; color: #1f2937; margin: 0 0 4px 0;">添加微信</h3>
                <p style="color: #9ca3af; font-size: 14px; margin: 0 0 24px 0;">扫码添加 · 获取产品详情与技术支持</p>
                
                <div style="border-radius: 16px; padding: 12px; margin-bottom: 20px; background: #ffffff; box-shadow: 0 2px 16px rgba(0,0,0,0.06); border: 1px solid #f0f0f0;">
                    <img src="https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={WECHAT_QR_ENCODED}" width="180" height="180" style="display: block; border-radius: 8px;" alt="微信二维码" />
                </div>
                
                <p style="font-size: 12px; color: #d1d5db; margin: 0;">工作时间 9:00-18:00 · 通常1小时内回复</p>
            </div>
        </div>
    </div>

    <script>
        function showModal() { document.getElementById('contactModal').style.display = 'flex'; }
        function closeModal() { document.getElementById('contactModal').style.display = 'none'; }
        function closeApp() { if (window.pywebview && window.pywebview.api) { window.pywebview.api.close_app(); } else { window.close(); } }
        
        // 绑定键盘 Esc 关闭弹窗
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') closeModal();
        });
    </script>
</body>
</html>
"""



class SafeStaticFiles(StaticFiles):
    async def check_config(self) -> None:
        """
        Bypass Starlette's strict directory checks to prevent RuntimeError crashes 
        if the static assets directory is dynamically removed/cleaned during runtime.
        """
        if self.directory is None:
            return
        if not os.path.isdir(self.directory):
            import logging
            logging.getLogger("uvicorn.error").warning(
                f"[SafeStaticFiles] Directory '{self.directory}' does not exist or was deleted. "
                "Requests to static files will fallback to 404."
            )
            return
        await super().check_config()


def mount_frontend_and_error_handlers(app: FastAPI) -> None:
    """在路由注册之后调用：挂载 / 静态资源并注册 404 处理。"""
    global _dist_candidates, _dist_mounted
    # 前端静态文件寻址逻辑
    if getattr(sys, 'frozen', False):
        # PyInstaller 解包/打包后的内置资源路径 (SYS._MEIPASS 等同于内部运行目录, 或 exe 的同级内部目录)
        _base = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    else:
        # 源代码开发环境路径
        _base = str(BACKEND_ROOT)
    
    _project_root = os.path.dirname(_base) # xm-bot4 或上级
    _dist_candidates = [
        # 打包后寻找 resources 内置的前端
        os.path.join(_base, "frontend", "dist"), 
        # 开发环境下的同级 frontend
        os.path.join(_project_root, "frontend", "dist"),
    ]
    _dist_mounted = False
    for web_dist in _dist_candidates:
        if os.path.exists(web_dist):
            # 同时挂载 /xm-bot4 和 / 根路径，确保能够正确解析以 /xm-bot4/ 开头的前端打包静态资源
            app.mount("/xm-bot4", SafeStaticFiles(directory=web_dist, html=True, check_dir=False), name="frontend_subpath")
            app.mount("/", SafeStaticFiles(directory=web_dist, html=True, check_dir=False), name="frontend")
            print(f"[前端] 加载: {web_dist}")
            _dist_mounted = True
            break

    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc: StarletteHTTPException):
        # 如果是后端 API 接口确实没找到，则正常返回 API 的 404
        if request.url.path.startswith("/api/"):
            return Response(content='{"error": "Not Found"}', status_code=404, media_type="application/json")
    
        # 如果是请求的静态资源文件（带常见静态文件后缀，如 .js, .css 等）丢失了，我们应该返回 404，而不是 fallback 到 index.html
        # 这能避免浏览器报 "Expected a JavaScript-or-Wasm module script..." 的混淆 MIME 错误，
        # 并允许前端感知到 chunk 加载失败从而触发重刷。
        path_lower = request.url.path.lower()
        static_exts = [".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".woff", ".woff2", ".ttf", ".eot", ".map"]
        if any(path_lower.endswith(ext) for ext in static_exts):
            return Response(content="File Not Found", status_code=404, media_type="text/plain")

        # === 关键优化：SPA 路由降级 ===
        # 如果系统其实已经挂载了前端包文件，那 404 就意味着用户在刷新前端的子路由（比如 /instances）
        # 在前端单页应用(SPA)机制中，我们需要将不存在请求兜底全重定向给 index.html
        if _dist_mounted:
            for web_dist in _dist_candidates:
                index_path = os.path.join(web_dist, "index.html")
                if os.path.exists(index_path):
                    return FileResponse(index_path)
                
        # === 极端状况容灾降级 ===
        # 真的是连前端静态文件包都没有(例如用户没起vite又没打包项目)，那就返回全屏错误兜底
        return HTMLResponse(content=ERROR_PAGE_HTML.replace("{WECHAT_QR_ENCODED}", WECHAT_QR_ENCODED), status_code=200)

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        """全局兜底异常处理器，防止 UIAInterruptError 或其他未捕获异常导致 ASGI 崩溃。"""
        # 安全导入 UIAInterruptError（Cython 编译后可能加载失败，不能让处理器自身崩溃）
        try:
            from src.uia.input_guard import UIAInterruptError
            is_uia_interrupt = isinstance(exc, UIAInterruptError)
        except Exception:
            is_uia_interrupt = False
        
        # 处理特定的 UIA 中断异常（用户按 ESC 主动中断，不算崩溃）
        if is_uia_interrupt:
            return JSONResponse(
                status_code=200,
                content={
                    "success": False, 
                    "reason": str(exc), 
                    "interrupted": True
                }
            )

        # 客户端主动断开连接（前端刷新、网络中断等），不算服务器错误，静默处理
        # 不写 crash.log，不触发 sentinel，避免误报
        try:
            from starlette.requests import ClientDisconnect
            if isinstance(exc, ClientDisconnect):
                import logging
                logging.getLogger("uvicorn.access").debug(
                    f"[API] ClientDisconnect on {request.method} {request.url.path} (client closed request, ignored)"
                )
                return Response(status_code=499)  # 499 = Client Closed Request（非标准但业界惯例）
        except Exception:
            pass

        # 其他异常：记录日志 + 上报 sentinel + 写入崩溃日志文件
        import logging
        import traceback
        tb_str = traceback.format_exc()
        error_detail = f"Global Exception on {request.method} {request.url.path}: {exc}\n{tb_str}"
        logging.getLogger("uvicorn.error").error(error_detail)
        
        # 写入崩溃日志文件（console=False 时至关重要）
        try:
            from main import _write_crash_log
            _write_crash_log(f"[API] {error_detail}")
        except Exception:
            pass
        
        # 上报到 xm-sentinel（fire-and-forget，不阻塞响应）
        try:
            from xm_py_server.sentinel import report_crash
            report_crash(
                message=f"{type(exc).__name__}: {exc}",
                stack_trace=tb_str,
                context={
                    "path": str(request.url.path),
                    "method": request.method,
                },
            )
        except Exception:
            pass  # sentinel 不可用时静默
        
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "reason": f"Internal Server Error: {str(exc)}"
            }
        )

