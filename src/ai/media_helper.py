import os
import httpx
import logging
import base64
from typing import Optional

logger = logging.getLogger(__name__)

async def call_generate_image(api_key: str, base_url: str, prompt: str) -> Optional[str]:
    """根据提示词调用图像生成 API，返回生成的图片 URL"""
    if not api_key:
        return None

    url = f"{base_url}/v1/images/generations"
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "prompt": prompt,
                    "n": 1,
                    "size": "1024x1024",
                    "response_format": "url",
                }
            )
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    if 'data' in data and len(data['data']) > 0:
                        return data['data'][0].get('url')
                except Exception as json_err:
                    print(f"[media_helper] 解析图像生成 JSON 失败: {json_err}")
            else:
                print(f"[media_helper] 图像生成接口返回错误: {resp.status_code} - {resp.text}")
    except Exception as e:
        print(f"[media_helper] 图像生成异常: {e}")
    return None

async def call_generate_video(api_key: str, base_url: str, prompt: str) -> Optional[str]:
    """根据提示词调用视频生成 API，返回生成的视频 MP4 URL"""
    if not api_key:
        return None

    url = f"{base_url}/v1/videos/generations"
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "prompt": prompt,
                    "n": 1,
                    "size": "1024x576",
                    "response_format": "url",
                }
            )
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    if 'data' in data and len(data['data']) > 0:
                        return data['data'][0].get('url')
                except Exception as json_err:
                    print(f"[media_helper] 解析视频生成 JSON 失败: {json_err}")
            else:
                print(f"[media_helper] 视频生成接口返回错误: {resp.status_code} - {resp.text}")
    except Exception as e:
        print(f"[media_helper] 视频生成异常: {e}")
    return None

async def call_describe_image(api_key: str, base_url: str, model: str, file_path: str) -> Optional[str]:
    """使用具备 Vision 多模态能力的大模型对图片进行内容文字描述和提取"""
    if not file_path or not os.path.exists(file_path):
        return None

    # 1. 读取图片并转换为 base64 编码
    try:
        with open(file_path, "rb") as image_file:
            base64_str = base64.b64encode(image_file.read()).decode('utf-8')
    except Exception as read_ex:
        logger.error(f"[media_helper] 读取图片失败: {read_ex}")
        return None

    # 2. 识别图片类型并获取对应的 MIME 头（默认 jpeg）
    mime_type = "image/jpeg"
    if file_path.lower().endswith(".png"):
        mime_type = "image/png"
    elif file_path.lower().endswith(".gif"):
        mime_type = "image/gif"
    elif file_path.lower().endswith(".webp"):
        mime_type = "image/webp"

    # 3. 构造请求 Payload
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "请帮我精确识别并提取该图片/表情包里的内容。\n"
                        "1. 如果图片或表情包里有文字，必须完整提取出所有文字内容。\n"
                        "2. 简要说明图片里画了什么、传达了什么情绪或社交场景（例如：难过无语、开玩笑、打招呼等）。\n"
                        "3. 必须以极其简练的一句话返回，例如：'【图片内容：一只小猫在哭泣，并配有文字‘我太难了’，表达无奈和委屈的情绪】'。"
                    )
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{base64_str}"
                    }
                }
            ]
        }
    ]

    # 4. 智能选择支持 Vision 的模型
    use_platform_proxy = "ai-proxy" in base_url or "xmcore.top" in base_url
    model_to_use = model
    
    # 判断当前模型是否具备 Vision 特征，如果不具备，则进行智能切换
    has_vision = any(x in model_to_use.lower() for x in ("vision", "vl", "gpt-4o", "claude-3-5", "gemini-1.5"))
    if not has_vision:
        if use_platform_proxy:
            model_to_use = "gpt-4o-mini"
        elif "api.openai.com" in base_url or "openai" in base_url.lower():
            model_to_use = "gpt-4o-mini"

    logger.info(f"[media_helper] 正在使用模型 '{model_to_use}' 对图片 {file_path} 进行 Vision 识别描述...")

    # 5. 发送请求
    try:
        if use_platform_proxy:
            from src.utils.cloud_sync.helpers import try_load_sso_token, detect_cloud_url, generate_dev_jwt_token
            from src.utils.http_client import XMClient
            import asyncio

            token = try_load_sso_token() or generate_dev_jwt_token()
            cloud_url = detect_cloud_url()
            xm_client = XMClient(base_url=cloud_url, token=token, timeout=30, encryption=True)

            def do_request():
                return xm_client.post(
                    "/ai-proxy/v1/chat/completions",
                    body={
                        "model": model_to_use,
                        "messages": messages,
                    }
                )

            data = await asyncio.get_event_loop().run_in_executor(None, do_request)
            if data and "choices" in data and len(data["choices"]) > 0:
                content = data["choices"][0]["message"]["content"]
                logger.info(f"[media_helper] Vision 识别结果 (Proxy): {content}")
                return content.strip()

        else:
            if not api_key:
                return None
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{base_url}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model_to_use,
                        "messages": messages,
                        "max_tokens": 150,
                        "temperature": 0.5,
                    }
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if 'choices' in data and len(data['choices']) > 0:
                        content = data['choices'][0]['message']['content']
                        logger.info(f"[media_helper] Vision 识别结果 (Direct): {content}")
                        return content.strip()
                else:
                    logger.warning(f"[media_helper] Vision API 返回错误: {resp.status_code} - {resp.text}")
    except Exception as e:
        logger.warning(f"[media_helper] Vision 识别发生异常 (已自动降级): {e}")

    return None
