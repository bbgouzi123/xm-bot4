import logging
import re
import time
import os
from typing import Any

logger = logging.getLogger(__name__)


class VoiceTranscribedMessage(str):
    """标准的语音转文字消息结构，继承自 str 以实现对底层 AI/字符串操作的零摩擦兼容"""
    def __new__(cls, content: str, voice_text: str = None, media_path: str = None, is_success: bool = True):
        obj = str.__new__(cls, content)
        obj.voice_text = voice_text
        obj.media_path = media_path
        obj.is_success = is_success
        return obj

    def __repr__(self):
        return f"VoiceTranscribedMessage(content={super().__repr__()}, voice_text={self.voice_text!r}, media_path={self.media_path!r}, is_success={self.is_success})"


async def process_incoming_multimedia(engine: Any, name: str, message: str, is_group: bool, user_name: str, account_id: str, wxid: str = None) -> tuple[str, dict]:
    """如果消息包含多媒体，则调用 UIA 抓取真实内容，提取结构化元数据并投递事件"""
    actual_message = re.sub(r'^[^:：]*[:：]', '', message).strip() if (is_group and (':' in message or '：' in message)) else message
    
    # 统一规范语音消息格式，防止因第三方接口或数据库读取到的 summary 仅有秒数时导致无法识别为语音
    actual_message_stripped = actual_message.strip()
    if re.match(r'^(?:\[语音\])?\s*\d+["\'秒秒]$', actual_message_stripped):
        sec = re.findall(r'\d+', actual_message_stripped)[0]
        actual_message = f"[语音] {sec}\""

    media_meta = {
        "media_path": None,
        "media_type": None,
        "voice_text": None
    }

    # 0. 检查是否为已通过数据库解密导出的图片/媒体链接
    img_match = re.search(r'(chat_img_[a-fA-F0-9]{32}\.(?:png|jpg|jpeg|gif|webp))', actual_message, re.IGNORECASE)
    if img_match:
        img_name = img_match.group(1)
        from src.api.file_api import UPLOAD_DIR
        out_path = os.path.join(UPLOAD_DIR, img_name)
        if os.path.exists(out_path):
            media_meta["media_path"] = out_path
            media_meta["media_type"] = "image"
            actual_message = "[图片]"
            logger.info(f"[多媒体处理] 识别到已解密的本地数据库图片: {out_path}，自动填充 media_meta 并将消息设为 [图片]")

    # 🌟 双通道智能识别与剥离：如果扫描拿到的最新消息本身就包含微信已转好的语音文本
    if actual_message.startswith("语音") and any(x in actual_message for x in ('"', '秒')):
        cleaned = re.sub(r'^语音\s*\d+[\s\\\"\'秒分]*(?:秒|分)?', '', actual_message).strip()
        cleaned = re.sub(r'^[\\\"\'`\s\-\:\：]+', '', cleaned).strip()
        if cleaned and "翻译" not in cleaned and "转写" not in cleaned:
            # 已经有文本，重新组装为语音识别结果，包装为 VoiceTranscribedMessage
            actual_message = VoiceTranscribedMessage(f"[语音识别结果]: {cleaned}", cleaned, None, True)
            media_meta["voice_text"] = cleaned
            media_meta["media_type"] = "voice"
            logger.info(f"[多媒体处理] 扫描消息直接提取到语音文本，跳过UIA抓取: {cleaned}")

    if any(p in actual_message for p in ("[图片]", "[语音]", "[文件]", "图片", "语音", "文件")):
        is_already_solved_voice = (media_meta["media_type"] == "voice" and media_meta["voice_text"] and "[图片]" not in message and "[文件]" not in message)
        if not is_already_solved_voice:
            try:
                # 预检：如果当前输入框已经是目标会话，则跳过 ChatWith 动作，杜绝“已在会话却重复切换/双击”的干扰
                is_already_at_session = False
                try:
                    import app.state as app_state
                    active_name = getattr(app_state, 'active_chat_name', None)
                    active_wxid = getattr(app_state, 'active_chat_wxid', None)
                    if (active_name == name or 
                        (active_wxid and active_wxid == wxid) or 
                        (active_wxid and active_wxid == name)):
                        is_already_at_session = True
                        
                    if not is_already_at_session:
                        edit_ctrl = engine.driver._get_edit_control(name)
                        if edit_ctrl and edit_ctrl.Exists(0.15):
                            is_already_at_session = True
                except Exception:
                    pass

                from src.utils.uia_task_runner import run_uia_with_timeout
                if not is_already_at_session:
                    await run_uia_with_timeout(engine.driver.ChatWith, 10.0, name, foreground=True, wxid=wxid)
                else:
                    logger.info(f"[多媒体处理] 检测到当前聊天已处于 '{name}'，安全跳过 ChatWith 动作以防抖动")
                last_msgs = await run_uia_with_timeout(engine.driver.get_all_messages, 15.0, True, 3, name, True)
                if last_msgs:
                    for item in reversed(last_msgs):
                        if isinstance(item, (list, tuple)) and len(item) >= 2:
                            sender, content = item[0], item[1]
                        else:
                            sender, content = "未知", str(item)
                        if sender != (getattr(engine.driver, "_nickname", None) or "我"):
                            is_downloaded_img = "chat_img_" in content and "/api/file/download/" in content
                            if any(p in content for p in ("[语音识别结果]:", "[图片本地路径]:", "[文件本地路径]:", "[语音本地路径]:")) or is_downloaded_img:
                                # 识别出结构化信息，将其剥离并记录
                                if "[图片本地路径]:" in content:
                                    parts = content.split("[图片本地路径]:", 1)
                                    media_meta["media_path"] = parts[1].strip()
                                    media_meta["media_type"] = "image"
                                    actual_message = parts[0].strip() or "[图片]"
                                elif is_downloaded_img:
                                    img_match = re.search(r'(chat_img_[a-fA-F0-9]{32}\.(?:png|jpg|jpeg|gif|webp))', content, re.IGNORECASE)
                                    if img_match:
                                        img_name = img_match.group(1)
                                        from src.api.file_api import UPLOAD_DIR
                                        out_path = os.path.join(UPLOAD_DIR, img_name)
                                        if os.path.exists(out_path):
                                            media_meta["media_path"] = out_path
                                            media_meta["media_type"] = "image"
                                            actual_message = "[图片]"
                                elif "[文件本地路径]:" in content:
                                    parts = content.split("[文件本地路径]:", 1)
                                    media_meta["media_path"] = parts[1].strip()
                                    media_meta["media_type"] = "file"
                                    actual_message = parts[0].strip() or "[文件]"
                                elif "[语音本地路径]:" in content:
                                    parts = content.split("[语音本地路径]:", 1)
                                    media_meta["media_path"] = parts[1].strip()
                                    media_meta["media_type"] = "voice"
                                    if "[语音识别结果]:" in content:
                                        voice_text = content.split("[语音识别结果]:", 1)[1].split("[语音本地路径]:", 1)[0].strip()
                                        media_meta["voice_text"] = voice_text
                                        if not any(x in voice_text for x in ("超时", "未能提取")):
                                            actual_message = VoiceTranscribedMessage(f"[语音识别结果]: {voice_text}", voice_text, media_meta["media_path"], True)
                                        else:
                                            actual_message = VoiceTranscribedMessage("[语音识别结果]: (识别失败/超时)", voice_text, media_meta["media_path"], False)
                                    else:
                                        actual_message = VoiceTranscribedMessage("[语音识别结果]: (识别失败/超时)", None, media_meta["media_path"], False)
                                elif "[语音识别结果]:" in content:
                                    voice_text = content.split("[语音识别结果]:", 1)[1].strip()
                                    media_meta["voice_text"] = voice_text
                                    media_meta["media_type"] = "voice"
                                    if not any(x in voice_text for x in ("超时", "未能提取")):
                                        actual_message = VoiceTranscribedMessage(f"[语音识别结果]: {voice_text}", voice_text, None, True)
                                    else:
                                        actual_message = VoiceTranscribedMessage("[语音识别结果]: (识别失败/超时)", voice_text, None, False)
                                break
                            elif content == "[语音]" or content == "语音":
                                # 找到了此语音消息，但由于转文字或Whisper接口超时未能成功提取文本
                                media_meta["media_type"] = "voice"
                                actual_message = VoiceTranscribedMessage("[语音识别结果]: (识别失败/超时)", None, None, False)
                                break
            except Exception as e:
                logger.error(f"[监控] 解析多媒体消息异常: {e}")

    # 对未成功识别或解析到的语音消息进行兜底转换，保证其以 VoiceTranscribedMessage 结构传出
    if (actual_message == "[语音]" or media_meta.get("media_type") == "voice") and not isinstance(actual_message, VoiceTranscribedMessage):
        actual_message = VoiceTranscribedMessage("[语音识别结果]: (识别失败/超时)", None, media_meta.get("media_path"), False)

    # 如果是文件，进行文本解析注入
    if media_meta["media_type"] == "file":
        try:
            from src.api.config_api import _load_configs
            global_configs = _load_configs()
            fr_settings = global_configs.get("friend_request_settings", {})
            file_parsing_enabled = fr_settings.get("file_parsing_enabled", True)

            if file_parsing_enabled:
                file_path = media_meta["media_path"]
                if file_path and os.path.exists(file_path):
                    from src.utils.document_extractor import extract_file_content
                    file_text = extract_file_content(file_path)
                    if file_text:
                        filename = os.path.basename(file_path)
                        actual_message = f"{actual_message}\n\n【系统提示：已成功自动解析该文件({filename})内容如下】:\n{file_text}"
                        logger.info(f"[文件解析] 已成功解析新接收的文件 {filename} 并作为上下文附加至回复流中")
        except Exception as file_ex:
            logger.error(f"[文件解析] 提取文件文本内容发生异常: {file_ex}")

    try:
        from src.api.customer_api.adapter_factory import submit_event
        submit_event("new_message", {
            "account_id": account_id,
            "session_name": name,
            "user_name": user_name,
            "message": actual_message,
            "is_group": is_group,
            "timestamp": int(time.time()),
            "media_meta": media_meta
        })
    except Exception as ce:
        logger.error(f"[客户API] 投递新消息事件异常: {ce}")

    return actual_message, media_meta
