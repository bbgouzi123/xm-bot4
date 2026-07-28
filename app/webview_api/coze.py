from __future__ import annotations

class CozeMixin:
    def open_coze_login_window(self) -> bool:
        """打开内嵌网页登录 Coze 窗口并自动截获 Cookie"""
        import webview
        import threading
        import time
        import logging

        logger = logging.getLogger(__name__)

        def monitor_coze_login():
            try:
                # 登录跳转火山引擎登录页：由于 coze.cn 支持通过 SSO 登录，我们直接打开 coze.cn 的登录页
                login_url = "https://www.coze.cn/?login_from=space_landing"
                
                login_window = webview.create_window(
                    '扣子 (Coze) 登录态获取',
                    url=login_url,
                    width=600,
                    height=700,
                    resizable=True,
                    on_top=True
                )
                
                # 等待 5 分钟或者窗口关闭
                cookie_detected = False
                for _ in range(600):  # 600 * 0.5s = 300s (5分钟)
                    time.sleep(0.5)
                    try:
                        # 检查窗口是否已关闭
                        if login_window not in webview.windows:
                            break
                            
                        # 获取 cookies
                        cookies = login_window.get_cookies()
                        if not cookies:
                            continue

                        # 提取 Cookie
                        cookie_dict = {}
                        for c_obj in cookies:
                            for name, morsel in c_obj.items():
                                cookie_dict[name] = morsel.value

                        # 检查是否有登录成功的关键 Cookie
                        if "sessionid" in cookie_dict or "sessionid_ss" in cookie_dict:
                            # 构造完整的 Cookie 字符串
                            cookie_items = [f"{k}={v}" for k, v in cookie_dict.items()]
                            full_cookie_str = "; ".join(cookie_items)

                            # 保存到配置文件中
                            from src.api.config_api import _load_configs, _save_configs
                            configs = _load_configs()
                            configs["coze_cookie"] = full_cookie_str
                            configs["coze_auto_login"] = True
                            _save_configs(configs)

                            logger.info("[Coze 登录助手] 自动检测并截获登录 Cookie 成功！")
                            
                            # 向前端广播通知
                            if self._window:
                                self._window.evaluate_js(
                                    "if(window.__CozeLoginCallback) window.__CozeLoginCallback.onSuccess();"
                                )
                            
                            # 销毁登录窗口
                            login_window.destroy()
                            cookie_detected = True
                            break
                    except Exception as e:
                        logger.warning(f"[Coze 登录助手] 轮询异常: {e}")
                        break
                        
                if not cookie_detected:
                    try:
                        login_window.destroy()
                    except Exception:
                        pass
            except Exception as e:
                logger.error(f"[Coze 登录助手] 运行错误: {e}")

        threading.Thread(target=monitor_coze_login, daemon=True).start()
        return True
