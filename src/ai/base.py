"""
AI 服务抽象基类
从 xm-bot4 AIServiceBase 逆向移植

所有 AI 平台（DeepSeek/Coze/Dify）继承此基类，
提供统一接口：start_chat() / generate_comment() / upload_file()
"""
import json
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Dict


class AIServiceBase(ABC):
    """AI 服务抽象基类（对标 xm-bot4 AIServiceBase）"""

    def __init__(self, token: str = "", platform: str = "unknown"):
        self.token = token
        self.platform = platform
        self._conversations: Dict[str, dict] = {}
        self._conv_file = (
            Path.home() / ".xm-ai-bot" / f"{platform}_conversations.json"
        )
        self._load_conversations()

    # ==================== 抽象接口 ====================

    @abstractmethod
    async def start_chat(
        self,
        agent_id: str,
        message: str,
        session_id: str = "",
        user_name: str = "",
        session_name: str = "",
        account_id: str = "",
        cache_session: bool = True,
        friend_tags: list = None,
        history_messages: list = None,
        **kwargs,
    ) -> dict:
        """发起对话

        返回: {"success": bool, "content": str, "elapsed": int, "error": str}
        """
        ...

    @abstractmethod
    async def generate_comment(
        self,
        content: str,
        agent_id: str = "",
        session_id: str = "",
        user_name: str = "",
        session_name: str = "",
        account_id: str = "",
    ) -> dict:
        """生成朋友圈评论

        返回: {"success": bool, "content": str, "elapsed": int}
        """
        ...

    async def upload_file(self, file_path: str, user_id: str = "") -> dict:
        """上传文件到 AI 平台（可选实现）

        返回: {"success": bool, "file_id": str}
        """
        return {"success": False, "error": "当前平台不支持文件上传"}

    async def close(self):
        """关闭资源"""
        pass

    def is_configured(self) -> bool:
        """检查是否已配置"""
        return bool(self.token)

    # ==================== 多智能体路由（子类可重写） ====================

    def register_agent(self, role: str, bot_id: str):
        """注册角色对应的智能体 Bot ID（多智能体路由）

        角色常量：
            chat          — 聊天业务
            moment_image  — 朋友圈图片生成
            moment_video  — 视频生成（预留）
        """
        pass

    def get_agent_id_for_role(self, role: str) -> str:
        """根据角色获取对应的 Bot ID，子类重写以支持多智能体路由"""
        return ""

    # ==================== 会话管理 ====================

    def _load_conversations(self):
        """加载会话记录"""
        try:
            if self._conv_file.exists():
                data = json.loads(self._conv_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._conversations = data
        except Exception:
            self._conversations = {}

    def _save_conversations(self):
        """保存会话记录"""
        try:
            self._conv_file.parent.mkdir(parents=True, exist_ok=True)
            self._conv_file.write_text(
                json.dumps(self._conversations, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[{self.platform}] 保存会话记录失败: {e}")

    def _get_conversation_id(self, session_id: str) -> Optional[str]:
        """获取会话 ID（用于平台的多轮对话）"""
        if session_id in self._conversations:
            entry = self._conversations[session_id]
            # 与 xm-bot4 一致：超过 48 小时的会话过期（172800秒）
            if time.time() - entry.get("last_time", 0) < 172800:
                return entry.get("conversation_id")
        return None

    def _update_conversation(self, session_id: str, conversation_id: str):
        """更新会话记录"""
        self._conversations[session_id] = {
            "conversation_id": conversation_id,
            "last_time": time.time(),
        }
        self._save_conversations()

    def _clear_conversation_cache(self, session_id: str):
        """清空指定会话缓存"""
        if session_id in self._conversations:
            del self._conversations[session_id]
            self._save_conversations()

    # ==================== 回复清理 ====================

    @staticmethod
    def clean_reply(content: str, max_len: int = 500) -> str:
        """清理 AI 回复（通用逻辑）
        
        注意：max_len 不能太小！之前是50字导致所有回复被截断成残废
        """
        if not content:
            return ""
        # 只做轻度清理：去掉 markdown 粗体、多余空白
        content = content.replace("**", "").replace("*", "").strip()
        # 去掉 <Reply> 或 <CRM_Action> 等 XML 标签
        import re
        content = re.sub(r'<[^>]+>', '', content).strip()
        # 合并多余换行
        content = re.sub(r'\n{3,}', '\n\n', content)

        if len(content) > max_len:
            content = content[:max_len]
            for p in ["。", "！", "？", "~", "\n"]:
                idx = content.rfind(p)
                if idx > max_len // 3:
                    content = content[: idx + 1]
                    break
        return content.strip()

    async def generate_image(self, prompt: str) -> Optional[str]:
        """根据提示词调用图像生成 API，返回生成的图片 URL（可选实现）"""
        return None

    async def generate_video(self, prompt: str) -> Optional[str]:
        """根据提示词调用视频生成 API，返回生成的视频 URL（可选实现）"""
        return None

    # ==================== async context manager ====================

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
