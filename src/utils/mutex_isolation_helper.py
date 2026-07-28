import os
import logging
from typing import Tuple, Optional

logger = logging.getLogger(__name__)

def ensure_data_isolation(instance_index: int, base_dir: str = r"D:\WeChatData") -> str:
    """为指定实例创建独立的数据目录

    目录结构：
        <base_dir>/instance_<N>/
            ├── WeChatFiles/         # 微信聊天/文件保存目录（注册表 FileSavePath）
            ├── AppData/Roaming/     # 重定向 %APPDATA%
            ├── AppData/Local/       # 重定向 %LOCALAPPDATA%
            ├── UserProfile/         # 重定向 %USERPROFILE%
            └── UserProfile/Documents

    Args:
        instance_index: 实例编号（从 1 开始）
        base_dir: 基础目录

    Returns:
        实例专属数据目录路径
    """
    instance_dir = os.path.join(base_dir, f"instance_{instance_index}")
    sub_dirs = [
        "WeChatFiles",
        os.path.join("AppData", "Roaming"),
        os.path.join("AppData", "Local"),
        os.path.join("UserProfile"),
        os.path.join("UserProfile", "Documents"),
    ]
    for sub in sub_dirs:
        os.makedirs(os.path.join(instance_dir, sub), exist_ok=True)
    return instance_dir


def build_isolated_env(instance_index: int,
                       base_dir: str = r"D:\WeChatData") -> Tuple[dict, str]:
    """为指定实例构建隔离的环境变量字典。

    通过覆盖 APPDATA / LOCALAPPDATA / USERPROFILE 三大目录变量，让微信子进程
    把账号缓存（MMSetting / accInfo / 登录令牌等）写到独立目录；再配合
    apply_filesave_path 切换注册表，可把聊天记录也隔离开。

    Returns:
        (env_dict, instance_dir)
    """
    instance_dir = ensure_data_isolation(instance_index, base_dir)
    roaming = os.path.join(instance_dir, "AppData", "Roaming")
    local = os.path.join(instance_dir, "AppData", "Local")
    user_profile = os.path.join(instance_dir, "UserProfile")

    env = os.environ.copy()
    env["APPDATA"] = roaming
    env["LOCALAPPDATA"] = local
    env["USERPROFILE"] = user_profile
    env["HOMEPATH"] = user_profile.split(":", 1)[1] if ":" in user_profile else user_profile
    env["XM_WECHAT_INSTANCE"] = str(instance_index)
    env["XM_WECHAT_DATA_DIR"] = instance_dir
    return env, instance_dir


def apply_filesave_path(instance_dir: str, process_name: str) -> Optional[str]:
    """在启动微信前，把注册表 FileSavePath 切到当前实例的独立目录。

    微信进程读取 FileSavePath 只在启动那一刻完成，后续修改不影响已运行进程，
    因此在串行多开流程中可以每启动一个就切一次。

    Args:
        instance_dir: 实例根目录
        process_name: "wechat.exe" / "weixin.exe"（决定写哪个注册表键）

    Returns:
        启动前该注册表项的原值（调用方负责在全部启动结束后恢复），
        读取失败返回 None。
    """
    try:
        import winreg
    except ImportError:
        return None

    file_save_dir = os.path.join(instance_dir, "WeChatFiles")
    os.makedirs(file_save_dir, exist_ok=True)

    if process_name.lower() == "weixin.exe":
        key_path = r"Software\Tencent\Weixin"
    else:
        key_path = r"Software\Tencent\WeChat"

    original_value: Optional[str] = None
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, key_path, 0,
            winreg.KEY_READ | winreg.KEY_SET_VALUE,
        ) as key:
            try:
                original_value, _ = winreg.QueryValueEx(key, "FileSavePath")
            except FileNotFoundError:
                original_value = None
            winreg.SetValueEx(
                key, "FileSavePath", 0, winreg.REG_SZ, file_save_dir,
            )
        logger.info(f"[多开] 切换 FileSavePath → {file_save_dir}")
    except OSError as e:
        logger.warning(f"[多开] 写注册表 {key_path}\\FileSavePath 失败: {e}")
    return original_value


def restore_filesave_path(process_name: str, original_value: Optional[str]) -> None:
    """恢复 FileSavePath 注册表原值（多开流程结束后调用）。"""
    if original_value is None:
        return
    try:
        import winreg
    except ImportError:
        return

    if process_name.lower() == "weixin.exe":
        key_path = r"Software\Tencent\Weixin"
    else:
        key_path = r"Software\Tencent\WeChat"

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(
                key, "FileSavePath", 0, winreg.REG_SZ, original_value,
            )
        logger.info(f"[多开] 恢复 FileSavePath → {original_value}")
    except OSError as e:
        logger.warning(f"[多开] 恢复 {key_path}\\FileSavePath 失败: {e}")
