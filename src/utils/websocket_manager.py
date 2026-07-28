import asyncio
import json
import logging
from typing import List, Dict, Any
from fastapi import WebSocket, WebSocketDisconnect
from .websocket_helpers import resolve_bot_wxid, fill_whitelist_status, update_status_overlay

logger = logging.getLogger(__name__)

class WebSocketManager:
    """全局单例的 WebSocket 管理器，负责前后端实时通信 (Phase 7 神经中枢)"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(WebSocketManager, cls).__new__(cls)
            cls._instance.active_connections = []
            cls._instance.loop = None
            cls._instance.sys_log_cache = []
            cls._instance.task_cache = {}
        return cls._instance

    async def connect(self, websocket: WebSocket):
        """处理新连接"""
        if self.loop is None:
            try:
                self.loop = asyncio.get_running_loop()
            except Exception:
                pass
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket 客户端已连接. 当前连接数: {len(self.active_connections)}")

        # 向新连接的客户端灌入历史缓存的活跃任务
        if hasattr(self, "task_cache") and self.task_cache:
            for cached_task in list(self.task_cache.values()):
                try:
                    await websocket.send_json(cached_task)
                except Exception:
                    pass

        # 向新连接的客户端灌入历史缓存的实时日志
        if hasattr(self, "sys_log_cache") and self.sys_log_cache:
            for cached_msg in self.sys_log_cache:
                try:
                    await websocket.send_json(cached_msg)
                except Exception:
                    pass

    def disconnect(self, websocket: WebSocket):
        """处理连接断开"""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info(f"WebSocket 客户端断开连接. 当前连接数: {len(self.active_connections)}")

    async def broadcast(self, message: Dict[str, Any], is_internal: bool = False):
        """广播 JSON 消息给所有已连接的前端"""
        # 缓存系统日志并执行节流合并
        if message.get("type") == "sys_log" and not is_internal:
            if not hasattr(self, "sys_log_queue"):
                self.sys_log_queue = []
            self.sys_log_queue.append(message)
            
            if not getattr(self, "sys_log_flush_task_active", False):
                self.sys_log_flush_task_active = True
                asyncio.create_task(self._flush_sys_log_queue())
            return

        if not self.active_connections:
            return
            
        disconnected = []
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        for connection in self.active_connections:
            try:
                if self.loop and current_loop != self.loop:
                    # 跨 event loop 广播，使用 run_coroutine_threadsafe 委派给主事件循环执行
                    asyncio.run_coroutine_threadsafe(connection.send_json(message), self.loop)
                else:
                    await connection.send_json(message)
            except Exception as e:
                logger.error(f"广播消息失败: {e}")
                disconnected.append(connection)
                
        # 自动清理死掉的连接
        for conn in disconnected:
            self.disconnect(conn)

    async def _flush_sys_log_queue(self):
        """延迟 250ms 合并高频日志后一次性推送，极大降低前端重绘渲染压力"""
        try:
            await asyncio.sleep(0.25)
            logs = getattr(self, "sys_log_queue", [])
            self.sys_log_queue = []
            self.sys_log_flush_task_active = False
            
            if not logs:
                return
                
            batch_text = "\n".join([item["data"] for item in logs if item.get("data")])
            if not batch_text:
                return
                
            batch_message = {"type": "sys_log", "data": batch_text}
            
            if not hasattr(self, "sys_log_cache"):
                self.sys_log_cache = []
            self.sys_log_cache.append(batch_message)
            self.sys_log_cache = self.sys_log_cache[-100:]
            
            await self.broadcast(batch_message, is_internal=True)
        except Exception as ex:
            logger.error(f"[WS] 节流合并系统日志并广播异常: {ex}")
            self.sys_log_flush_task_active = False

    async def broadcast_task_update(self, task_id: str, task_type: str, status: str, progress: int = 0, total: int = 0, message: str = "", **kwargs):
        """广播批量任务/自动执行任务的进度更新"""
        # 1. 自动提取或遗传 bot_wxid
        bot_wxid = resolve_bot_wxid(kwargs, task_id, self.task_cache)

        # 2. 自动补全是否加白/免打扰状态以供前端正常渲染“白名单/免打扰”标签，防止显示“未设置”
        fill_whitelist_status(task_type, bot_wxid, kwargs)

        task_data = {
            "task_id": task_id,
            "task_type": task_type,
            "status": status,
            "progress": progress,
            "total": total,
            "message": message,
            **kwargs
        }
        if bot_wxid:
            task_data["bot_wxid"] = bot_wxid

        data = {
            "type": "task_update",
            "data": task_data
        }

        # 缓存任务状态以供重连或新开启的控制中心拉取显示
        if not hasattr(self, "task_cache"):
            self.task_cache = {}
        
        is_finished = status in ("completed", "error", "success", "failed")
        is_whitelist = task_id.startswith("whitelist_")
        
        # 🌟 优化：对相同联系人的任务在写入缓存前进行去重，防止同一个未读生成多个冗余任务缓存
        new_name = task_data.get("friend_name")
        new_wxid = task_data.get("friend_wxid")
        new_type = task_data.get("task_type")
        
        if (new_name or new_wxid) and not is_finished:
            duplicate_keys = []
            for old_id, old_val in self.task_cache.items():
                if old_id != task_id:
                    old_data = old_val.get("data", {})
                    if old_data.get("task_type") == new_type:
                        old_name = old_data.get("friend_name")
                        old_wxid = old_data.get("friend_wxid")
                        if (new_name and old_name == new_name) or (new_wxid and old_wxid == new_wxid):
                            duplicate_keys.append(old_id)
            for k in duplicate_keys:
                self.task_cache.pop(k, None)

        # 只有在白名单任务状态真正为 "completed"（加白放行）时，或者普通任务正常结束 (is_finished) 时才从缓存中清除，
        # 白名单拦截拦截 (status="error") 时必须保留缓存，以供加白后能够从中提取拦截的消息进行内存直接重投
        should_pop = False
        if is_whitelist:
            if status == "completed":
                should_pop = True
        else:
            if is_finished:
                should_pop = True

        if should_pop:
            self.task_cache.pop(task_id, None)
        else:
            self.task_cache[task_id] = data

        if len(self.task_cache) > 200:
            completed_keys = [k for k, v in self.task_cache.items() if v.get("data", {}).get("status") in ("completed", "error")]
            if len(completed_keys) > 50:
                for k in completed_keys[:30]:
                    self.task_cache.pop(k, None)

        await self.broadcast(data)

        # 🌟 联动白名单拦截卡片状态自愈更新
        # ⚠️ [修复] 只在任务「完成」时清除对应 whitelist 缓存，防止 running(5%) 状态把
        #    whitelist 拦截卡片刷成 running，导致真正在白名单的好友也显示进度卡在 5%
        if task_id.startswith("auto_reply_") and task_type == "自动回复" and is_finished:
            suffix = task_id[len("auto_reply_"):]
            possible_whitelist_ids = [f"whitelist_{suffix}"]
            
            friend_name = kwargs.get("friend_name")
            friend_wxid = kwargs.get("friend_wxid")
            if friend_name:
                possible_whitelist_ids.append(f"whitelist_{friend_name}")
            if friend_wxid:
                possible_whitelist_ids.append(f"whitelist_{friend_wxid}")
                
            for wl_task_id in set(possible_whitelist_ids):
                if wl_task_id != task_id and wl_task_id in self.task_cache:
                    self.task_cache.pop(wl_task_id, None)
                    # 广播一次 completed 让前端关闭这个 whitelist 卡片
                    wl_task_data = task_data.copy()
                    wl_task_data["task_id"] = wl_task_id
                    wl_task_data["status"] = "completed"
                    wl_task_data["progress"] = 100
                    wl_data = {"type": "task_update", "data": wl_task_data}
                    await self.broadcast(wl_data)

        # 🌟 实时同步更新到右上角悬浮看板
        update_status_overlay(status, message, task_type, progress, total, kwargs)
        
    async def broadcast_alert(self, level: str, title: str, content: str, screenshot: str = None):
        """向前端发出异常/风控的弹窗报警"""
        data = {
            "type": "system_alert",
            "data": {
                "level": level,  # 'info', 'warning', 'error', 'fatal'
                "title": title,
                "content": content,
                "screenshot": screenshot
            }
        }
        await self.broadcast(data)

    # 别名兼容
    broadcast_json = broadcast

# 导出单例
ws_manager = WebSocketManager()