"""
账号隔离私有配置存储管理器 (account_settings_store.py)
从 account_data.py 中解耦出来，以严格遵守单文件 300 行规范。
"""
import os
import json
import logging
import re
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

def _is_actual_user_mismatch(local_owner: str, current_uid: str) -> bool:
    """
    判断本地归属用户和当前登录用户是否确实存在“账号切换导致的真正不匹配”。
    
    只有在 local_owner 和 current_uid 都是合法的 SaaS 用户 UUID 字符串时，
    二者不同才算作“他人遗留脏配置”。
    如果当前登录用户 current_uid 是降级占位词（例如 "local-desktop-user" 或 "default" 等非 UUID），
    或者未处于完全登录就绪状态，则判定不成立，以最大程度地在启动/竞态期保护本地白名单等配置。
    """
    if not local_owner or not current_uid:
        return False
        
    # UUID 格式 (32位或36位，允许带或不带 -)
    uuid_pattern = re.compile(r'^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$')
    
    if uuid_pattern.match(local_owner) and uuid_pattern.match(current_uid):
        return local_owner != current_uid
        
    return False

def _save_local_settings_with_meta(path: str, data: dict, owner_uid: str, updated_at: str = None):
    """保存配置到本地磁盘，并注入归属所有者和更新时间元数据"""
    persisted_data = dict(data)
    persisted_data.pop("_meta", None)
    
    up_time = updated_at or (datetime.utcnow().isoformat() + "Z")
    persisted_data["_meta"] = {
        "owner_uid": owner_uid,
        "updated_at": up_time
    }
    import random, time
    tmp_path = f"{path}.{random.randint(1000, 9999)}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(persisted_data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        for i in range(10):
            try:
                os.replace(tmp_path, path)
                break
            except OSError as pe:
                if i == 9:
                    raise pe
                time.sleep(0.05 * (2 ** i))
    except Exception as e:
        logger.warning(f"物理写入本地私有设置失败: {e}")
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass

def get_account_settings(wxid: str = None, force_reload: bool = False) -> dict:
    """获取当前账号的私有设置 (AI 引擎与自动化策略)"""
    from src.crm.account_data import get_active_account, get_account_data_dir
    target_wxid = wxid or get_active_account()
    from src.utils.config_cache import config_cache
    
    cache_key = f"account_settings_{target_wxid}"
    if force_reload:
        config_cache.delete(cache_key)
        logger.info(f"[多账号] 强制清除内存缓存并从本地磁盘/云端重载配置: {target_wxid}")

    # 1. 尝试从本地加载最新物理配置作为真相源之一
    local_data = None
    path = os.path.join(get_account_data_dir(target_wxid), "settings.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                local_data = json.load(f)
        except PermissionError as e_perm:
            # 文件被其他进程短暂锁定（如杀软扫描），等待 50ms 后重试一次
            import time as _t
            _t.sleep(0.05)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    local_data = json.load(f)
            except Exception as e_retry:
                logger.warning(f"读取本地账号私有设置失败（重试后仍失败，将使用默认配置）: {e_retry}")
        except Exception as e:
            logger.warning(f"读取本地账号私有设置失败: {e}")

    # 纠偏：若本地被嵌套包裹，解包自愈
    if isinstance(local_data, dict) and "settings" in local_data and isinstance(local_data["settings"], dict):
        logger.info("[纠偏] 发现本地 settings.json 被嵌套，执行解包纠偏")
        local_data = local_data["settings"]

    # 2. 从内存/同步后端拉取缓存数据
    cached = config_cache.get(cache_key)

    # 纠偏：若缓存被嵌套包裹，解包自愈
    if isinstance(cached, dict) and "settings" in cached and isinstance(cached["settings"], dict):
        logger.info("[纠偏] 发现内存 cached 包含 settings 嵌套，执行解包纠偏")
        cached = cached["settings"]
    
    # 默认基本配置模板
    default_config = {
        "ai": {
            "provider": "coze",
            "api_key": "",
            "bot_id": "",
            "system_prompt": ""
        },
        "reply": {
            "auto_accept_friend": False,
            "welcome_msg": "您好，很高兴认识您！",
            "snooze_rate": 0,
            "auto_follow": False
        }
    }

    # 提取本地的 owner_uid 和更新时间
    local_meta = local_data.get("_meta", {}) if isinstance(local_data, dict) else {}
    local_owner = local_meta.get("owner_uid")
    local_updated_str = local_meta.get("updated_at")

    # 提取当前登录平台用户 ID
    from src.utils.cloud_sync import get_cloud_client
    cloud_client = get_cloud_client()
    current_uid = cloud_client._try_load_sso_user_id() or "local-desktop-user"

    # 提取云端更新时间
    cloud_updated_str = config_cache.get_updated_at(cache_key)

    # 清除 local_data 中的 _meta 属性，以便返回给前端和核心引擎最纯净的配置字段
    pure_local_data = dict(local_data) if isinstance(local_data, dict) else {}
    pure_local_data.pop("_meta", None)

    # 3. 对齐本地与内存/云端数据
    if cached and isinstance(cached, dict):
        pure_cached = dict(cached)
        pure_cached.pop("_meta", None)

        # 场景一：如果本地配置归属用户与当前登录用户不符（他人残留脏文件），必须强制以新用户云端配置为准
        if _is_actual_user_mismatch(local_owner, current_uid):
            logger.info(f"[多账号] 检测到本地配置归属用户[{local_owner}]与当前登录用户[{current_uid}]不符，已丢弃本地残留，以云端配置覆盖本地。")
            _save_local_settings_with_meta(path, pure_cached, current_uid, cloud_updated_str)
            return pure_cached

        # 场景二：同用户离线比对。若本地时间戳新于云端，说明本地在离线/断网期间发生过修改，应当信任本地并上报
        is_local_newer = False
        if local_updated_str and cloud_updated_str:
            try:
                def parse_time(ts_str):
                    ts_str = str(ts_str).strip().replace(" ", "T").replace("Z", "").split("+")[0]
                    if "." in ts_str:
                        main_part, micro_part = ts_str.split(".", 1)
                        micro_part = (micro_part + "000000")[:6]
                        ts_str = f"{main_part}.{micro_part}"
                        return datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S.%f")
                    return datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S")

                local_dt = parse_time(local_updated_str)
                cloud_dt = parse_time(cloud_updated_str)
                if local_dt > cloud_dt:
                    is_local_newer = True
            except Exception as te:
                logger.debug(f"比对本地与云端更新时间戳失败: {te}")

        if is_local_newer:
            logger.info(f"[多账号] 本地修改时间[{local_updated_str}]新于云端时间[{cloud_updated_str}]，判定为离线未同步配置，自动推送上报云端。")
            config_cache.set(cache_key, pure_local_data, sync_cloud=True)
            return pure_local_data
        else:
            # 云端较新或一致，且归属人无误：用云端覆盖落盘本地
            # 🌟 增量安全策略：我们在用云端覆盖本地 settings 前，
            # 必须把本地已有的白名单与云端拉取的名单做“并集去重合并”，
            # 决不能因为版本覆盖导致用户之前用 F9 或前端刚保存的名单瞬间蒸发。
            import copy
            has_merged_any = False
            if isinstance(pure_local_data, dict) and "reply" in pure_local_data:
                # 🛡️ 架构防线：如果云端根本没有 reply 字段，则直接复制本地的整个 reply 配置到云端缓存中
                if "reply" not in pure_cached or not isinstance(pure_cached["reply"], dict):
                    pure_cached["reply"] = copy.deepcopy(pure_local_data["reply"])
                    has_merged_any = True
                else:
                    local_reply = pure_local_data["reply"]
                    cached_reply = pure_cached["reply"]
                    if isinstance(local_reply, dict) and isinstance(cached_reply, dict):
                        # 🛡️ [Bug Fix] 名单类字段并集合并：防止云端空值或旧版本将本地名单抹除
                        for list_key in ["auto_chat_friend_whitelist", "auto_chat_group_whitelist", "auto_chat_friend_excludes", "auto_chat_group_excludes"]:
                            local_list = local_reply.get(list_key)
                            cached_list = cached_reply.get(list_key)
                            
                            # 🛡️ 架构防线：如果云端该名单缺失或为空，而本地有值，则直接用本地值覆盖它，防止被云端空值抹除
                            if local_list and not cached_list:
                                cached_reply[list_key] = copy.deepcopy(local_list)
                                has_merged_any = True
                            elif isinstance(local_list, list) and isinstance(cached_list, list):
                                merged_list = list(set(str(item).strip() for item in (local_list + cached_list) if item))
                                if len(merged_list) != len(cached_list) or set(merged_list) != set(cached_list):
                                    cached_reply[list_key] = merged_list
                                    has_merged_any = True

                        # 🛡️ [Bug Fix] 模式开关字段保护：当云端完全缺失该字段时（如旧版升级），以本地有效值补全
                        # 场景：用户将好友模式从 black 改成 white 后升级产品，云端旧快照中可能根本没有该字段
                        # 注意：此处只补全「云端缺失」的字段，不覆盖云端已有的值（因为 is_local_newer=False 分支云端更新）
                        _mode_keys = [
                            "auto_chat_friend_mode", "auto_chat_group_mode",
                            "moment_interact_friend_mode", "auto_chat_group_at_only",
                            "bot_group_auto_start", "respond_to_all_mentions",
                        ]
                        for mode_key in _mode_keys:
                            local_val = local_reply.get(mode_key)
                            if local_val is not None and mode_key not in cached_reply:
                                cached_reply[mode_key] = local_val
                                has_merged_any = True
                                logger.info(f"[多账号] 云端缺失开关字段 '{mode_key}'，已从本地补全: {local_val}")


            # 判断是否有实际脏数据需落盘
            local_dirty = False
            if not local_data:
                local_dirty = True
            else:
                try:
                    if json.dumps(pure_local_data, sort_keys=True) != json.dumps(pure_cached, sort_keys=True):
                        local_dirty = True
                except Exception:
                    local_dirty = True

            if local_dirty:
                _save_local_settings_with_meta(path, pure_cached, current_uid, cloud_updated_str)
                logger.info(f"[多账号] ☁️ 已用云端最新配置覆盖并落盘本地配置文件 ({target_wxid})")
            
            # 如果发生过并集合并，说明本地的数据成功并入了云端缓存，应该异步同步推送给云端，以保证云端 and 本地一致
            if has_merged_any:
                config_cache.set(cache_key, pure_cached, sync_cloud=True)

            return pure_cached

    # 4. 如果云端目前没有数据（空账号冷启动），则信任并上传本地物理配置，或者落盘默认配置
    if local_data:
        # 安全保护：如果本地配置文件的归属用户（owner_uid）与当前登录用户不符，说明是他人遗留的脏配置，严禁反向推送污染云端！
        if _is_actual_user_mismatch(local_owner, current_uid):
            logger.info(f"[多账号] 检测到本地遗留配置归属用户[{local_owner}]与当前登录用户[{current_uid}]不符，已丢弃该本地残留配置，走默认配置初始化。")
            # 🛡️ [Bug Fix] 原来此处直接 fall-through 到 Step 5，会把空 default_config 推送到云端，
            # 在 owner_uid 不匹配且云端确实为空的边界场景下，可能污染云端配置（如把白名单清空推上去）。
            # 修复：直接落盘新用户干净的默认配置，但不推送云端（sync_cloud=False），
            # 等后续登录触发 load_from_cloud 后，云端正确快照自然建立，无需此处贸然推送。
            _save_local_settings_with_meta(path, default_config, current_uid)
            config_cache.set(cache_key, default_config, sync_cloud=False)
            logger.info(f"[多账号] 已为当前用户 [{current_uid}] 落盘干净默认配置，等待云端正式同步确认 ({target_wxid})")
            return default_config
        else:
            # 将本地现有的配置同步推送给该新登录的云端，建立云端首个快照
            config_cache.set(cache_key, pure_local_data, sync_cloud=True)
            logger.info(f"[多账号] ☁️ 检测到云端无此配置，已将本地配置初始化并推送同步至云端 ({target_wxid})")
            return pure_local_data

    # 5. 均没有则写入并同步默认模板配置
    _save_local_settings_with_meta(path, default_config, current_uid)
    config_cache.set(cache_key, default_config, sync_cloud=True)
    logger.info(f"[多账号] 已初始化并同步默认私有设置模板 ({target_wxid})")

    return default_config


def save_account_settings(data: dict, wxid: str = None):
    """保存当前账号的私有设置 (写入本地并在内存中触发同步后端漫游)"""
    from src.crm.account_data import get_active_account, get_account_data_dir
    target_wxid = wxid or get_active_account()
    # 提取当前登录平台用户 ID
    from src.utils.cloud_sync import get_cloud_client
    cloud_client = get_cloud_client()
    current_uid = cloud_client._try_load_sso_user_id() or "local-desktop-user"
    
    # 物理落盘本地，注入 _meta
    path = os.path.join(get_account_data_dir(target_wxid), "settings.json")
    _save_local_settings_with_meta(path, data, current_uid)
        
    # 推送至缓存与同步后端漫游，注意去掉 _meta 以免污染后端
    from src.utils.config_cache import config_cache
    cache_key = f"account_settings_{target_wxid}"
    
    pure_data = dict(data)
    pure_data.pop("_meta", None)
    config_cache.set(cache_key, pure_data)
