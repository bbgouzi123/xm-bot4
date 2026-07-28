import time
import asyncio
import logging

logger = logging.getLogger(__name__)

class EvaluatorReplyMixin:
    """回复队列入列与事件广播逻辑"""

    def _extract_bubble_info(self, bubble) -> tuple:
        if isinstance(bubble, (list, tuple)) and len(bubble) >= 2:
            return bubble[0], bubble[1]
        return "未知", str(bubble)

    def _enqueue_to_reply_buffer(self, name: str, last_msg: str, is_group: bool, user_name: str, is_at: bool, fp: str, wxid: str = None, is_physical_at: bool = None):
        now = time.time()
        key = wxid or name

        if is_physical_at is None:
            is_physical_at = is_at

        # 记录当前即将尝试自动回复的最新指纹
        if not hasattr(self, "_pending_reply_fps"):
            self._pending_reply_fps = {}
        self._pending_reply_fps[key] = fp

        # 🌟 终极安全防线：拦截微信内置系统号和公众号，不进行自动回复入队
        if not key or key.startswith("gh_") or key in (
            "fmessage", "medianote", "floatbottle", "filehelper", "newsapp", 
            "helper_entry", "mphelper", "weibo", "qqmail", "tmessage", "blogapp"
        ):
            return
        existing_fps = self._fingerprints.setdefault(key, set())


        # 🛡️ [重复回复根因修复] 若 fp 已记录在 fingerprints 且 buffer 中也无此 key（说明
        # 消息已从 buffer 弹出处理中，当前是 UIA 再次扫描到同一消息的重入），直接拦截。
        # 否则，若 fp 已在 buffer 的 msgs 中也属重复，同样拦截。
        if fp in existing_fps and key not in self._message_buffer:
            logger.debug(f"[重复拦截] 会话 '{name}' 消息 fp={fp[:8]}... 已处理过，阻断重复入队")
            return

        existing_fps.add(fp)
        if len(existing_fps) > 200:
            self._fingerprints[key] = set(list(existing_fps)[-100:])

        if key not in self._message_buffer:
            print(f"[监控] 成功将会话 '{name}' (wxid={wxid}) 加入消息缓冲区准备自动回复: '{last_msg}'")
            self._message_buffer[key] = {
                "name": name, 
                "wxid": wxid, 
                "msgs": [last_msg], 
                "last_active": now, 
                "is_group": is_group, 
                "user_name": user_name, 
                "is_at": is_at,
                "is_physical_at": is_physical_at
            }
            self._play_notification_sound()
            try:
                from src.utils.websocket_manager import ws_manager
                payload = {
                    "type": "new_chat_message",
                    "data": {
                        "name": name,
                        "wxid": wxid,
                        "is_group": is_group,
                        "user_name": user_name,
                        "message": last_msg
                    }
                }
                loop = asyncio.get_running_loop()
                if loop and loop.is_running():
                    asyncio.ensure_future(ws_manager.broadcast(payload))
                    asyncio.ensure_future(ws_manager.broadcast_task_update(
                        task_id=f"auto_reply_{key}",
                        task_type="自动回复",
                        status="running",
                        progress=5,
                        total=100,
                        message="收到微信新消息，准备进入智能回复决策队列...",
                        friend_name=name,
                        friend_wxid=wxid,
                        bot_wxid=self.account_id,
                        is_group=is_group,
                        incoming_msg=last_msg
                    ))
                    try:
                        from src.utils.status_overlay import status_overlay
                        status_overlay.update("消息捕获", "收到微信新消息，准备智能分析", name, 0x00A5FF)
                    except Exception:
                        pass
            except Exception as e:
                logger.error(f"[WS] 广播新消息及任务初始化失败: {e}")
        else:
            _is_new_msg = self._message_buffer[key]["msgs"][-1] != last_msg
            if _is_new_msg:
                self._message_buffer[key]["msgs"].append(last_msg)
                # 🌟 [滑窗修复] 只有真正新的消息到达时才延伸 2s 合并窗口；
                # 若 fp 相同（UIA 每 0.8s 重复扫到同一条未读消息），严禁刷新 last_active——
                # 否则会导致滑窗永不到期，缓冲区消息永久卡住，前端停在 5% 不回复！
                self._message_buffer[key]["last_active"] = now
            # fp 已在 existing_fps 中（同一条旧消息被重复扫描）：不刷新 last_active
            if is_at:
                self._message_buffer[key]["is_at"] = True
            if is_physical_at:
                self._message_buffer[key]["is_physical_at"] = True

        # 维度一：语义完结度断言 (LLM Fast-Pass)
        is_fast_pass = any(last_msg.strip().endswith(char) for char in ["。", "？", "?", "！", "!"])
        if is_fast_pass:
            self._message_buffer[key]["last_active"] = 0.0

        # 🌟 [兜底排水] 消息入队后，调度一个延迟排水任务。
        # 确保当 UIA 扫描循环尚未启动（如 bot_auto_start=False、启动时序差异等）时，
        # 缓冲区内的消息仍能被自动处理，不会永久卡在 5%。
        # 若扫描循环已在运行，它会在 3 秒内先于此任务处理消息，排水任务将无操作退出（安全幂等）。
        try:
            loop = asyncio.get_running_loop()
            if loop and loop.is_running():
                async def _self_drain_buffer():
                    await asyncio.sleep(3.0)
                    try:
                        await self._process_message_buffer()
                    except Exception as _drain_err:
                        logger.debug(f"[缓冲区自触发排水] 执行异常: {_drain_err}")
                asyncio.ensure_future(_self_drain_buffer())
        except Exception:
            pass
