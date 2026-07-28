import httpx
import logging

logger = logging.getLogger(__name__)

async def auto_activate_coze(cookie_str: str) -> dict:
    """模拟访问 Coze 主页以激活每日积分发放
    
    返回:
        dict: {"success": bool, "message": str, "url": str, "status_code": int}
    """
    if not cookie_str:
        return {"success": False, "message": "Cookie 为空，请配置后重试。"}

    # 清理可能存在的首尾空白或引号
    cookie_str = cookie_str.strip().strip('"').strip("'")

    url = "https://www.coze.cn/space"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Cookie": cookie_str,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://www.coze.cn/",
    }

    try:
        # 禁用跟随重定向或启用跟随重定向但检测最终 URL 变化
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            
            final_url = str(resp.url)
            
            # 如果返回 403 或者是特定安全拦截
            if resp.status_code == 403:
                msg = "访问被拒绝 (403)，已触发扣子安全盾人机验证拦截。请点击上方【内嵌浏览器一键登录】重新访问刷新状态。"
                logger.warning(f"[Coze 激活] {msg}")
                return {
                    "success": False,
                    "message": msg,
                    "url": final_url,
                    "status_code": resp.status_code
                }
            
            # 如果未登录，Coze 会重定向到 passport 登录页
            if resp.status_code == 200 and "passport" not in final_url and "login" not in final_url and "challenge" not in final_url:
                logger.info(f"[Coze 激活] 自动访问 Coze Space 成功。最终 URL: {final_url}")
                return {
                    "success": True, 
                    "message": "自动访问 Coze 成功，已触发日活积分发放。",
                    "url": final_url,
                    "status_code": resp.status_code
                }
            else:
                if "passport" in final_url or "login" in final_url:
                    message = "扣子登录态已失效，请点击上方【内嵌浏览器一键登录】重新登录以更新 Cookie。"
                elif "challenge" in final_url or "captcha" in final_url:
                    message = "已触发安全盾人机校验拦截，请点击上方【内嵌浏览器一键登录】重新访问以完成验证。"
                else:
                    message = "Cookie 校验失败或已失效，请通过上方【内嵌浏览器一键登录】刷新 Cookie 状态。"
                
                logger.warning(f"[Coze 激活] 未通过验证 (status_code={resp.status_code})。最终 URL: {final_url}")
                return {
                    "success": False, 
                    "message": message,
                    "url": final_url,
                    "status_code": resp.status_code
                }
    except Exception as e:
        err_msg = f"访问发生异常: {e}"
        logger.error(f"[Coze 激活] {err_msg}")
        return {
            "success": False, 
            "message": f"连接 Coze 失败: {str(e)}",
            "url": url,
            "status_code": 500
        }
