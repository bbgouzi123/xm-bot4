"""
ChatHistoryLegacyMixin — 兼容废弃的老 API（安全过渡层）

从 chat_history.py 中拆分以遵守 300 行代码质量规范。
这些方法基于本地文件索引，已被云端同步后端替代，保留仅供向后兼容。
"""
import json
import time
import logging

logger = logging.getLogger(__name__)


class ChatHistoryLegacyMixin:
    """已废弃的本地文件索引 API（向后兼容）"""

    def _load_sessions_index(self) -> dict:
        """从本地文件加载会话索引"""
        try:
            if self._sessions_index_path.exists():
                with open(self._sessions_index_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"[ChatHistory] 读取 sessions_index.json 异常: {e}")
        return {}

    def _save_sessions_index(self, index_data: dict):
        """保存会话索引到本地文件"""
        try:
            with open(self._sessions_index_path, "w", encoding="utf-8") as f:
                json.dump(index_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[ChatHistory] 保存 sessions_index.json 异常: {e}")

    def get_last_message_fingerprint(self, session_id: str) -> str:
        """获取最近一条消息的指纹"""
        index = self._load_sessions_index()
        if session_id in index:
            return index[session_id].get("last_message_fingerprint", "")
        return ""

    def build_context_messages(self, session_id: str, all_messages: list) -> list:
        """遇到已保存的最后消息指纹锚点，停止向上检索，大幅节省 Token 并避免复读"""
        last_fp = self.get_last_message_fingerprint(session_id)
        if not last_fp:
            return all_messages

        import hashlib
        context = []
        for msg in reversed(all_messages):
            content = msg.get("content", "")
            fp = hashlib.md5(content.strip().encode("utf-8")).hexdigest()
            if fp == last_fp:
                break
            context.insert(0, msg)
        return context

    def save_messages(self, session_id: str, session_name: str, messages: list, is_group: bool = False):
        """保存一系列消息（更新内存、推同步后端、更新本地索引）"""
        if not messages:
            return

        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = []

            for m in messages:
                is_filtered = 0
                content = m.get("content", "")
                if len(content) <= 2 and content in ["12", "1", "测", "测试", "是", "好", "哦", "啊", "嗯", "11"]:
                    is_filtered = 1

                self._sessions[session_id].append({
                    "role": m.get("role", ""),
                    "content": content,
                    "sender": m.get("sender", session_name),
                    "time": m.get("time", time.strftime("%Y-%m-%d %H:%M:%S")),
                    "is_filtered": is_filtered
                })

                self._async_push_to_cloud(
                    session_id,
                    m.get("sender", session_name),
                    m.get("role", ""),
                    content,
                    is_filtered
                )

            if len(self._sessions[session_id]) > 100:
                self._sessions[session_id] = self._sessions[session_id][-100:]

            last_msg = messages[-1]
            last_content = last_msg.get("content", "")
            import hashlib
            fp = hashlib.md5(last_content.strip().encode("utf-8")).hexdigest()

            index_data = self._load_sessions_index()
            index_data[session_id] = {
                "session_name": session_name,
                "session_id": session_id,
                "is_group": is_group,
                "last_message_fingerprint": fp,
                "last_message": last_content[:100],
                "last_updated": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            self._save_sessions_index(index_data)
