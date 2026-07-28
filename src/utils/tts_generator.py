import os
import tempfile
import uuid
import logging

logger = logging.getLogger("TTSGenerator")

import asyncio
import re
import websockets

def try_edge_tts(text: str, voice_id: str, out_path: str) -> bool:
    """
    尝试通过微软 Edge TTS 免 Key API 在云端合成超拟真的高质量音频。
    """
    # 映射 voice_id 到 Edge-TTS 的高级拟真音色
    voice_map = {
        "s_xiaomei": "zh-CN-XiaoxiaoNeural",
        "s_dashu": "zh-CN-YunxiNeural",
        "s_zhuli": "zh-CN-XiaohanNeural",
        "s_tongyin": "zh-CN-XiaoyiNeural"
    }
    
    vid_lower = voice_id.lower()
    edge_voice = "zh-CN-XiaoxiaoNeural" # 默认
    
    if "-" in voice_id and "neural" in vid_lower:
        edge_voice = voice_id
    else:
        for k, v in voice_map.items():
            if k in vid_lower:
                edge_voice = v
                break
                
        # 如果是自定义音色或没匹配到，可以根据关键字猜测或者默认
        if edge_voice == "zh-CN-XiaoxiaoNeural":
            if "dashu" in vid_lower or "male" in vid_lower or "man" in vid_lower:
                edge_voice = "zh-CN-YunxiNeural"
            elif "tongyin" in vid_lower or "child" in vid_lower or "kid" in vid_lower:
                edge_voice = "zh-CN-XiaoyiNeural"
            elif "zhuli" in vid_lower:
                edge_voice = "zh-CN-XiaohanNeural"

    # 全局时钟偏斜，以便在系统时间不准时自动校准
    global _clock_skew
    if "_clock_skew" not in globals():
        globals()["_clock_skew"] = 0.0

    async def _fetch():
        import uuid
        import secrets
        import hashlib
        import time
        from datetime import datetime as dt
        from datetime import timezone as tz

        trusted_client_token = "6A5AA1D4EAFF4E9FB37E23D68491D6F4"
        chrom_full = "143.0.3650.75"
        chrom_major = "143"
        sec_ms_gec_ver = f"1-{chrom_full}"

        def get_unix_timestamp():
            return dt.now(tz.utc).timestamp() + globals()["_clock_skew"]

        def get_sec_ms_gec():
            ticks = get_unix_timestamp()
            ticks += 11644473600
            ticks -= ticks % 300
            ticks *= 10000000
            str_to_hash = f"{ticks:.0f}{trusted_client_token}"
            return hashlib.sha256(str_to_hash.encode("ascii")).hexdigest().upper()

        def parse_rfc2616_date(date_str):
            try:
                return (
                    dt.strptime(date_str, "%a, %d %b %Y %H:%M:%S %Z")
                    .replace(tzinfo=tz.utc)
                    .timestamp()
                )
            except ValueError:
                return None

        # 尝试最多 2 次（第 1 次如果报 403 会根据 Date 调整 clock_skew 重新连接）
        for attempt in range(2):
            sec_ms_gec = get_sec_ms_gec()
            muid = secrets.token_hex(16).upper()

            uri = f"wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1?TrustedClientToken={trusted_client_token}&Sec-MS-GEC={sec_ms_gec}&Sec-MS-GEC-Version={sec_ms_gec_ver}"
            headers = {
                "User-Agent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrom_major}.0.0.0 Safari/537.36 Edg/{chrom_major}.0.0.0",
                "Accept-Encoding": "gzip, deflate, br, zstd",
                "Accept-Language": "en-US,en;q=0.9",
                "Pragma": "no-cache",
                "Cache-Control": "no-cache",
                "Origin": "chrome-extension://jdiccldimpdaibmpdkjnbmckianbfold",
                "Cookie": f"muid={muid};"
            }

            try:
                async with websockets.connect(uri, extra_headers=headers, open_timeout=4, close_timeout=2) as ws:
                    # 1. 发送配置请求
                    cfg_msg = (
                        "Content-Type:application/json; charset=utf-8\r\n"
                        "Path:speech.config\r\n\r\n"
                        '{"context":{"synthesis":{"audio":{"metadataoptions":{'
                        '"sentenceBoundaryEnabled":"false","wordBoundaryEnabled":"true"'
                        "},"
                        '"outputFormat":"audio-24khz-48kbitrate-mono-mp3"'
                        "}}}}\r\n"
                    )
                    await ws.send(cfg_msg)
                    
                    # 2. 发送合成文本请求 (SSML)
                    # 转换文本中的 XML 敏感字符
                    clean_text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    msg_id = uuid.uuid4().hex.upper()
                    ssml_msg = (
                        f"X-RequestId:{msg_id}\r\n"
                        "Content-Type:application/ssml+xml\r\n"
                        "Path:ssml\r\n\r\n"
                        "<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='zh-CN'>"
                        f"<voice name='{edge_voice}'>"
                        f"{clean_text}"
                        "</voice>"
                        "</speak>"
                    )
                    await ws.send(ssml_msg)
                    
                    # 3. 接收流式音频数据并写入文件
                    audio_data = bytearray()
                    async for message in ws:
                        if isinstance(message, bytes):
                            header_len = int.from_bytes(message[:2], "big")
                            audio_data.extend(message[2 + header_len:])
                        elif isinstance(message, str):
                            if "turn.end" in message:
                                break
                                
                    if len(audio_data) > 0:
                        with open(out_path, "wb") as f:
                            f.write(audio_data)
                        return True
                    return False
            except websockets.exceptions.InvalidStatusCode as e:
                # 遇到 403 进行自适应时钟校准后重试一次
                if e.status_code == 403 and attempt == 0:
                    server_date = None
                    if hasattr(e, 'headers') and e.headers:
                        server_date = e.headers.get("Date")
                    if server_date:
                        parsed_server_time = parse_rfc2616_date(server_date)
                        if parsed_server_time:
                            client_time = get_unix_timestamp()
                            diff = parsed_server_time - client_time
                            globals()["_clock_skew"] += diff
                            logger.info(f"[EdgeTTS] 握手被拒，由于时钟偏斜，已将时钟偏移校正了 {diff:.1f} 秒，正在重试...")
                            continue
                raise e

    # 安全地在各种同步/异步嵌套环境中执行 Edge-TTS 异步协程，彻底规避 Cannot run the event loop while another loop is running 报错
    try:
        coro = asyncio.wait_for(_fetch(), timeout=5.0)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # 当前线程已有正在运行的 loop，我们在独立后台线程中运行，完美避开冲突
            import threading
            result = [False]
            exception = [None]

            def worker():
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                try:
                    result[0] = new_loop.run_until_complete(coro)
                except Exception as ex:
                    exception[0] = ex
                finally:
                    new_loop.close()

            t = threading.Thread(target=worker)
            t.start()
            t.join(timeout=6.0)

            if t.is_alive():
                logger.warning("[EdgeTTS] 异步工作线程在 6 秒内未能退出，已强制放弃该任务以防主线程卡死")
                return False

            if exception[0]:
                raise exception[0]
            return result[0]
        else:
            # 当前没有运行的事件循环，直接新建并运行
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            try:
                success = new_loop.run_until_complete(coro)
                return success
            finally:
                new_loop.close()
    except Exception as e:
        logger.warning(f"[EdgeTTS] 云端高保真合成失败 (可能无网络): {e}")
        return False


def generate_tts_audio(text: str, voice_id: str) -> str:
    """
    将文本通过选定音色合成为本地临时音频文件。
    支持 Edge-TTS 云端高保真 -> pyttsx3 -> Windows SpVoice -> 纯字节兜底 的三重健壮性降级方案。
    """
    # 在非主线程（如 FastAPI 异步工作线程）中调用 COM 时，必须初始化 pythoncom
    try:
        import pythoncom
        pythoncom.CoInitialize()
    except Exception:
        pass

    temp_dir = tempfile.gettempdir()
    out_path = os.path.join(temp_dir, f"tts_{uuid.uuid4().hex[:8]}.wav")
    
    # 1. 优先尝试 Edge TTS 高保真云端音色克隆/合成
    if try_edge_tts(text, voice_id, out_path):
        logger.info(f"成功使用 Edge-TTS 将文本合成至: {out_path} (音色: {voice_id})")
        return out_path
    
    try:
        import pyttsx3
        engine = pyttsx3.init()
        engine.setProperty('rate', 150)
        voices = engine.getProperty('voices')
        if voices:
            # 根据 voice_id 的语义关键词选择最接近的系统音色
            # Windows 默认有中文女声/男声等，以关键词匹配最合适索引
            vid_lower = voice_id.lower()

            female_idx = 0
            male_idx = 0
            for i, v in enumerate(voices):
                v_name = (v.name or '').lower()
                if any(k in v_name for k in ['female', 'zira', 'huihui', 'yaoyao', '女', 'girl']):
                    female_idx = i
                    break
            for i, v in enumerate(voices):
                v_name = (v.name or '').lower()
                if any(k in v_name for k in ['male', 'david', 'kangkang', '男', 'boy']):
                    male_idx = i
                    break

            # 匹配 voice_id 到系统音色索引
            if any(k in vid_lower for k in ['tongyin', 'child', 'tong', 'kid', 'yaoyao']):
                # 童声：尝试第3个声音（可能存在儿童声），否则降级女声
                idx = 2 if len(voices) > 2 else female_idx
            elif any(k in vid_lower for k in ['dashu', 'male', 'man', 'dad', 'lao', '叔']):
                idx = male_idx
            else:
                # 默认（xiaomei/zhuli 等）→ 女声
                idx = female_idx

            selected_voice = voices[min(idx, len(voices) - 1)]
            engine.setProperty('voice', selected_voice.id)
            logger.info(f"[TTS] 音色 {voice_id} → 系统声音: {selected_voice.name}（本地系统预览）")

        engine.save_to_file(text, out_path)
        engine.runAndWait()
        logger.info(f"成功使用 pyttsx3 将文本合成至: {out_path} (音色: {voice_id})")
        return out_path
    except Exception as e:
        logger.warning(f"pyttsx3 合成失败，将采用 Windows SAPI 底层合成: {e}")
        try:
            import win32com.client
            speaker = win32com.client.Dispatch("SAPI.SpVoice")
            
            # SAPI 默认是直接播放，需要将其重定向到文件流中
            filestream = win32com.client.Dispatch("SAPI.SpFileStream")
            
            # 设置音频格式为 22kHz 16-bit Mono (SAFT22kHz16BitMono = 22)
            # 这能保证输出高保真音频，并且所有浏览器/音频解码器均原生支持播放
            filestream.Format.Type = 22
            
            # 3 = SSFMCreateForWrite
            filestream.Open(out_path, 3, False)
            speaker.AudioOutputStream = filestream
            speaker.Speak(text)
            filestream.Close()
            logger.info(f"成功使用 Windows SpVoice 将文本合成至: {out_path}")
            return out_path
        except Exception as ex:
            logger.error(f"SAPI 合成也失败了，将生成默认提示音: {ex}")
            # 兜底：如果完全无法合成，就创建一个静音或者预设测试音频
            with open(out_path, "wb") as f:
                # 写入标准 RIFF WAV 格式头部
                f.write(b'RIFF$\x1f\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00@\x1f\x00\x00@\x1f\x00\x00\x01\x00\x08\x00data\x00\x1f\x00\x00')
                f.write(b'\x80' * 8000)
            return out_path
