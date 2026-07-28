import threading
import logging

logger = logging.getLogger(__name__)

class StatusOverlay:
    """屏幕右上角鼠标穿透置顶的实时状态动态看板"""
    def __init__(self):
        self.status = "就绪"
        self.detail = "等待系统指令..."
        self.friend = "-"
        self.color = 0x00DC00  # 默认绿色主题色 (0xBBGGRR 格式)
        self.hwnd = None
        self._thread = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._wndproc_callback = None
        self.pending_whitelist_friend = None
        self.pending_is_group = False
        self.task_type = "自动回复"
        self.progress = 0

        # 历史记录/当前活跃任务卡片缓存
        self.history_title = "无活跃任务"
        self.history_detail = "聊天通道就绪，等待微信新消息..."
        self.history_status = "就绪"
        self.history_progress = 100
        self.history_color = 0x00DC00

def broadcast_overlay_status(enabled: bool):
    try:
        from src.utils.websocket_manager import ws_manager
        import asyncio
        if ws_manager.loop:
            asyncio.run_coroutine_threadsafe(
                ws_manager.broadcast({"type": "overlay_status", "data": {"enabled": enabled}}),
                ws_manager.loop
            )
    except Exception:
        pass

class StatusOverlay:
    """屏幕右上角鼠标穿透置顶的实时状态动态看板"""
    def __init__(self):
        self.status = "就绪"
        self.detail = "等待系统指令..."
        self.friend = "-"
        self.color = 0x00DC00  # 默认绿色主题色 (0xBBGGRR 格式)
        self.hwnd = None
        self._thread = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._wndproc_callback = None
        self.pending_whitelist_friend = None
        self.pending_is_group = False
        self.task_type = "自动回复"
        self.progress = 0

        # 历史记录/当前活跃任务卡片缓存
        self.history_title = "无活跃任务"
        self.history_detail = "聊天通道就绪，等待微信新消息..."
        self.history_status = "就绪"
        self.history_progress = 100
        self.history_color = 0x00DC00

    def start(self):
        with self._lock:
            # 清理已死亡的线程引用，避免阻碍下次启动
            if self._thread and not self._thread.is_alive():
                self._thread = None
            if self.hwnd or self._thread:
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run_loop, daemon=True, name="status-overlay")
            self._thread.start()
            logger.info("[状态看板] 屏幕右上角实时看板线程已启动")
        broadcast_overlay_status(True)

    def stop(self):
        with self._lock:
            self._stop_event.set()
            if self.hwnd:
                from src.utils.status_overlay_win32 import post_close_message
                post_close_message(self.hwnd)
            if self._thread and self._thread.is_alive():
                # 线程接收到 WM_CLOSE 后会在消息循环中退出，我们在此等待其彻底释放资源以防止重开冲突
                self._thread.join(timeout=0.5)
            self.hwnd = None
            self._thread = None
            logger.info("[状态看板] 屏幕右上角实时看板已停止并注销")
        broadcast_overlay_status(False)

    def update(self, status: str, detail: str, friend: str = "-", color: int = None, from_control_center: bool = False, task_type: str = "自动回复", progress: int = 0):
        """
        更新看板内容并重绘
        color: COLORREF，如 0x00DC00 表示绿色 (0xBBGGRR 格式)
        from_control_center: 为 True 表示该更新经由自动化控制中心推送通道触发，或为看板内部合法状态机变更
        """
        # 数据源收束约束：自动化控制中心是唯一数据源。
        # 允许通过的例外：初始化就绪状态、悬浮看板内部 F9 加白热键事件响应，以及白名单检测未回复拦截提示
        # 🌟 额外允许：物理锁定状态更新，或物理锁定期间的实时进度
        from src.uia.input_guard import uia_lock
        is_allowed = (
            from_control_center or
            (status == "就绪" and "等待系统指令" in detail) or
            status in ("已加白", "加白失败", "未回复", "消息捕获") or
            uia_lock.is_locked or
            status in ("物理锁定", "已中断")
        )
        if not is_allowed:
            # 静默忽略散落的其他直接更新，保证与控制中心展示完全一致
            return

        # 🌟 物理锁定期间的直接状态更新，实时同步到 UIA 物理锁的 WS 广播通道，保证两端信息实时一致
        if not from_control_center and uia_lock.is_locked:
            try:
                uia_lock.update_status(detail)
            except Exception as e_ws:
                logger.debug(f"[StatusOverlay] 同步更新 UIA 物理锁 WS 失败: {e_ws}")

        with self._lock:
            self.status = status
            self.task_type = task_type
            
            # 计算状态的颜色
            resolved_color = color
            if resolved_color is None:
                status_lower = status.lower()
                if any(x in status_lower for x in ["成功", "运行", "工作", "发送中", "进行中", "完成", "就绪", "生成", "排队", "决策", "分析", "等待", "准备"]):
                    resolved_color = 0x00DC00  # 绿色
                elif any(x in status_lower for x in ["避让", "暂停", "锁定", "物理锁定", "休眠", "未回复"]):
                    resolved_color = 0x00A5FF  # 橙黄
                elif any(x in status_lower for x in ["失败", "异常", "错误", "已中断", "熔断", "崩溃"]):
                    resolved_color = 0x3C3CFF  # 红色
                else:
                    resolved_color = 0x00DC00  # 默认绿色，杜绝离线灰色误导
            self.color = resolved_color

            # 区分“就绪”和“实际执行任务”
            is_idle = (status == "就绪" and ("等待系统指令" in detail or "就绪" in detail))
            
            if not is_idle:
                # 这是一个真实运行中或刚刚结束的任务，更新卡片内容
                if friend and friend != "-":
                    self.history_title = friend
                elif self.history_title == "无活跃任务":
                    self.history_title = "自动回复"
                
                # 净化 detail 字符串，使其更加简洁美观
                clean_detail = detail
                if "等待系统指令" in clean_detail:
                    clean_detail = "聊天通道就绪，等待微信新消息..."
                self.history_detail = clean_detail
                
                # 对齐卡片的 Badge 状态与颜色、进度条
                if status in ("已完成", "成功"):
                    self.history_status = "成功"
                    self.history_progress = 100
                    self.history_color = 0x00DC00  # 绿色
                elif status in ("异常", "失败", "错误", "已中断"):
                    self.history_status = "失败" if status != "已中断" else "中断"
                    self.history_progress = 100
                    self.history_color = 0x3C3CFF  # 红色
                else:
                    # 运行中、未回复或其它自定义状态任务
                    self.history_status = f"{progress}%" if progress > 0 else status
                    self.history_progress = progress
                    self.history_color = resolved_color if resolved_color is not None else 0x00DC00

        if self.hwnd:
            from src.utils.status_overlay_win32 import manage_overlay_timer, trigger_repaint
            # 当收到临时性的结束/异常/提示状态时，启动 3 秒定时器重置为“就绪”，避免永久停留
            if status in {"已完成", "成功", "异常", "失败", "错误", "已加白", "加白失败", "未回复", "发送成功", "发送失败", "已中断"}:
                manage_overlay_timer(self.hwnd, True)
            else:
                manage_overlay_timer(self.hwnd, False)
            trigger_repaint(self.hwnd)

    def _handle_f9_hotkey(self):
        with self._lock:
            name = self.pending_whitelist_friend
            is_group = self.pending_is_group
            if not name:
                return
            # 为了防止短时间内重复触发，先清空
            self.pending_whitelist_friend = None

        def _bg_add():
            try:
                nonlocal is_group
                import re
                from src.utils.contacts_cache import contacts_cache
                from src.crm.account_data import get_active_account, get_account_settings, save_account_settings
                wxid = get_active_account()

                # 自动纠偏逻辑：如果该会话名称在缓存中被归类为群聊，或者含有群聊后缀特征，则自动纠偏为群聊
                all_friends = contacts_cache.get_friends(wxid)
                all_groups = contacts_cache.get_groups(wxid)
                clean_name = re.sub(r'[\(（]\d+[\)）]$', '', name).strip()
                is_known_group = False
                if any(g.get("name") == name or g.get("name") == clean_name for g in all_groups):
                    is_known_group = True
                elif any((f.get("name") == name or f.get("name") == clean_name) and f.get("category") == "群聊" for f in all_friends):
                    is_known_group = True
                elif bool(re.search(r'[\(（]\d+[\)）]$', name)) or bool(re.search(r'[\(（]\d+[\)）]$', clean_name)):
                    is_known_group = True
                    
                if is_known_group:
                    is_group = True

                settings = get_account_settings(wxid)
                reply = settings.get("reply", {})

                whitelist_key = "auto_chat_group_whitelist" if is_group else "auto_chat_friend_whitelist"
                lst = reply.get(whitelist_key, [])
                if name not in lst:
                    lst = list(lst)
                    lst.append(name)
                    reply[whitelist_key] = lst

                excludes_key = "auto_chat_group_excludes" if is_group else "auto_chat_friend_excludes"
                ex_lst = reply.get(excludes_key, [])
                if name in ex_lst:
                    ex_lst = list(ex_lst)
                    ex_lst.remove(name)
                    reply[excludes_key] = ex_lst

                settings["reply"] = reply
                save_account_settings(settings, wxid)

                # 🌟 热更新同步：清除扫描器里关于当前好友的所有拦截指纹，确保能够立刻做出反应
                try:
                    from src.monitor.chat_monitor.message_scanner import MessageScannerLogic
                    for scanner in getattr(MessageScannerLogic, "_all_scanner_instances", []):
                        if scanner.account_id == wxid:
                            # 清理数据库中的所有指纹
                            try:
                                scanner.db.delete_session_fingerprints(name)
                            except Exception as db_err:
                                logger.debug(f"[状态看板] 清除指纹 DB 异常: {db_err}")
                            
                            # 清理内存缓存
                            scanner._fingerprints.pop(name, None)
                            scanner._last_seen_msg.pop(name, None)
                            scanner._manual_interventions.pop(name, None)
                            logger.info(f"[状态看板] 成功清除扫描器中好友 '{name}' 的指纹缓存，促使立刻重新回复")
                except Exception as clear_ex:
                    logger.debug(f"[状态看板] 清除扫描器指纹异常: {clear_ex}")

                logger.info(f"[状态看板] F9 加白成功: 已将 '{name}' 加入白名单")
                self.update("已加白", f"成功将 '{name}' 加入白名单", name, 0x00DC00) # 绿色
            except Exception as e:
                logger.error(f"[状态看板] F9 加白失败: {e}")
                self.update("失败", f"加白失败: {str(e)}", name, 0x3C3CFF) # 红色

        threading.Thread(target=_bg_add, daemon=True).start()

    def _run_loop(self):
        from src.utils.status_overlay_win32 import run_status_overlay_loop
        run_status_overlay_loop(self)


# 全局单例
status_overlay = StatusOverlay()
