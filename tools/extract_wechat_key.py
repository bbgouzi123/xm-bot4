import os
import sys

# 配置 Python 模块解析路径
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, base_dir)

from src.wechat_4x.wechat_hook_controller import WeChatHookController

def update_env(key: str):
    # 统一交由本地隔离的 wechat_keys.json 管理，防止多账号切换时在大仓公共配置文件 .env 中产生密钥冲突与覆盖
    from src.utils.wechat_key_store import persist_wechat_key
    try:
        persist_wechat_key(key)
        print("[*] 密钥已成功保存至本地隔离配置文件 wechat_keys.json 中。")
        print("[*] 增强型多微信密钥自动感知链路已就绪！")
    except Exception as e:
        print(f"[-] 保存至 wechat_keys.json 异常: {e}")

def main():
    print("=" * 60)
    print("      WeChat 数据库密钥自动化提取工具 (Native DLL 注入)")
    print("=" * 60)
    print("[提示] 该过程会自动关闭现有微信，并在注入后等待您重新登录。")
    print("=" * 60)
    
    controller = WeChatHookController()
    key = controller.auto_get_key()
    
    if key:
        print("\n[OK] 密钥提取大成功！")
        update_env(key)
    else:
        print("\n[-] 提取失败，请参考上方错误日志进行排查。")

if __name__ == "__main__":
    main()
