"""聊天历史管理器 — 纯内存 LRU + 同步后端持久化"""
import time
import threading
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List
from src.utils.chat_history_legacy import ChatHistoryLegacyMixin

logger = logging.getLogger(__name__)

# 🔧 [Bug 2b Fix] 使用有界线程池替代裸 threading.Thread，防止高并发推送时线程无限泄漏
# 最多 2 个并发推送线程，超出时新任务在队列中等待，不会创建新线程
_cloud_push_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="chat-push")


def parse_time_to_ts(time_str: str) -> float:
    if not time_str:
        return 0.0
    try:
        clean_time = time_str.replace('T', ' ').replace('Z', '').split('.')[0].strip()
        return time.mktime(time.strptime(clean_time, "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return 0.0


class ChatHistoryManager(ChatHistoryLegacyMixin):
    """聊天历史管理器（多账号隔离 + 内存 LRU + 同步后端持久化）"""

    _instances: Dict[str, 'ChatHistoryManager'] = {}

    def __new__(cls, account_id: str = "main"):
        acc = account_id or "main"
        if acc not in cls._instances:
            instance = super().__new__(cls)
            cls._instances[acc] = instance
            instance._initialized = False
        return cls._instances[acc]

    def __init__(self, account_id: str = "main"):
        if getattr(self, '_initialized', False):
            return
        self._initialized = True
        self.account_id = account_id or "main"
        self._sessions: Dict[str, List[dict]] = {}
        self._lock = threading.RLock()
        self._industry_switched_at: float = 0.0
        self.base_dir = Path(f"data/chat_history/{self.account_id}")
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._sessions_index_path = self.base_dir / "sessions_index.json"
        logger.info(f"[ChatHistory] 初始化账号 {self.account_id} 内存记忆库，本地目录: {self.base_dir}")

    def mark_industry_switched(self):
        """切换行业清空内存"""
        with self._lock:
            for sid in list(self._sessions.keys()):
                self._sessions[sid] = []
            self._industry_switched_at = time.time()
        logger.info(f"[行业切换] 账号 '{self.account_id}' 已清空会话上下文并开启60秒保护期")

    def _fetch_session_from_cloud(self, session_id: str):
        """精准懒加载：从同步后端拉取单个会话的最近聊天记录到内存"""
        if time.time() - self._industry_switched_at < 60.0:
            return
        try:
            from src.utils.cloud_sync import get_cloud_client
            from urllib.parse import quote
            cloud = get_cloud_client()
            safe_session_id = quote(session_id)
            bot = quote(self.account_id or "", safe="")
            data = cloud._get(f"/api/v1/chat/history?session_id={safe_session_id}&limit=100&bot_wxid={bot}", need_auth=True)
            if data and isinstance(data, list):
                with self._lock:
                    self._sessions[session_id] = [{
                        "role": item.get("role", ""),
                        "content": item.get("content", ""),
                        "sender": item.get("sender", ""),
                        "time": item.get("created_at", ""),
                        "is_filtered": item.get("is_filtered", False) or item.get("is_filtered", 0) == 1,
                    } for item in data]
                logger.info(f"[ChatHistory] 已拉取会话 '{session_id}' 的 {len(data)} 条历史记忆")
        except Exception as e:
            logger.debug(f"[ChatHistory] 同步后端历史加载跳过 ({session_id}): {e}")


    # ==================== 会话索引 ====================

    def get_sessions_index(self) -> dict:
        with self._lock:
            return {
                sid: {
                    "session_name": sid, "session_id": sid, "is_group": False,
                    "last_message": messages[-1].get("content", "")[:100],
                    "last_updated": messages[-1].get("time", "")
                }
                for sid, messages in self._sessions.items() if messages
            }

    def update_session_index(self, session_name: str, session_id: str, last_message: str = "", is_group: bool = False):
        pass


    # ==================== 消息历史 ====================

    def load_history(self, session_id: str) -> list:
        """加载会话历史消息（纯内存）"""
        with self._lock:
            messages = self._sessions.get(session_id, [])
            return [{"role": m["role"], "content": m["content"], "sender": m.get("sender", ""), "time_str": m.get("time", "")} for m in messages]

    def clear_history(self, session_id: str):
        """【一键洗脑】清空指定用户的记忆"""
        with self._lock:
            self._sessions.pop(session_id, None)
        logger.info(f"[ChatHistory] 会话 {session_id} 的记忆已被清空")

        # 异步通知同步后端清除
        def _cloud_clear():
            try:
                from src.utils.cloud_sync import get_cloud_client
                get_cloud_client()._post(
                    "/api/v1/chat-history/clear",
                    {"session_id": session_id},
                    need_auth=True,
                )
            except Exception:
                pass
        threading.Thread(target=_cloud_clear, daemon=True).start()

    def add_message(self, session_id: str, session_name: str, role: str, content: str, is_group: bool = False):
        """添加一条消息到内存 + 异步推同步后端"""
        if not self.account_id or not content.strip():
            return

        is_filtered = 0
        if len(content) <= 2 and content in ["12", "1", "测", "测试", "是", "好", "哦", "啊", "嗯", "11"]:
            is_filtered = 1

        msg = {
            "role": role,
            "content": content,
            "sender": session_name,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "is_filtered": is_filtered,
        }

        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = []
            self._sessions[session_id].append(msg)
            # 容量控制：每个 session 最多 100 条
            if len(self._sessions[session_id]) > 100:
                self._sessions[session_id] = self._sessions[session_id][-100:]

        # 异步推送到同步后端
        self._async_push_to_cloud(session_id, session_name, role, content, is_filtered)

    def get_context(self, session_id: str, window_size: int = 10, max_chars: int = 4000) -> list:
        """【核心能力】获取"脱水合并后"的动态时序滑动窗口记忆（纯内存拉取 or 同步后端按需回溯）"""
        if not self.account_id or not session_id:
            return []

        # 精准懒加载同步后端记忆：只有当本地记录彻底为空时，才主动去抓取同步后端前科
        with self._lock:
            needs_fetch = session_id not in self._sessions
            
        if needs_fetch:
            self._fetch_session_from_cloud(session_id)

        with self._lock:
            messages = self._sessions.get(session_id, [])

            # 🌟 2小时无应答上下文重置逻辑（放宽至 2 小时，兼顾连贯与防溢出）
            if messages:
                last_msg = messages[-1]
                last_time_str = last_msg.get("time", "")
                if last_time_str:
                    try:
                        clean_time = last_time_str.replace('T', ' ').replace('Z', '').split('.')[0].strip()
                        last_ts = time.mktime(time.strptime(clean_time, "%Y-%m-%d %H:%M:%S"))
                        if time.time() - last_ts > 7200:
                            logger.info(f"[ChatHistory] 会话 '{session_id}' 超过 2 小时无活跃，已自动重置上下文")
                            self._sessions[session_id] = []
                            messages = []
                    except Exception as e:
                        logger.error(f"[ChatHistory] 解析时间 '{last_time_str}' 异常: {e}")
            
            # 1. 过滤垃圾消息，对群聊（session_id以@chatroom结尾）拼装发言人前缀
            is_group_chat = session_id.endswith("@chatroom")
            valid_msgs = []
            for m in messages:
                if m.get("is_filtered", 0):
                    continue
                content = m["content"].strip()
                if "【画像】" in content:
                    content = content.split("【画像】")[0].strip()
                if not content:
                    continue
                
                # 如果是群聊中的 user 消息，且有明确的发送人，加上发送人昵称前缀，让 AI 能分清是谁说的话
                sender = m.get("sender", "")
                if is_group_chat and m["role"] == "user" and sender:
                    content = f"{sender}: {content}"

                valid_msgs.append({
                    "role": m["role"],
                    "content": content,
                    "content_type": "text",
                    "sender": sender,
                    "time": m.get("time", "")
                })

            # 2. 合并连续同发送方（同 role）的消息，但在群聊中如果发送人不同则不予合并
            merged_msgs = []
            for msg in valid_msgs:
                time_diff_ok = True
                if merged_msgs and msg.get("time") and merged_msgs[-1].get("time"):
                    t1 = parse_time_to_ts(merged_msgs[-1]["time"])
                    t2 = parse_time_to_ts(msg["time"])
                    if t1 > 0 and t2 > 0 and abs(t2 - t1) > 30.0:
                        time_diff_ok = False

                is_same_sender = True
                if is_group_chat and merged_msgs and merged_msgs[-1].get("sender") != msg.get("sender"):
                    is_same_sender = False

                if merged_msgs and merged_msgs[-1]["role"] == msg["role"] and time_diff_ok and is_same_sender:
                    # 合并内容，用换行连接
                    merged_msgs[-1]["content"] = merged_msgs[-1]["content"] + "\n" + msg["content"]
                    merged_msgs[-1]["time"] = msg.get("time", merged_msgs[-1].get("time", ""))
                else:
                    # 拷贝数据结构
                    merged_msgs.append({
                        "role": msg["role"],
                        "content": msg["content"],
                        "content_type": "text",
                        "sender": msg.get("sender", ""),
                        "time": msg.get("time", "")
                    })


            # 3. 滑动窗口截取：只保留最近 window_size 轮合并后的对话
            # 并从新到旧反向检查，以控制总字符数不超过 max_chars
            final_msgs = []
            total_chars = 0
            for msg in reversed(merged_msgs):
                if len(final_msgs) >= window_size:
                    break
                msg_len = len(msg["content"])
                if total_chars + msg_len > max_chars:
                    # 单条特别长的防溢出切片
                    if not final_msgs:
                        allowed_len = max_chars - total_chars
                        msg["content"] = msg["content"][-allowed_len:]
                        final_msgs.append(msg)
                    break
                
                final_msgs.append(msg)
                total_chars += msg_len

            return list(reversed(final_msgs))

    # ==================== 同步后端推送 ====================

    def _async_push_to_cloud(self, session_id: str, sender: str, role: str, content: str, is_filtered: int):
        """异步单条同步：借用批处理接口上报（统一出口）"""
        is_self = (role == "assistant")
        def _push():
            try:
                from src.utils.cloud_sync import get_cloud_client
                get_cloud_client().sync_chat_history([{"session_id": session_id, "sender": sender, "role": role, "content": content, "is_filtered": bool(is_filtered), "is_self": is_self}])
            except Exception as e:
                logger.debug(f"[ChatHistory·同步后端] 推送失败: {e}")

        # 🔧 [Bug 2b Fix] 使用有界线程池提交，避免裸线程无限堆积
        try:
            _cloud_push_executor.submit(_push)
        except RuntimeError:
            # executor 已关闭（进程退出阶段），静默忽略
            pass
