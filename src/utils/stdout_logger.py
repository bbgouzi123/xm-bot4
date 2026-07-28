import sys
import re
import asyncio
from src.utils.websocket_manager import ws_manager

# 噪音过滤：匹配以下模式的日志行会被丢弃，不推送到前端
_NOISE_PATTERNS = [
    re.compile(r'^\d+(\.\d+)?%'),                          # 纯进度条 "100%"
    re.compile(r'^\s*$'),                                   # 纯空行
    re.compile(r'^\d+$'),                                   # 纯数字行
    re.compile(r'\d+\.\d+\.\d+\.\d+.*?"(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)'), # HTTP 访问日志
    re.compile(r'^(GET|POST|PUT|DELETE|PATCH) /(.*?) HTTP'),# uvicorn 简短路由日志
    re.compile(r'^INFO:\s+\d+\.\d+\.\d+\.\d+'),            # uvicorn INFO: 127.0.0.1 - ...
]

def _is_noise(msg: str) -> bool:
    """判断是否为噪音行，噪音行不向前端推送"""
    for pattern in _NOISE_PATTERNS:
        if pattern.search(msg):
            return True
    return False

def _is_sensitive(msg: str) -> bool:
    """判断是否为跟微信数据库连接、解密、密钥等相关的敏感隐私日志"""
    lower = msg.lower()
    keywords = [
        'wcdb', 'sqlite', 'decrypt', 'sqlcipher', 'hex_key', 
        'db_unread', 'db_reader', 'db_contact', 'wechat_decrypt',
        'wechat_key_store', '微信数据库', '微信db', '微信密钥', 
        '微信key', '获取key', '读取密钥', '解密并同步', '解密成功',
        '解密失败', 'dat解密', 'dat图片', 'dat_decrypt', 'rc=', 'db_path',
        'cipher', 'dec_aes', 'python消息监听', '未读同步', 'wcdb协调器', 
        'wcdb监听', 'chatmonitor', 'wcdb双引擎', 'getlasterror', 'wcdb_init', 
        'wcdb_open_account', '自动密钥监控'
    ]
    return any(k in lower for k in keywords)

class WSStdoutWrapper:
    def __init__(self, original_stream):
        self.original_stream = original_stream
        # 追踪上一次 write 是否被敏感拦截，用于过滤其孤立的后续换行符
        self._last_blocked = False

    def write(self, message):
        # 🛡️ 安全分层策略：WCDB/密钥等敏感日志仅输出到本地终端（方便排查），
        # 不推送至前端自动化控制中心（WebSocket），防止底层破解技术细节暴露给用户。
        if _is_sensitive(message):
            self.original_stream.write(message)   # 终端可见，便于调试
            self._last_blocked = True
            return

        # 📎 精准过滤孤立换行符：
        # print("xxx") 实际是两次 write()：write("xxx") + write("\n")
        # 当 write("xxx") 因敏感被拦截后，紧接的 write("\n") 仍会到来，
        # 导致控制台出现空行。解决：只在上一条消息被拦截后，才跳过纯空白 write()。
        # 普通日志的换行符（_last_blocked=False 时）正常通过，不影响多行显示。
        if self._last_blocked and (not message or not message.strip()):
            self._last_blocked = False
            return
        self._last_blocked = False

        # 1. 始终输出到控制台，保持原样
        self.original_stream.write(message)

        # 2. 提取并推送有效的日志行给前端
        msg_str = message.strip()
        if not msg_str:
            return

        # 从标准 logging 格式中提取业务消息（保留 level 作为前缀展示，方便着色）
        actual_msg = msg_str
        log_level = ''
        for lvl_tag, lvl_label in [
            (' - INFO - ',    '[INFO]'),
            (' - WARNING - ', '[WARN]'),
            (' - ERROR - ',   '[ERROR]'),
            (' - DEBUG - ',   '[DEBUG]'),
            ('INFO:     ',    '[INFO]'),
            ('WARNING:  ',    '[WARN]'),
            ('ERROR:    ',    '[ERROR]'),
        ]:
            if lvl_tag in msg_str:
                parts = msg_str.split(lvl_tag, 1)
                actual_msg = parts[1].strip()
                log_level = lvl_label
                break

        # 丢弃噪音行（HTTP 访问记录、uvicorn 路由日志、进度条等）
        if _is_noise(actual_msg):
            return

        # 丢弃跟微信数据库、解密、密钥等敏感隐私的日志行，确保数据安全与合规
        if _is_sensitive(actual_msg):
            return

        # 所有非空、非噪音的行均推送到前端（不再硬性要求以 [ 开头）
        # 对超长行做截断，防止 WS 帧过大
        display_msg = f"{log_level} {actual_msg}" if log_level else actual_msg
        if len(display_msg) > 500:
            display_msg = display_msg[:497] + '...'

        try:
            loop = ws_manager.loop
            if not loop:
                try:
                    loop = asyncio.get_running_loop()
                except Exception:
                    pass
            if loop:
                payload = {"type": "sys_log", "data": display_msg}
                asyncio.run_coroutine_threadsafe(
                    ws_manager.broadcast(payload),
                    loop
                )
        except Exception:
            # 容错处理，防止广播异常影响正常输出
            pass

    def write_original(self, message):
        self.original_stream.write(message)

    def flush(self):
        self.original_stream.flush()

def setup_stdout_logging():
    # 包装 stdout 和 stderr（StreamHandler 默认输出到 stderr，因此两者都必须包装）
    sys.stdout = WSStdoutWrapper(sys.stdout)
    sys.stderr = WSStdoutWrapper(sys.stderr)
