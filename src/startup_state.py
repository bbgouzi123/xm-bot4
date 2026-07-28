"""
全局启动状态标志位（独立模块，避免 main.py 与 api 之间的循环导入）

用法：
    from src.startup_state import startup_state
    startup_state.ready = True   # 标记就绪
    if startup_state.ready: ...  # 检查就绪
"""


class _StartupState:
    def __init__(self):
        # 核心 HTTP 服务是否已启动（外壳跳转阈值）
        self.ready = False
        # 后台初始化是否全部完成（微信连接、同步服务等）
        self.init_complete = False
        # 当前初始化进度描述
        self.status = "准备启动..."


startup_state = _StartupState()
