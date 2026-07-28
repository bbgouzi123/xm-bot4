import os
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("WeChatKeyStore")

APP_DATA_DIR = str(Path.home() / ".xm-ai-bot")
KEYS_FILE_PATH = os.path.join(APP_DATA_DIR, "wechat_keys.json")

import re

def clean_wxid(wxid: str) -> Optional[str]:
    if not wxid:
        return None
    # 过滤掉多开容器添加的随机后缀，如 _afe3 (下划线加 4 位十六进制数)
    return re.sub(r'_[0-9a-fA-F]{4}$', '', wxid)

def _validate_and_sanitize_key(key: str, wxid: str = None) -> bool:
    """
    验证密钥是否能解密本地数据库。如果指定了 wxid 且本地存在该账号的数据库目录，
    则密钥必须能够解密该账号的数据库。如果不能，或者未指定 wxid 且密钥无法解密任何本地数据库，
    则判定该密钥已失效或非本账户/机器密钥，并执行清理。
    """
    if not key or len(key) != 64:
        return False
    try:
        from src.wechat_4x.db_match_helper import get_wechat_base_dirs, match_db_storage_by_key
        base_dirs = get_wechat_base_dirs()
        
        target_wxid_clean = clean_wxid(wxid) if wxid else None
        target_wxid_md5 = None
        if target_wxid_clean:
            import hashlib
            target_wxid_md5 = hashlib.md5(target_wxid_clean.encode('utf-8')).hexdigest()
            
        all_db_storage_dirs = []
        has_target_dir = False
        
        for base_dir in base_dirs:
            if os.path.isdir(base_dir):
                for entry in os.listdir(base_dir):
                    if entry.lower() in {"all users", "all_users", "backup", "finderlive", "common", "global", "temp", "cache"}:
                        continue
                    
                    entry_clean = clean_wxid(entry)
                    db_storage = os.path.join(base_dir, entry, "db_storage")
                    if os.path.isdir(db_storage):
                        if target_wxid_clean:
                            # 优先只验证与该微信号对应的目录（支持原始ID和MD5哈希目录）
                            if entry_clean == target_wxid_clean or (target_wxid_md5 and entry_clean == target_wxid_md5):
                                all_db_storage_dirs.append(db_storage)
                                has_target_dir = True
                        else:
                            all_db_storage_dirs.append(db_storage)
                            
        # 如果指定了微信号，但没有找到任何对应的本地目录，则退而求其次验证所有目录，或者直接放行
        if target_wxid_clean and not has_target_dir:
            # 这种情况可能是新装微信，或者微信号对应的目录尚未生成，我们放行
            return True
            
        if all_db_storage_dirs:
            matched = match_db_storage_by_key(key, all_db_storage_dirs)
            if not matched:
                logger.warning(f"[KeyStore] ⚠️ 密钥 {key[:6]}****** 验证失败（无法解密{'指定账号(' + wxid + ')' if wxid else '任何'}本地数据库），执行自动清理。")
                clear_persisted_wechat_key(wxid)
                return False
        return True
    except Exception as e:
        logger.debug(f"[KeyStore] 校验密钥有效性异常: {e}")
    return True

def verify_wechat_key(key: str, wxid: str = None) -> bool:
    """用密钥去尝试解密微信本地数据库，校验密钥是否有效且确实归属于该账号"""
    return _validate_and_sanitize_key(key, wxid)


def get_persisted_wechat_key(wxid: str = None) -> Optional[str]:
    """
    从本地用户数据目录中无感获取微信数据库密钥。
    1. 优先根据 wxid 获取对应账号的密钥
    2. 如果 wxid 未指定或未匹配，则返回 last_key 作为通用兜底
    """
    wxid = clean_wxid(wxid)
    # 1. 如果指定了 wxid，必须且仅能从 wechat_keys.json 中根据 wxid 读取密钥，隔离跨账号污染
    if wxid and wxid != "default":
        if os.path.exists(KEYS_FILE_PATH):
            try:
                with open(KEYS_FILE_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and wxid in data:
                    key = data[wxid]
                    if key and len(key) == 64:
                        if not _validate_and_sanitize_key(key, wxid):
                            # 验证失败已被 clear_persisted_wechat_key 从 JSON 中擦除
                            return None
                        
                        logger.info(f"[KeyStore] 成功恢复指定账号({wxid})的本地持久化密钥")
                        # 仅在此账号的当前调用中写入临时环境变量，不做全局污染覆盖
                        os.environ["WECHAT_4X_KEY_HEX"] = key
                        os.environ["WCDB_HEX_KEY"] = key
                        return key
            except Exception as e:
                logger.debug(f"[KeyStore] 读取 wechat_keys.json 异常: {e}")
        
        # 找不到指定微信账号的密钥时，直接返回 None，等待登录时动态捕获，严禁拿其它账号的密钥兜底
        return None

    # 2. 如果没有指定 wxid，或者配置文件中找不到该 wxid，再尝试从当前进程环境变量中获取密钥
    env_key = os.environ.get("WECHAT_4X_KEY_HEX") or os.environ.get("WCDB_HEX_KEY")
    if env_key and len(env_key) == 64:
        if not _validate_and_sanitize_key(env_key, None):
            os.environ.pop("WECHAT_4X_KEY_HEX", None)
            os.environ.pop("WCDB_HEX_KEY", None)
            return None
        return env_key

    # 3. 尝试从 wechat_keys.json 中获取 last_key 兜底
    if os.path.exists(KEYS_FILE_PATH):
        try:
            with open(KEYS_FILE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                last_key = data.get("last_key")
                if last_key and len(last_key) == 64:
                    if not _validate_and_sanitize_key(last_key, None):
                        return None
                    logger.info("[KeyStore] 成功恢复全局最新(last_key)的本地持久化密钥")
                    os.environ["WECHAT_4X_KEY_HEX"] = last_key
                    os.environ["WCDB_HEX_KEY"] = last_key
                    return last_key
        except Exception as e:
            logger.debug(f"[KeyStore] 读取 wechat_keys.json last_key 异常: {e}")

    # 4. 开发环境从 .env 读兜底
    try:
        backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        for env_path in [os.path.join(backend_dir, '.env'), os.path.join(backend_dir, '..', '.env')]:
            if os.path.exists(env_path):
                with open(env_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.strip().startswith("WECHAT_4X_KEY_HEX="):
                            val = line.split("=", 1)[1].strip()
                            if len(val) == 64:
                                if not _validate_and_sanitize_key(val, None):
                                    return None
                                logger.info("[KeyStore] 成功从 .env 中读取密钥")
                                os.environ["WECHAT_4X_KEY_HEX"] = val
                                os.environ["WCDB_HEX_KEY"] = val
                                return val
    except Exception:
        pass

    return None

def persist_wechat_key(key: str, wxid: str = None):
    """
    持久化保存微信数据库密钥到本地数据目录与环境变量中。
    """
    wxid = clean_wxid(wxid)
    if not key or len(key) != 64:
        return

    # 写入当前进程环境变量
    os.environ["WECHAT_4X_KEY_HEX"] = key
    os.environ["WCDB_HEX_KEY"] = key
    os.environ["WECHAT_4X_KEY_HEX_DYNAMIC"] = "1"

    # 写入持久化 JSON
    os.makedirs(APP_DATA_DIR, exist_ok=True)
    data = {}
    if os.path.exists(KEYS_FILE_PATH):
        try:
            with open(KEYS_FILE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass

    data["last_key"] = key
    if wxid:
        data[wxid] = key

    try:
        with open(KEYS_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"[KeyStore] 密钥已持久化保存至: {KEYS_FILE_PATH}")
    except Exception as e:
        logger.error(f"[KeyStore] 写入 wechat_keys.json 失败: {e}")


def clear_persisted_wechat_key(wxid: str = None):
    """
    当密钥失效、被证实错误时，清除持久化保存的特定微信密钥，
    以阻止下一次无感热启动错误重用它，强制走强退重启流程拦截正确的密钥。
    """
    wxid = clean_wxid(wxid)
    # 1. 从当前进程环境变量中清除
    for env_name in ["WECHAT_4X_KEY_HEX", "WCDB_HEX_KEY", "WECHAT_4X_KEY_HEX_DYNAMIC"]:
        if env_name in os.environ:
            del os.environ[env_name]

    # 2. 从持久化 JSON 中清除
    if os.path.exists(KEYS_FILE_PATH):
        try:
            with open(KEYS_FILE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                changed = False
                if wxid and wxid in data:
                    del data[wxid]
                    changed = True
                    logger.info(f"[KeyStore] 已清除失效的本地指定账号({wxid})密钥")
                if "last_key" in data:
                    del data["last_key"]
                    changed = True
                
                if changed:
                    with open(KEYS_FILE_PATH, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[KeyStore] 清除 wechat_keys.json 失效密钥失败: {e}")

