import os
import sys
import time
from dotenv import load_dotenv

# 确保能正确导入 src 模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.wechat_4x.db_match_helper import auto_detect_db_path
from src.wechat_4x.db_unread_monitor import SessionDbFallbackMonitor


def on_message(msg):
    print("\n" + "=" * 50)
    print(f"[测试成功] 🔔 捕捉到未读消息变动事件!")
    print(f"  会话ID (wxid/群ID): {msg['session_id']}")
    print(f"  消息摘要 (Content): {msg['content']}")
    print(f"  是否是群聊 (is_group): {msg['is_group']}")
    print("=" * 50 + "\n")


def main():
    load_dotenv()

    # 优先使用获取到的密钥
    hex_key = (
        os.environ.get("WCDB_HEX_KEY")
        or os.environ.get("WECHAT_4X_KEY_HEX")
        or ""
    )
    print(f"[*] 准备使用的解密密钥: {hex_key}")

    # 1. 自动探测 session.db 路径
    db_path = auto_detect_db_path(hex_key)
    if not db_path:
        print("[!] 自动探测 session.db 路径失败。")
        print("    请确保当前微信已登录，或在 .env / 环境变量中配置了 WCDB_SESSION_DB_PATH。")
        return

    print(f"[+] 探测到微信 session.db 路径: {db_path}")

    # 2. 实例化并启动纯 Python 影子拷贝监测器
    monitor = SessionDbFallbackMonitor()
    print("[*] 正在启动 SessionDbFallbackMonitor 影子轮询监听器...")

    if monitor.start(db_path, hex_key, on_message):
        print("\n" + "*" * 60)
        print(" ✅ 影子监测器启动成功！已在后台开始静默轮询未读消息。")
        print(" 👉 测试步骤：")
        print("    1. 保持当前微信在后台登录运行（可以最小化）。")
        print("    2. 用另一个微信号给当前登录的微信发送一条消息（不要点开聊天窗口）。")
        print("    3. 观察本控制台，是否能实时输出「捕捉到未读消息变动事件」的打印。")
        print("    (按 Ctrl + C 可以随时终止测试)")
        print("*" * 60 + "\n")

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[*] 正在停止监听器并清理临时影子文件...")
            monitor.stop()
            print("[*] 测试已结束。")
    else:
        print("[!] 影子拷贝监听器启动失败。")


if __name__ == "__main__":
    main()
