import logging
import threading
import time
from collections import deque
from typing import Any, Callable, Dict, Optional, List

from .types import UICommand, UICommandKind, UICommandPriority, UICommandStatus, UICommandHandler
from .metrics import UIBusMetrics
from .scheduler import UIBusScheduler

logger = logging.getLogger(__name__)

class UIBus:
    _instance: Optional["UIBus"] = None
    _instance_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True

        self._scheduler = UIBusScheduler()
        self._metrics_manager = UIBusMetrics()

        self._by_id: Dict[str, UICommand] = {}
        self._by_id_lock = threading.Lock()

        self._history: deque[UICommand] = deque(maxlen=500)
        self._handlers: Dict[UICommandKind, UICommandHandler] = {}
        self._throttles: Dict[str, Callable[[], Optional[float]]] = {}
        self._throttle_factory: Optional[
            Callable[[str], Callable[[], Optional[float]]]
        ] = None

        self._ws_broadcast: Optional[Callable[[Dict[str, Any]], None]] = None
        self._command_sink: Optional[Callable[[UICommand], None]] = None

        self._stop_flag = threading.Event()
        self._worker: Optional[threading.Thread] = None

    def acquire_session_lock(self, wxid: str, session_name: str):
        self._scheduler.acquire_session_lock(wxid, session_name)

    def release_session_lock(self, wxid: str, session_name: str):
        self._scheduler.release_session_lock(wxid, session_name)

    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop_flag.clear()
        self._worker = threading.Thread(
            target=self._run, name="ui-bus-worker", daemon=True,
        )
        self._worker.start()
        logger.info("[UIBus] worker 已启动")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_flag.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=timeout)
        logger.info("[UIBus] worker 已停止")

    def register_handler(self, kind: UICommandKind, handler: UICommandHandler) -> None:
        self._handlers[kind] = handler
        logger.info(f"[UIBus] handler 注册: {kind.value}")

    def set_throttle(self, wxid: str, throttle: Callable[[], Optional[float]]) -> None:
        self._throttles[wxid] = throttle

    def set_default_throttle_factory(
        self,
        factory: Callable[[str], Callable[[], Optional[float]]],
    ) -> None:
        self._throttle_factory = factory

    def set_ws_broadcaster(self, broadcaster: Callable[[Dict[str, Any]], None]) -> None:
        self._ws_broadcast = broadcaster

    def set_command_sink(self, sink: Callable[[UICommand], None]) -> None:
        self._command_sink = sink
        logger.info("[UIBus] 命令终态 sink 已注入")

    def submit(self, command: UICommand) -> str:
        self._scheduler.insert_queue(command)
        with self._by_id_lock:
            self._by_id[command.id] = command

        self._metrics_manager.metrics["submitted"] += 1
        self._emit("ui_bus:submitted", command)
        logger.debug(
            f"[UIBus] 入队 wxid={command.wxid} kind={command.kind.value} "
            f"prio={int(command.priority)} id={command.id}"
        )
        return command.id

    def get(self, command_id: str) -> Optional[UICommand]:
        with self._by_id_lock:
            return self._by_id.get(command_id)

    def cancel(self, command_id: str) -> bool:
        cmd = self.get(command_id)
        if not cmd or cmd.status != UICommandStatus.QUEUED:
            return False
        self._scheduler.remove_queue(cmd)
        cmd.status = UICommandStatus.CANCELED
        cmd.finished_ts = time.time()
        cmd._done_event.set()
        self._metrics_manager.metrics["canceled"] += 1
        self._emit("ui_bus:canceled", cmd)
        self._sink_terminal(cmd)
        return True

    def await_result(self, command_id: str, timeout: float = 30.0) -> UICommand:
        cmd = self.get(command_id)
        if not cmd:
            raise KeyError(f"command {command_id} not found")
        cmd._done_event.wait(timeout=timeout)
        return cmd

    def snapshot(self) -> Dict[str, Any]:
        now = time.time()
        accounts = []
        with self._scheduler.queues_lock:
            for wxid, q in self._scheduler.queues.items():
                head_wait = (now - q[0].submit_ts) if q else 0.0
                head_score = None
                if q:
                    head_score = round(
                        int(q[0].priority) * self._scheduler.PRIO_W + max(0.0, head_wait),
                        2,
                    )
                accounts.append({
                    "wxid": wxid,
                    "queued": len(q),
                    "next": q[0].to_dict() if q else None,
                    "head_wait_seconds": round(max(0.0, head_wait), 2) if q else 0,
                    "head_score": head_score,
                })
        with self._by_id_lock:
            history = [c.to_dict() for c in list(self._history)[-50:]]

        return {
            "metrics": dict(self._metrics_manager.metrics),
            "accounts": accounts,
            "handlers": [k.value for k in self._handlers.keys()],
            "recent": history,
            "minute_series": self._metrics_manager.get_minute_series(30),
        }

    def _run(self) -> None:
        try:
            import comtypes
            comtypes.CoInitialize()
        except Exception as e:
            logger.debug(f"[UIBus] COM 初始化失败/已在其他套间中: {e}")

        from src.utils.uia_lock import uia_lock, UIATaskPriority

        while not self._stop_flag.is_set():
            cmd = self._scheduler.pick_next()
            if cmd is None:
                time.sleep(0.1)
                continue

            throttle = self._throttles.get(cmd.wxid)
            if throttle is None and self._throttle_factory is not None:
                try:
                    throttle = self._throttle_factory(cmd.wxid)
                    if throttle:
                        self._throttles[cmd.wxid] = throttle
                except Exception as e:
                    logger.warning(f"[UIBus] 默认节流器工厂异常 wxid={cmd.wxid}: {e}")
                    throttle = None
            if throttle:
                try:
                    delay = throttle(cmd)
                except TypeError:
                    try:
                        delay = throttle()
                    except Exception as e:
                        logger.warning(f"[UIBus] 节流器异常 wxid={cmd.wxid}: {e}")
                        delay = None
                except Exception as e:
                    logger.warning(f"[UIBus] 节流器异常 wxid={cmd.wxid}: {e}")
                    delay = None
                if delay and delay > 0:
                    with self._scheduler.queues_lock:
                        self._scheduler.queues[cmd.wxid].append(cmd)
                    time.sleep(min(delay, 5.0))
                    continue

            handler = self._handlers.get(cmd.kind)
            if handler is None:
                cmd.status = UICommandStatus.FAILED
                cmd.error = f"no handler for kind={cmd.kind.value}"
                cmd.finished_ts = time.time()
                cmd._done_event.set()
                self._metrics_manager.metrics["failed"] += 1
                with self._by_id_lock:
                    self._history.append(cmd)
                self._emit("ui_bus:failed", cmd)
                self._sink_terminal(cmd)
                continue

            cmd.status = UICommandStatus.RUNNING
            cmd.started_ts = time.time()
            self._metrics_manager.metrics["last_active_wxid"] = cmd.wxid
            self._metrics_manager.metrics["current_running_cmd"] = cmd.id
            self._emit("ui_bus:started", cmd)

            try:
                prio_map = {
                    UICommandPriority.LOW: UIATaskPriority.LOW,
                    UICommandPriority.NORMAL: UIATaskPriority.NORMAL,
                    UICommandPriority.HIGH: UIATaskPriority.HIGH,
                    UICommandPriority.URGENT: UIATaskPriority.HIGH,
                }
                ui_prio = prio_map.get(cmd.priority, UIATaskPriority.NORMAL)
                task_label = f"{cmd.kind.value}@{cmd.wxid}"
                
                kind_display = {
                    UICommandKind.SEND_MESSAGE: "发送消息",
                    UICommandKind.SEND_FILE: "发送文件/图片",
                    UICommandKind.SEND_VOICE: "发送收藏语音",
                    UICommandKind.PUBLISH_MOMENT: "发布朋友圈",
                    UICommandKind.MOMENT_INTERACT: "朋友圈互动",
                    UICommandKind.ADD_FRIEND: "添加好友",
                    UICommandKind.ACCEPT_FRIEND: "接受好友申请",
                    UICommandKind.SYNC_TAGS: "同步标签",
                    UICommandKind.FETCH_AVATAR: "获取头像",
                    UICommandKind.EXTRACT_USER_INFO: "提取用户信息",
                    UICommandKind.ENABLE_VOICE_TO_TEXT: "开启语音转文字",
                }.get(cmd.kind, cmd.kind.value)
                
                lock_msg = f"正在执行: {kind_display}"
                from src.uia.input_guard import uia_lock as physical_lock
                
                with uia_lock.sync_acquire(priority=ui_prio,
                                           task_name=task_label,
                                           timeout=cmd.timeout):
                    with physical_lock(lock_msg):
                        try:
                            target_hwnd = None
                            try:
                                from app import state
                                if hasattr(state, 'account_manager') and state.account_manager:
                                    for h, inst in getattr(state.account_manager, '_instances', {}).items():
                                        if inst.wxid == cmd.wxid:
                                            target_hwnd = h
                                            break
                            except Exception:
                                pass
                                
                            if not target_hwnd:
                                try:
                                    from app import state
                                    if hasattr(state, 'driver') and state.driver:
                                        target_hwnd = getattr(state.driver, 'hwnd', None)
                                except Exception:
                                    pass
                                    
                            if target_hwnd:
                                from src.uia.retry import ensure_wechat_foreground
                                logger.info(f"[UIBus] 正在强制置顶目标微信窗口 hwnd={target_hwnd} (wxid={cmd.wxid})")
                                ensure_wechat_foreground(target_hwnd)
                        except Exception as focus_ex:
                            logger.warning(f"[UIBus] 前置窗口激活置顶失败: {focus_ex}")

                        res = handler(cmd)
                        cmd.result = res
                        if res is False:
                            raise RuntimeError("物理驱动操作执行返回失败")
                        if isinstance(res, dict) and res.get("success") is False:
                            raise RuntimeError(res.get("error") or "指令物理执行返回失败")
                cmd.status = UICommandStatus.SUCCESS
                self._metrics_manager.metrics["succeeded"] += 1
                self._emit("ui_bus:finished", cmd)
            except TimeoutError as e:
                cmd.status = UICommandStatus.TIMEOUT
                cmd.error = str(e)
                self._metrics_manager.metrics["timeout"] += 1
                self._emit("ui_bus:timeout", cmd)
                logger.warning(f"[UIBus] 超时: {cmd.id} {cmd.kind.value} {e}")
            except Exception as e:
                cmd.status = UICommandStatus.FAILED
                cmd.error = f"{type(e).__name__}: {e}"
                self._metrics_manager.metrics["failed"] += 1
                self._emit("ui_bus:failed", cmd)
                if "物理驱动操作" in str(e) or "避让" in str(e) or "冲突" in str(e):
                    logger.warning(f"[UIBus] 执行被安全避让或物理驱动受阻: {cmd.id} - {e}")
                else:
                    logger.exception(f"[UIBus] 执行失败: {cmd.id}")
            finally:
                cmd.finished_ts = time.time()
                cmd._done_event.set()
                self._metrics_manager.metrics["current_running_cmd"] = None
                with self._by_id_lock:
                    self._history.append(cmd)
                self._sink_terminal(cmd)
                try:
                    from src.utils.stop_signal import stop_signal
                    stop_signal.reset()
                except Exception:
                    pass
                try:
                    import gc
                    gc.collect()
                except Exception:
                    pass

    def _emit(self, event: str, cmd: UICommand) -> None:
        if self._ws_broadcast is None:
            return
        try:
            self._ws_broadcast({
                "type": event,
                "data": cmd.to_dict(),
            })
        except Exception as e:
            logger.debug(f"[UIBus] 事件广播失败: {e}")

    def _sink_terminal(self, cmd: UICommand) -> None:
        self._metrics_manager.record_minute_bucket(cmd)
        sink = self._command_sink
        if sink is None:
            return
        try:
            sink(cmd)
        except Exception as e:
            logger.debug(f"[UIBus] 终态 sink 异常: {e}")

ui_bus = UIBus()
