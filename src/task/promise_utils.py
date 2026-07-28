import os
import re
import ssl
import json
import logging
import shutil
import subprocess
import urllib.request

logger = logging.getLogger(__name__)


def capture_web_page(url: str, output_path: str) -> bool:
    """
    利用 Windows 系统中自带的 Edge (Chromium) 浏览器无头命令行实现静默网页截图。
    无需安装第三方 webdriver 或 playwright 依赖，超轻量，极其稳定。
    """
    edge_paths = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    edge_exe = None
    for p in edge_paths:
        if os.path.exists(p):
            edge_exe = p
            break
    if not edge_exe:
        edge_exe = shutil.which("msedge")
    if not edge_exe:
        # 尝试 Chrome 兜底
        chrome_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
        for p in chrome_paths:
            if os.path.exists(p):
                edge_exe = p
                break
    if not edge_exe:
        edge_exe = shutil.which("chrome")
        
    if not edge_exe:
        logger.warning("[WebSnapshot] 未在当前 Windows 系统中找到 Edge 或 Chrome，无法截图。")
        return False
        
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    cmd = [
        edge_exe,
        "--headless",
        "--disable-gpu",
        "--hide-scrollbars",
        f"--screenshot={output_path}",
        "--window-size=1280,900",
        url
    ]
    try:
        proc = subprocess.run(cmd, timeout=15, capture_output=True)
        return proc.returncode == 0 and os.path.exists(output_path)
    except Exception as e:
        logger.error(f"[WebSnapshot] 启动浏览器命令行静默截图异常: {e}")
        return False


def resolve_video_watermark(share_url: str) -> str:
    """
    短视频解析服务：
    若为抖音、快手、小红书的分享口令或短地址，调用免费公共解析网关获取无水印直链；
    若为普通链接，直接作为直链返回。
    """
    url_match = re.search(r'https?://[^\s]+', share_url)
    if not url_match:
        return share_url
    clean_url = url_match.group(0)
    
    # 使用 Peark 公开免签短视频去水印网关
    api_url = f"https://api.peark.cn/api/video/video.php?url={clean_url}"
    try:
        req = urllib.request.Request(
            api_url, 
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=context, timeout=8) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            if res_data.get("code") == 200:
                video_url = res_data.get("video") or res_data.get("url")
                if video_url:
                    return video_url
    except Exception as e:
        logger.warning(f"[WatermarkResolve] 去水印接口调用失败，使用直链兜底: {e}")
        
    return clean_url
