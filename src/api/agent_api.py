"""
Agent 开放 API 接口模块。
为外部 Agent 提供回调结果提交、任务状态查询和任务队列管理。
"""

from __future__ import annotations

import os
import json
import logging
import time
from typing import Any, Dict, Optional
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from src.task.agent_reply_waiter import agent_reply_waiter

router = APIRouter(prefix="/api/agent", tags=["Agent"])
logger = logging.getLogger("AgentAPI")


class SubmitReplySchema(BaseModel):
    task_id: str = Field(..., description="待恢复的自动回复任务ID")
    action: str = Field(..., description="处理动作：reply / no_reply / defer")
    reply: Optional[str] = Field(None, description="回复文本（当 action 为 reply 时必填）")
    reason: Optional[str] = Field(None, description="转人工或跳过的原因")


class AgentAPI:
    """外部 Agent 可调用的后端状态摘要。"""

    def __init__(self) -> None:
        self.logger = logging.getLogger("AgentAPI")

    async def get_backend_status(self) -> Dict[str, Any]:
        """返回当前机器人实例的基本运行状态信息"""
        # 可以尝试动态感知状态
        return {
            "timestamp": int(time.time()),
            "tasks": agent_reply_waiter.get_pending_list(),
            "features": {
                "auto_reply": True,
                "moment_comment": False,
                "add_friend": False,
                "friend_request": False,
            },
        }


agent_api_instance = AgentAPI()


@router.get("/status")
async def get_status():
    """获取当前后端运行状态与特征支持列表"""
    return await agent_api_instance.get_backend_status()


@router.post("/submit_reply")
async def submit_agent_reply(payload: SubmitReplySchema):
    """外部智能体处理完毕后回调提交回复结果"""
    action = payload.action
    task_id = payload.task_id
    reply = payload.reply
    reason = payload.reason

    if action not in ("reply", "no_reply", "defer"):
        raise HTTPException(
            status_code=400,
            detail=f"无效的 action: {action}，合法值为 reply / no_reply / defer"
        )

    if action == "reply" and not reply:
        raise HTTPException(
            status_code=400,
            detail="action 为 reply 时, reply 回复字段不能为空"
        )

    ok = agent_reply_waiter.submit_result(
        task_id, action=action, reply=reply, reason=reason
    )
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"任务 {task_id} 不存在或已超时失效"
        )

    return {
        "success": True,
        "taskId": task_id,
        "action": action
    }


@router.get("/queue")
async def get_queue():
    """获取当前挂起等待 AI 介入的消息队列"""
    return {
        "connected": True,
        "reply_mode": "agent",
        "pending_count": len(agent_reply_waiter.get_pending_list()),
        "tasks": agent_reply_waiter.get_pending_list()
    }


from pathlib import Path
VOICES_FILE = str(Path.home() / ".xm-ai-bot" / "voices.json")
os.makedirs(os.path.dirname(VOICES_FILE), exist_ok=True)

def load_voices():
    if not os.path.exists(VOICES_FILE):
        default_voices = [
            {"id": "S_xiaomei", "name": "客服小美 (S_xiaomei)"},
            {"id": "S_dashu", "name": "沉稳大叔 (S_dashu)"},
            {"id": "S_zhuli", "name": "金牌助理 (S_zhuli)"},
            {"id": "S_tongyin", "name": "可爱童音 (S_tongyin)"}
        ]
        try:
            with open(VOICES_FILE, "w", encoding="utf-8") as f:
                json.dump(default_voices, f, ensure_ascii=False, indent=4)
        except Exception:
            pass
        return default_voices
    try:
        with open(VOICES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_voices(voices):
    try:
        with open(VOICES_FILE, "w", encoding="utf-8") as f:
            json.dump(voices, f, ensure_ascii=False, indent=4)
    except Exception:
        pass


class CreateVoiceSchema(BaseModel):
    name: str = Field(..., description="音色名称")
    audio_path: Optional[str] = Field(None, description="已上传的音色样本音频文件路径")


@router.get("/voice/voices")
async def get_cloned_voices():
    """获取用户已克隆的音色列表，供前端选择配置"""
    return {
        "success": True,
        "data": load_voices()
    }


@router.post("/voice/create")
async def create_voice(payload: CreateVoiceSchema):
    """录入/创建新的自定义音色"""
    voices = load_voices()
    new_id = f"V_custom_{int(time.time())}"
    new_voice = {
        "id": new_id,
        "name": f"{payload.name} (自定义)",
        "audio_path": payload.audio_path
    }
    voices.append(new_voice)
    save_voices(voices)
    return {
        "success": True,
        "data": new_voice
    }


@router.delete("/voice/delete")
async def delete_voice(voice_id: str):
    """删除指定的自定义音色"""
    voices = load_voices()
    builtin_ids = {"S_xiaomei", "S_dashu", "S_zhuli", "S_tongyin"}
    if voice_id in builtin_ids:
        raise HTTPException(status_code=400, detail="系统内置音色无法删除")
    
    new_voices = [v for v in voices if v.get("id") != voice_id]
    if len(new_voices) == len(voices):
        raise HTTPException(status_code=404, detail="未找到指定的音色")
        
    save_voices(new_voices)
    return {
        "success": True,
        "detail": f"音色 {voice_id} 删除成功"
    }


@router.get("/voice/preview")
async def preview_voice(voice_id: str, text: Optional[str] = None):
    """试听并预览当前所选音色"""
    if not text:
        text = "您好，这是正在测试的声音预览，系统环境一切正常。"
    try:
        from src.utils.tts_generator import generate_tts_audio
        wav_path = generate_tts_audio(text, voice_id)
        if os.path.exists(wav_path):
            return FileResponse(wav_path, media_type="audio/wav")
        else:
            raise HTTPException(status_code=500, detail="生成音频预览失败")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"试听失败: {str(e)}")

