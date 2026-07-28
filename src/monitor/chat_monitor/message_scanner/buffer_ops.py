import time
import asyncio
import logging
from src.utils.uia_task_runner import run_uia_with_timeout

logger = logging.getLogger(__name__)

class BufferOpsMixin:
    """缓冲队列合并与窄屏返回会话列表控制"""

    async def _process_message_buffer(self):
        now = time.time()
        for key, data in list(self._message_buffer.items()):
            # 维度一：智能消息合并缓冲机制，滑窗 2.0 秒
            if now - data["last_active"] >= 2.0:
                is_proc = self._is_session_processing(data["name"], data.get("wxid"))
                if is_proc:
                    # 💡 【消息无损排队机制】
                    # 如果上一次 RPA 物理回复还在进行中，我们暂不弹出该消息，保留在缓冲区内。
                    # 等待物理回复释放锁后，下一轮轮询再次弹出，以彻底解决用户高频追问/再发一份被丢弃的 bug。
                    logger.info(f"[缓冲区处理器] 会话 '{data['name']}' (wxid={data.get('wxid')}) 物理回复执行中，避让排队...")
                    continue
                
                # 🌟 黄金法则：若该会话当前已被操作员人工接管，则立即丢弃缓冲区中的回复任务，防止抢焦冲突
                try:
                    from src.utils.contacts_cache import contacts_cache
                    f_wxid = data.get("wxid") or key
                    is_takeover = any(
                        f.get("is_takeover", False)
                        for f in contacts_cache.get_friends(self.account_id)
                        if f.get("name") == data["name"] or f.get("wxid") == f_wxid
                    )
                    if is_takeover:
                        self._message_buffer.pop(key)
                        logger.info(f"[缓冲区处理器] 会话 '{data['name']}' (wxid={f_wxid}) 已被操作员人工接管，丢弃缓冲回复，避让手工干预。")
                        continue
                except Exception as takeover_ex:
                    logger.debug(f"[缓冲区处理器] 检查人工接管状态异常: {takeover_ex}")

                self._message_buffer.pop(key)
                logger.info(f"[缓冲区处理器] 弹出消息 key={key}, name={data['name']}, wxid={data.get('wxid')}")
                self._stats["detected"] += 1
                try:
                    merged_msg = "。".join(data["msgs"])
                    logger.info(f"[缓冲区处理器] 准备为会话 '{data['name']}' 发起 _safe_reply 后台任务。合并消息: '{merged_msg}'")
                    asyncio.create_task(self._safe_reply(
                        name=data["name"],
                        message=merged_msg,
                        is_group=data["is_group"],
                        user_name=data["user_name"],
                        is_at=data["is_at"],
                        wxid=data.get("wxid"),
                        is_physical_at=data.get("is_physical_at", data["is_at"])
                    ))
                except Exception as buffer_ex:
                    logger.error(f"[缓冲区处理器] 创建 _safe_reply 任务异常: {buffer_ex}", exc_info=True)

        for pname in list(self._suspicious_pending.keys()):
            pdata = self._suspicious_pending[pname]
            if now - pdata["time"] > 60:
                logger.info(f"[{pname}] 悬疑消息超时（>60s），从待处理中移除，不永久拉黑。")
                self._suspicious_pending.pop(pname, None)

    async def _check_narrow_screen_back(self, user_active_now: bool):
        # 窄屏兼容策略：如果当前正处于窄屏的聊天内容页（存在左上角“返回”按钮），且无合并及正在处理的回复任务，且用户此时空闲，
        # 则自动点击返回以回退到会话列表，这样在后续扫描中才能即时接收和更新其它联系人的未读消息
        import win32gui as _win32gui_back
        from src.utils.user_activity import is_user_active
        _wechat_is_fg = (_win32gui_back.GetForegroundWindow() == self.driver.hwnd)
        # 双重守卫：必须是前台窗口、用户空闲(>=3秒无键鼠)，才允许物理点击
        if not self._message_buffer and not self._processing and _wechat_is_fg \
                and not is_user_active(cooldown_ms=3000):
            # 🔧 [Bug 3 Fix] 熔断器保护：若 UIA 近期连续失败次数 >= 2，
            # 跳过窄屏返回操作，避免在线程池重建期 COM 尚未稳定时再次触发超时
            try:
                from src.utils.uia_circuit_breaker import _global_fail_count
                if _global_fail_count >= 2:
                    return
            except Exception:
                pass
            try:
                clicked = await run_uia_with_timeout(self.driver._check_and_click_back_button, 5.0)
                if clicked:
                    logger.info("[监控] 检测到微信处于窄屏会话界面且当前无回复任务，已自动点击返回以显式会话列表，用于接收新消息")
                    await asyncio.sleep(0.5)
            except Exception as e:
                logger.warning(f"[监控] 自动检查/点击窄屏返回按钮操作发生超时或异常，已忽略: {e}")
