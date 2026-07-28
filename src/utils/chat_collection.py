import time
import logging
import threading
from typing import List, Dict, Set
from src.utils.cloud_sync import get_cloud_client

logger = logging.getLogger(__name__)

class ChatCollectionManager:
    """私域聊天数据全量高性能采集与监控管理器（高性能非阻塞批量上报）"""
    
    _instance = None
    _lock = threading.Lock()
    
    @classmethod
    def get_instance(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance
            
    def __init__(self):
        if hasattr(self, "_initialized") and self._initialized:
            return
        self._initialized = True
        self.queue: List[dict] = []
        self.seen_fingerprints: Set[str] = set() # 消息 MD5 去重
        self.queue_lock = threading.RLock()
        
        # 启动常驻后台批量上报工作线程
        self.worker_thread = threading.Thread(target=self._batch_report_loop, daemon=True, name="chat-collector")
        self.worker_thread.start()
        logger.info("[数据监控] 🚀 高性能聊天记录全量采集监控引擎启动")
        
    def collect_messages(self, account_id: str, session_id: str, messages: List[dict]):
        """收集单个会话的多条增量消息
        
        messages 中的元素字典格式:
            {
                "sender": "发件人昵称",
                "role": "user" | "assistant",
                "content": "消息正文"
            }
        """
        if not account_id or not session_id or not messages:
            return
            
        import hashlib
        new_items = []
        
        with self.queue_lock:
            for msg in messages:
                content = msg.get("content", "").strip()
                sender = msg.get("sender", "")
                role = msg.get("role", "user")
                if not content:
                    continue
                    
                # 基于账号、会话、发件人和正文内容生成去重指纹
                fp_src = f"{account_id}:{session_id}:{sender}:{content}"
                fp = hashlib.md5(fp_src.encode("utf-8")).hexdigest()
                
                if fp in self.seen_fingerprints:
                    continue
                    
                # 记录指纹，控制去重指纹集容量，防止超大内存开销
                self.seen_fingerprints.add(fp)
                if len(self.seen_fingerprints) > 10000:
                    # 淘汰部分老指纹
                    self.seen_fingerprints = set(list(self.seen_fingerprints)[-5000:])
                    
                # 构造同步后端标准数据结构
                is_filtered = len(content) <= 2 and content in ["12", "1", "测", "测试", "是", "好", "哦", "啊", "嗯", "11"]
                new_items.append({
                    "session_id": session_id,
                    "sender": sender,
                    "role": role,
                    "content": content,
                    "is_filtered": is_filtered,
                    "is_self": role == "assistant",
                })
                
                # 同步写入本地热 LRU 缓存，保证本地 AI 调用也有上下文
                try:
                    from src.utils.chat_history import ChatHistoryManager
                    history_mgr = ChatHistoryManager(account_id)
                    # 避免 add_message 重新上报，我们在这里仅需要调用 add_message 保持本地内存
                    # 为兼容原有逻辑，我们正常写入
                    history_mgr.add_message(session_id, sender, role, content)
                except Exception as ex:
                    logger.debug(f"[数据监控] 写入本地历史管理器异常: {ex}")
            
            if new_items:
                self.queue.extend(new_items)
                
    def _batch_report_loop(self):
        """后台轮询消费线程：每 5 秒或积攒 >= 10 条消息时批量打包推送"""
        while True:
            try:
                time.sleep(5)
                to_send = []
                with self.queue_lock:
                    if self.queue:
                        to_send = list(self.queue)
                        self.queue.clear()
                        
                if to_send:
                    logger.info(f"[数据监控] ☁️ 正在批量上报 {len(to_send)} 条私域聊天数据至 CRM 同步后端...")
                    try:
                        client = get_cloud_client()
                        success = client.sync_chat_history(to_send)
                        if success:
                            logger.info(f"[数据监控] ☁️ {len(to_send)} 条聊天数据上报同步后端成功")
                        else:
                            logger.warning("[数据监控] ☁️ 聊天数据上报返回失败状态，保留至下一次重试")
                            with self.queue_lock:
                                # 失败则回滚到队列头部
                                self.queue = to_send + self.queue
                    except Exception as err:
                        logger.error(f"[数据监控] ☁️ 聊天数据批量上报网络异常: {err}")
                        with self.queue_lock:
                            self.queue = to_send + self.queue
            except Exception as e:
                logger.error(f"[数据监控] 后台批量上报线程异常: {e}")
