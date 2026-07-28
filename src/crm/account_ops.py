import os
import json
import shutil
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

def list_accounts() -> List[Dict[str, Any]]:
    from src.crm.account_data import ACCOUNTS_DIR, _active_wxid, _load_account_meta
    results = []
    if not os.path.exists(ACCOUNTS_DIR):
        return results

    for name in sorted(os.listdir(ACCOUNTS_DIR)):
        account_dir = os.path.join(ACCOUNTS_DIR, name)
        if not os.path.isdir(account_dir):
            continue

        meta = _load_account_meta(name)

        profiles_dir = os.path.join(account_dir, "profiles")
        profiles_count = 0
        if os.path.isdir(profiles_dir):
            profiles_count = len([
                f for f in os.listdir(profiles_dir)
                if f.endswith(".json")
            ])

        config_path = os.path.join(account_dir, "config.json")
        has_industry = False
        try:
            from src.utils.config_cache import config_cache
            cache_key = f"industry_config_{name}"
            cfg = config_cache.get(cache_key)
            
            if not cfg:
                # 尝试读取本地配置兜底
                if os.path.exists(config_path):
                    with open(config_path, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
            
            if cfg and isinstance(cfg, dict):
                has_industry = bool(cfg.get("profiles"))
        except Exception:
            pass

        results.append({
            "wxid": meta.get("wxid", name),
            "nickname": meta.get("nickname", name),
            "dir_name": name,
            "profiles_count": profiles_count,
            "has_config": has_industry,
            "is_active": name == _active_wxid,
        })

    return results


def copy_config_from(source_wxid: str, target_wxid: str = None) -> bool:
    from src.crm.account_data import get_active_account, _safe_dirname, get_config_path, _load_account_meta
    target = _safe_dirname(target_wxid) if target_wxid else get_active_account()
    source = _safe_dirname(source_wxid)

    if source == target:
        logger.warning("[多账号] 源和目标相同，跳过复制")
        return False

    try:
        from src.utils.config_cache import config_cache
        import uuid

        source_cache_key = f"industry_config_{source}"
        source_data = config_cache.get(source_cache_key)
        
        if not source_data:
            # 尝试读取本地兜底配置
            src_cfg_path = get_config_path(source)
            if os.path.exists(src_cfg_path):
                with open(src_cfg_path, "r", encoding="utf-8") as f:
                    source_data = json.load(f)

        if not source_data or not isinstance(source_data, dict):
            logger.warning(f"[多账号] 源账号 {source} 无行业配置可复制")
            return False

        # 为每个 profile 生成新 ID
        new_profiles = []
        for p in source_data.get("profiles", []):
            new_p = p.copy()
            new_p["id"] = f"profile_{uuid.uuid4().hex[:8]}"
            new_profiles.append(new_p)

        target_data = {
            "active_profile_id": new_profiles[0]["id"] if new_profiles else "",
            "profiles": new_profiles,
        }

        target_cache_key = f"industry_config_{target}"
        config_cache.set(target_cache_key, target_data)
        
        # 本地同步一份
        try:
            target_cfg_path = get_config_path(target)
            with open(target_cfg_path, "w", encoding="utf-8") as f:
                json.dump(target_data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        source_meta = _load_account_meta(source)
        source_name = source_meta.get("nickname", source)
        logger.info(f"[多账号] 配置已复制: {source_name} → {target}")
        print(f"[多账号] 行业配置已从 {source_name} 复制到 {target}")
        return True

    except Exception as e:
        logger.error(f"[多账号] 复制配置失败: {e}")
        return False


def migrate_legacy_data():
    from src.crm.account_data import ACCOUNTS_DIR, APP_DATA_DIR
    migrated_flag = os.path.join(ACCOUNTS_DIR, ".migrated_v2")
    if os.path.exists(migrated_flag):
        return

    default_dir = os.path.join(ACCOUNTS_DIR, "default")
    os.makedirs(os.path.join(default_dir, "profiles"), exist_ok=True)
    migrated_count = 0

    # --- 迁移位置1: 程序目录下的 data/profiles/ ---
    try:
        prog_base = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        old_profiles = os.path.join(prog_base, "data", "profiles")
        if os.path.isdir(old_profiles):
            for f in os.listdir(old_profiles):
                if f.endswith(".json"):
                    src = os.path.join(old_profiles, f)
                    dst = os.path.join(default_dir, "profiles", f)
                    if not os.path.exists(dst):
                        shutil.copy2(src, dst)
                        migrated_count += 1
    except Exception as e:
        logger.error(f"[迁移] profiles 迁移失败: {e}")

    # --- 迁移位置1b: 旧版 accounts 目录（如果之前已在程序目录创建过） ---
    try:
        prog_base = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        old_accounts = os.path.join(prog_base, "data", "accounts")
        if os.path.isdir(old_accounts):
            for acct_name in os.listdir(old_accounts):
                src_dir = os.path.join(old_accounts, acct_name)
                if not os.path.isdir(src_dir):
                    continue
                dst_dir = os.path.join(ACCOUNTS_DIR, acct_name)
                if not os.path.exists(dst_dir):
                    shutil.copytree(src_dir, dst_dir)
                    migrated_count += 1
    except Exception as e:
        logger.error(f"[迁移] 旧 accounts 目录迁移失败: {e}")

    # --- 迁移位置2: 程序目录下的 config.json ---
    try:
        prog_base = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        old_config = os.path.join(prog_base, "config.json")
        if os.path.exists(old_config):
            with open(old_config, "r", encoding="utf-8") as fh:
                old_cfg = json.load(fh)
            if "industry" in old_cfg:
                new_config = os.path.join(default_dir, "config.json")
                new_cfg = {}
                if os.path.exists(new_config):
                    with open(new_config, "r", encoding="utf-8") as fh:
                        new_cfg = json.load(fh)
                new_cfg["industry"] = old_cfg["industry"]
                with open(new_config, "w", encoding="utf-8") as fh:
                    json.dump(new_cfg, fh, ensure_ascii=False, indent=2)
                migrated_count += 1
    except Exception as e:
        logger.error(f"[迁移] 行业配置迁移失败: {e}")

    # --- 迁移位置3: ~/.xm-ai-bot/contacts.json ---
    try:
        old_contacts = os.path.join(APP_DATA_DIR, "contacts.json")
        if os.path.exists(old_contacts):
            dst = os.path.join(default_dir, "contacts.json")
            if not os.path.exists(dst):
                shutil.copy2(old_contacts, dst)
                migrated_count += 1
    except Exception as e:
        logger.error(f"[迁移] contacts 迁移失败: {e}")

    if migrated_count > 0:
        print(f"[多账号] 旧数据已迁移到 ~/.xm-ai-bot/accounts/default/ ({migrated_count} 项)")

    # 写迁移标记
    os.makedirs(ACCOUNTS_DIR, exist_ok=True)
    with open(migrated_flag, "w") as fh:
        fh.write("done_v2")
