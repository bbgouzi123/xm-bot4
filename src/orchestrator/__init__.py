"""UI 命令总线（阶段 0：多账号调度的地基）

统一收口所有对微信窗口（UIA / OCR / Win32）的物理操作：
- 以"账号 wxid"为粒度的 FIFO 命令队列
- 全局唯一 worker 线程调度，天然避免多线程抢前台焦点
- 优先级抢占 + 加权轮询 + 节流冷却
- 生命周期事件流推送前端（WebSocket）

使用姿势：
    from src.orchestrator.ui_bus import ui_bus, UICommand, UICommandKind, UICommandPriority

    cmd_id = ui_bus.submit(UICommand(
        wxid="wxid_xxx",
        kind=UICommandKind.SEND_MESSAGE,
        payload={"target": "张三", "text": "你好"},
        priority=UICommandPriority.NORMAL,
    ))

    result = ui_bus.await_result(cmd_id, timeout=30)
"""
from src.orchestrator.ui_bus import (  # noqa: F401
    ui_bus,
    UIBus,
    UICommand,
    UICommandKind,
    UICommandPriority,
    UICommandStatus,
)
from src.orchestrator.account_profile import (  # noqa: F401
    AccountTempo,
    make_account_throttle,
    configure_tempo,
    get_tempo_snapshot,
)
