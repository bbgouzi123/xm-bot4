import asyncio, time, logging, hashlib, re, random
from src.utils.uia_task_runner import is_uia_maintenance_active
from .base import _chat_daily_counter

logger = logging.getLogger(__name__)

# 🌟 全局工作流排他锁，彻底杜绝多个好友消息并发时，UIA 疯狂切换点击窗口的冲突
_workflow_lock = asyncio.Lock()


class ReplyEngineLogic:
    """AI 回复逻辑与软工作流 Mixin"""
    
    async def _safe_reply(self, name: str, message: str, is_group: bool, user_name: str, is_at: bool = False, wxid: str = None, is_physical_at: bool = None):
        logger.info(f"[ReplyEngine] _safe_reply 被调用。name={name}, wxid={wxid}, message='{message}', is_at={is_at}, is_physical_at={is_physical_at}")
        for _attr, _def in [("_message_queues", {}), ("_message_timers", {}),
                            ("_session_cool_down_until", {}), ("_session_reply_history", {}),
                            ("_message_is_at", {}), ("_message_is_physical_at", {})]:
            if not hasattr(self, _attr): setattr(self, _attr, _def)

        # 🌟 [双路径 key 统一] WCDB 路径携带 wxid，UIA 路径 wxid 可能为空(仅有 name)。
        # 若不统一 key，两路径各自独立防抖，同一条消息会被发送两次（双回复 Bug）。
        if not wxid:
            try:
                from src.utils.contacts_cache import contacts_cache
                from .session_lock_helper import resolve_wxid_from_cache
                _acct = getattr(self.driver, 'bot_wxid', None) or getattr(self.driver, '_wxid', None) or 'default'
                wxid = resolve_wxid_from_cache(contacts_cache, _acct, name)
            except Exception:
                pass

        key = wxid or name

        if is_physical_at is None:
            is_physical_at = is_at

        # 🌟 [is_at 粘滞锁] 合并 is_at：一旦该 key 的任意消息被标记为 is_at=True，则后续保持 True
        if is_at:
            self._message_is_at[key] = True
        elif key not in self._message_is_at:
            self._message_is_at[key] = False
        # 使用 key 级别的合并 is_at 值（不使用本次调用参数，防止被低权限消息覆盖）
        effective_is_at = self._message_is_at[key]
        if effective_is_at != is_at:
            logger.info(f"[is_at 粘滞锁] 会话 '{name}' (wxid={wxid}) 本次调用 is_at={is_at}，但 key 历史最高权限为 is_at=True，已提升为 True 防止误拦截")
        is_at = effective_is_at

        # 🌟 [is_physical_at 粘滞锁] 合并 is_physical_at：一旦该 key 的任意消息被标记为 is_physical_at=True，则后续保持 True
        if is_physical_at:
            self._message_is_physical_at[key] = True
        elif key not in self._message_is_physical_at:
            self._message_is_physical_at[key] = False
        effective_is_physical_at = self._message_is_physical_at[key]

        try:
            # 🌟 1. 检查是否处于冷却期（支持重请求豁免与延迟避让）
            now_ts = time.time()
            cool_down_until = self._session_cool_down_until.get(key, 0.0)
            
            is_re_request = False
            re_request_keywords = ["再发", "重发", "没收到", "重新", "再来", "发一份", "发一遍", "发个"]
            if any(kw in message for kw in re_request_keywords):
                is_re_request = True
                logger.info(f"[冷却豁免] 检出重请求意图词，豁免会话 '{name}' 的冷却锁拦截")

            if not is_re_request and now_ts < cool_down_until:
                wait_sec = cool_down_until - now_ts
                # 🌟 [冷却策略分流]
                # 若余量 > 60 秒：说明是上一次 AI 工作流执行中设置的「执行保护期」，
                # 此时新消息不能丢弃！直接将其重新放入 buffer，等待 _is_session_processing
                # 锁释放后（AI 回复完成），由 _process_message_buffer 的下一轮轮询正常处理。
                # 若余量 ≤ 60 秒：正常的「回复后冷却期」（10s），此时直接丢弃是合理的。
                if wait_sec > 60:
                    logger.info(f"[冷却让行] 会话 '{name}' (wxid={wxid}) 正处于 AI 工作流执行保护期（余 {wait_sec:.1f}s），"
                                f"新消息重新进入缓冲区排队，等待前一轮完成后处理。")
                    # 重新塞回缓冲区（使用极小的 last_active 使其立即可被弹出，
                    # 但由于 _is_session_processing 会拦截，实际会在锁释放后才被处理）
                    if key not in self._message_buffer:
                        self._message_buffer[key] = {"name": name, "wxid": wxid, "msgs": [message], "last_active": 0.0, "is_group": is_group, "user_name": user_name, "is_at": is_at}
                    else:
                        buf = self._message_buffer[key]
                        if message not in buf["msgs"]:
                            buf["msgs"].append(message)
                        buf["last_active"] = 0.0  # 同上，立即可弹出
                    return
                logger.info(f"[冷却锁拦截] 会话 '{name}' (wxid={wxid}) 处于自动回复冷却期，跳过本次回复（余 {wait_sec:.1f}s）")
                try:
                    from src.utils.websocket_manager import ws_manager
                    task_id = f"auto_reply_{key}"
                    await ws_manager.broadcast_task_update(
                        task_id=task_id, task_type="自动回复", status="completed", progress=100, total=100,
                        message=f"处于自动回复冷却期，跳过回复（余 {wait_sec:.1f}s）", friend_name=name, friend_wxid=wxid, incoming_msg=message
                    )
                except Exception as ws_ex:
                    logger.debug(f"[冷却拦截] 广播消除任务卡片异常: {ws_ex}")
                return  # 🌟 [双回复修复] 冷却期内直接 return，杜绝第二次触发

            # 🌟 2. 检查频率限制防刷
            history = self._session_reply_history.get(key, [])
            history = [ts for ts in history if now_ts - ts <= 60.0]
            self._session_reply_history[key] = history
            
            if len(history) >= 3:
                self._session_cool_down_until[key] = now_ts + 120.0
                logger.warning(f"[频次防刷拦截] 会话 '{name}' (wxid={wxid}) 1分钟内自动回复达 {len(history)} 次，触发风控挂起，静默锁定 120 秒！")
                from src.utils.websocket_manager import ws_manager
                task_id = f"auto_reply_{key}"
                await ws_manager.broadcast_task_update(
                    task_id=task_id, task_type="自动回复", status="completed", progress=100, total=100,
                    message="频次超限，该会话自动挂起冷却120秒", friend_name=name, friend_wxid=wxid, incoming_msg=message
                )
                self._clear_session_processing(name, wxid)
                return

            # 3. 压入聚合消息队列
            if key not in self._message_queues:
                self._message_queues[key] = []
            if message not in self._message_queues[key]:
                self._message_queues[key].append(message)

            if key in self._message_timers:
                self._message_timers[key].cancel()

            # 广播消息接收和防抖状态
            from src.utils.websocket_manager import ws_manager
            task_id = f"auto_reply_{key}"
            await ws_manager.broadcast_task_update(
                task_id=task_id,
                task_type="自动回复",
                status="running",
                progress=5,
                total=100,
                message="收到新消息，高频防抖合并中...",
                friend_name=name,
                friend_wxid=wxid,
                incoming_msg=message
            )

            # 3. 创建延迟触发任务
            async def _trigger_merged_reply():
                try:
                    await asyncio.sleep(1.0)  # 1.0 秒高频消息合并窗口
                    msgs = self._message_queues.pop(key, [])
                    if not msgs:
                        return
                        
                    clean_msgs = [m.strip() for m in msgs if m.strip()]
                    if not clean_msgs:
                        return
                    merged_msg = "。".join(clean_msgs)

                    try:
                        from src.crm.account_data import get_account_settings
                        cur_wxid = getattr(self.driver, 'bot_wxid', None) or getattr(self.driver, '_wxid', None) or 'default'
                        settings = get_account_settings(cur_wxid)
                        reply_cfg = settings.get("reply", {})
                        bot_group_auto_start = reply_cfg.get("bot_group_auto_start", False)
                        logger.info(f"[监控] 准备为会话 '{name}' (wxid={wxid}, is_group={is_group}, is_at={is_at}) 执行回复。")
                        
                        # 物理回复前置加锁，防御高频并发切换错发
                        self._mark_session_processing(name, wxid)
                        
                        snooze_rate = reply_cfg.get("snooze_rate", 0)
                        if snooze_rate > 0 and random.randint(1, 100) <= snooze_rate:
                            logger.info(f"[防封] 命中安全发送限制率 ({snooze_rate}%)，本次主动抛弃请求制造真人假死: {name}")
                            self._fingerprints.setdefault(key, set()).add(hashlib.md5(f"{key}:{merged_msg}".encode()).hexdigest())
                            await ws_manager.broadcast_task_update(
                                task_id=task_id,
                                task_type="自动回复",
                                status="completed",
                                progress=100,
                                total=100,
                                message="已命中防封主动抛弃策略，抛弃本次回复",
                                friend_name=name,
                                friend_wxid=wxid,
                                incoming_msg=merged_msg
                            )
                            return
                        await self._reply(name, merged_msg, is_group, user_name, is_at, wxid=wxid, is_physical_at=effective_is_physical_at)

                        # 成功回复后，将当前未读指纹记录为已回复，阻断假未读状态导致的点击自锁
                        if hasattr(self, "_pending_reply_fps") and key in self._pending_reply_fps:
                            success_fp = self._pending_reply_fps[key]
                            if not hasattr(self, "_replied_fingerprints"):
                                self._replied_fingerprints = set()
                            self._replied_fingerprints.add(success_fp)
                            self._pending_reply_fps.pop(key, None)
                        
                        now_ts = time.time()
                        if key not in self._session_reply_history:
                            self._session_reply_history[key] = []
                        self._session_reply_history[key].append(now_ts)
                        # 🌟 [双路径冷却共享] 同时给 wxid key 和 name key 都打冷却，
                        # 防止 UIA 路径 wxid 解析失败时以 name 为 key 绕过冷却锁
                        self._session_cool_down_until[key] = now_ts + 10.0
                        if name and name != key:
                            self._session_cool_down_until[name] = now_ts + 10.0
                    except Exception as e:
                        logger.error(f"[监控] _reply 执行异常: {e}", exc_info=True)
                        # 🛡️ [物理驱动失败重试] 当异常为物理驱动相关（InputGuard/UIBus 发送失败），
                        # 回滚该消息的 fingerprint 记录，并清除冷却，让下次扫描可以重新拾起此消息重试发送。
                        err_str = str(e).lower()
                        _is_physical_failure = any(k in err_str for k in (
                            "物理驱动", "inputguard", "物理驱动操作", "send_message", "避让", "冲突", "驱动受阻"
                        ))
                        if _is_physical_failure:
                            import hashlib as _hlib
                            _retry_fp = _hlib.md5(f"{key}:{merged_msg}".encode()).hexdigest()
                            fps = self._fingerprints.get(key, set())
                            fps.discard(_retry_fp)
                            # 也清除通用的 WCDB 格式指纹
                            _wcdb_fp = _hlib.md5(f"{name}:WCDB:{merged_msg}".encode()).hexdigest()
                            fps.discard(_wcdb_fp)
                            self._session_cool_down_until.pop(key, None)
                            logger.warning(
                                f"[物理驱动失败重试] 会话 '{name}' 物理发送失败，已回滚 fingerprint 并清除冷却，"
                                f"等待下一轮扫描重试发送。错误: {e}"
                            )
                        try:
                            await ws_manager.broadcast_task_update(
                                task_id=task_id, task_type="自动回复", status="completed", progress=100, total=100,
                                message=f"回复执行异常: {e}", friend_name=name, friend_wxid=wxid, incoming_msg=merged_msg
                            )
                        except Exception:
                            pass
                except asyncio.CancelledError:
                    raise
                except Exception as trigger_err:
                    logger.error(f"[监控] 消息防抖聚合器执行异常: {trigger_err}", exc_info=True)
                    try:
                        await ws_manager.broadcast_task_update(
                            task_id=task_id, task_type="自动回复", status="completed", progress=100, total=100,
                            message=f"聚合器执行异常: {trigger_err}", friend_name=name, friend_wxid=wxid, incoming_msg=message
                        )
                    except Exception:
                        pass
                finally:
                    current_task = asyncio.current_task()
                    if key not in self._message_timers or self._message_timers.get(key) == current_task:
                        self._clear_session_processing(name, wxid)
                    self._message_timers.pop(key, None)
                    # 🌟 [is_at 粘滞锁] 任务完成后重置该 key 的 is_at 状态，为下一轮新消息做好清零准备
                    self._message_is_at.pop(key, None)
                    self._message_is_physical_at.pop(key, None)

            self._message_timers[key] = asyncio.create_task(_trigger_merged_reply())
        except Exception as outer_e:
            logger.error(f"[监控] _safe_reply 启动失败，清理正在处理标志: {outer_e}", exc_info=True)
            self._clear_session_processing(name, wxid)

    async def _reply(self, name: str, message: str, is_group: bool = False, user_name: str = '', is_at: bool = False, wxid: str = None, is_physical_at: bool = None):
        from .reply_workflow import execute_reply_workflow
        from src.utils.websocket_manager import ws_manager
        
        key = wxid or name
        task_id = f"auto_reply_{key}"
        try:
            await ws_manager.broadcast_task_update(
                task_id=task_id,
                task_type="自动回复",
                status="running",
                progress=10,
                total=100,
                message="微信操作独占锁排队中（避免多好友冲突）...",
                friend_name=name,
                friend_wxid=wxid,
                incoming_msg=message
            )
        except Exception:
            pass

        # 🌟 [并发保护重构]
        # 原来此处有「5分钟执行保护冷却期」，但它会导致：
        #   在 AI 响应期间（往往 10-30s）到来的新消息被 _safe_reply 的冷却检查直接 completed 丢弃，
        #   用户体验上表现为「发了消息但机器人没有回复」。
        # 现在改为：
        #   · 依赖 _is_session_processing 锁 + _message_buffer 排队机制来防并发
        #     （_trigger_merged_reply 第 151 行加锁，finally 第 230 行释放）
        #   · _safe_reply 中对 >60s 余量冷却的「让行重入队」逻辑接管，确保新消息不被丢失
        #   · _workflow_lock（全局排他锁）+ asyncio.wait_for(120s) 已足够防止物理 UIA 并发冲突
        import time as _time
        if not hasattr(self, "_session_cool_down_until"):
            self._session_cool_down_until = {}
        # 注意：_protect_until 变量仍保留，用于后续 except 分支的比对清理（保持兼容性）
        _protect_until = _time.time() + 300.0  # 仅用于异常分支比对，不再预设冷却

        if is_physical_at is None:
            is_physical_at = is_at

        try:
            async with _workflow_lock:
                # 🛡️ 终极安全阀：对整个回复工作流施加 120 秒强行超时，
                # 防止由于任何 UIA 线程挂起、COM 阻塞或 AI 大模型请求无限卡死导致 _workflow_lock 永远无法释放
                # 进入锁后，立即设置执行中冷却保护期（此处才设，避免之前过早设置导致新消息被丢弃）
                if self._session_cool_down_until.get(key, 0.0) < _protect_until:
                    self._session_cool_down_until[key] = _protect_until
                    logger.debug(f"[双重触发防御] 会话 '{name}' (wxid={wxid}) 已进入工作流锁，设置执行中临时冷却（5分钟保护期）")
                await asyncio.wait_for(
                    execute_reply_workflow(
                        self, name, message, is_group, user_name, is_at,
                        account_id=self.account_id,
                        task_id=task_id,
                        wxid=wxid,
                        is_physical_at=is_physical_at
                    ),
                    timeout=120.0
                )
        except asyncio.TimeoutError:
            logger.error(f"[安全阀] 自动回复工作流在会话 '{name}' (wxid={wxid}) 运行超过 120s 发生严重超时，强行熔断并清理锁")
            try:
                from src.utils.uia_task_runner import report_uia_failure
                report_uia_failure(name)
            except Exception:
                pass
            try:
                # 强行释放 UIBus 会话锁
                from src.orchestrator.ui_bus import ui_bus
                ui_bus.release_session_lock(self.account_id, name)
            except Exception:
                pass
            try:
                from src.uia.input_guard import uia_lock as physical_lock
                physical_lock.force_release()
            except Exception:
                pass
            try:
                # 广播任务失败通知给前端以释放任务卡片，防止其永久保留 10%
                await ws_manager.broadcast_task_update(
                    task_id=task_id, task_type="自动回复", status="error", progress=0, total=1,
                    message="自动回复运行超时被强制熔断自愈", friend_name=name, friend_wxid=wxid, incoming_msg=message
                )
            except Exception:
                pass
            # 🔧 [Bug 4 Fix] 防无限死循环重入：
            # 120s 熔断后主动清理 _message_buffer 中该会话的挂起消息，
            # 并注册该消息的 fingerprint，防止下一轮扫描立刻重新弹出导致无限循环。
            try:
                import hashlib as _hlib
                _buf_key = wxid or name
                for _k in list(self._message_buffer.keys()):
                    _entry = self._message_buffer.get(_k)
                    if _entry and (_entry.get("name") == name or _entry.get("wxid") == wxid):
                        self._message_buffer.pop(_k, None)
                        logger.warning(f"[安全阀] 120s 熔断后主动清理缓冲区条目: key={_k}, 防止死循环重入")
                # 将本条消息的指纹注册为已处理，阻止重入
                _fuse_fp = _hlib.md5(f"{_buf_key}:{message}".encode()).hexdigest()
                if not hasattr(self, "_fingerprints"):
                    self._fingerprints = {}
                self._fingerprints.setdefault(_buf_key, set()).add(_fuse_fp)
            except Exception as _buf_ex:
                logger.debug(f"[安全阀] 清理缓冲区条目异常: {_buf_ex}")
            if self._session_cool_down_until.get(key, 0.0) == _protect_until:
                self._session_cool_down_until.pop(key, None)
        except Exception as workflow_err:
            # 💡 容错与自愈：工作流执行意外出错崩溃时，立即释放 5 分钟临时保护锁，避免用户被静默锁死
            if self._session_cool_down_until.get(key, 0.0) == _protect_until:
                self._session_cool_down_until.pop(key, None)
            try:
                # 广播任务失败通知给前端以释放任务卡片，防止其永久保留
                await ws_manager.broadcast_task_update(
                    task_id=task_id, task_type="自动回复", status="error", progress=0, total=1,
                    message=f"自动回复执行意外崩溃: {workflow_err}", friend_name=name, friend_wxid=wxid, incoming_msg=message
                )
            except Exception:
                pass
            raise workflow_err


