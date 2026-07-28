import os
import json
import logging
import threading
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class SessionTypeCache:
    def __init__(self):
        self.config_dir = Path(os.path.expanduser("~/.xm-ai-bot"))
        self._caches: Dict[str, Dict[str, str]] = {}
        self._lock = threading.RLock()

    def _resolve_wxid(self, wxid: Optional[str] = None) -> str:
        """解析当前上下文中的微信号 wxid，以便按微信号进行数据隔离"""
        if wxid:
            return wxid
        try:
            from src.crm.account_data import get_active_account
            active = get_active_account()
            if active and active != 'default':
                return active
        except Exception:
            pass
        return 'default'

    def _get_cache_file(self, wxid: str) -> Path:
        """根据 wxid 获取缓存文件路径"""
        if wxid == 'default':
            return self.config_dir / "session_types.json"
        
        # 兼容微信原始微信号以 "wxid_" 开头的情况，只保留单层 "wxid_" 前缀
        clean_wxid = wxid
        if clean_wxid.startswith("wxid_"):
            clean_wxid = clean_wxid[5:]
        return self.config_dir / f"session_types_wxid_{clean_wxid}.json"

    def _get_cache(self, wxid: str) -> Dict[str, str]:
        """获取特定 wxid 的缓存字典（懒加载模式）"""
        with self._lock:
            if wxid not in self._caches:
                cache_file = self._get_cache_file(wxid)
                cache_data = {}
                try:
                    # 💡 兼容性迁移逻辑：如果新格式的规范化文件名不存在，但是存在带有重复前缀的旧版缓存文件，自动执行无损迁移
                    if not cache_file.exists() and wxid.startswith("wxid_"):
                        legacy_file = self.config_dir / f"session_types_wxid_{wxid}.json"
                        if legacy_file.exists():
                            try:
                                logger.info(f"[SessionCache] 检测到旧版双重前缀缓存文件 {legacy_file.name}，正在无损迁移至新版规范化路径...")
                                legacy_file.rename(cache_file)
                            except Exception as e_rename:
                                logger.warning(f"[SessionCache] 迁移旧缓存文件失败: {e_rename}")

                    if cache_file.exists():
                        cache_data = json.loads(cache_file.read_text(encoding="utf-8"))
                        logger.info(f"[SessionCache] 从 {cache_file} 成功加载了 {len(cache_data)} 条会话映射记录")
                    else:
                        # 首次创建时，尝试从旧版默认 session_types.json 迁移数据
                        default_file = self.config_dir / "session_types.json"
                        if wxid != 'default' and default_file.exists():
                            try:
                                default_data = json.loads(default_file.read_text(encoding="utf-8"))
                                cache_data = dict(default_data)
                                self.config_dir.mkdir(parents=True, exist_ok=True)
                                cache_file.write_text(json.dumps(cache_data, ensure_ascii=False, indent=2), encoding="utf-8")
                                logger.info(f"[SessionCache] 首次初始化：已成功从默认缓存迁移 {len(cache_data)} 条记录到 {cache_file}")
                            except Exception as e:
                                logger.warning(f"[SessionCache] 迁移默认缓存记录到新微信实例失败: {e}")
                except Exception as e:
                    logger.error(f"[SessionCache] 加载缓存文件 {cache_file} 异常: {e}")
                
                # 初始化缓存后，异步从云端同步数据库拉取并合并配置
                self._async_pull_from_cloud(wxid, cache_data)
                self._caches[wxid] = cache_data
            
            return self._caches[wxid]

    def _save_cache(self, wxid: str):
        """保存特定 wxid 的缓存到本地磁盘"""
        with self._lock:
            if wxid not in self._caches:
                return
            try:
                self.config_dir.mkdir(parents=True, exist_ok=True)
                cache_file = self._get_cache_file(wxid)
                cache_file.write_text(json.dumps(self._caches[wxid], ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as e:
                logger.error(f"[SessionCache] 写入本地缓存失败: {e}")

    def get_type(self, name: str, wxid: Optional[str] = None) -> Optional[str]:
        target_wxid = self._resolve_wxid(wxid)
        cache = self._get_cache(target_wxid)
        return cache.get(name)

    def set_type(self, name: str, type_str: str, wxid: Optional[str] = None):
        target_wxid = self._resolve_wxid(wxid)
        cache = self._get_cache(target_wxid)
        
        old_type = cache.get(name)
        # 降级防御：如果是已确认的好友或群聊，拒绝自动识别退化为公众号
        if old_type in ("friend", "chat", "group") and type_str in ("official_account", "unknown"):
            return
            
        if old_type != type_str:
            with self._lock:
                cache[name] = type_str
                self._save_cache(target_wxid)
            # 异步上报同步到云端数据库
            self._async_push_to_cloud(target_wxid, name, type_str)

    def revalidate_with_contacts(self, friends: list, groups: list, wxid: Optional[str] = None):
        """当通讯录加载完成后，订正可能被误判的缓存记录"""
        if not friends and not groups:
            return
            
        target_wxid = self._resolve_wxid(wxid)
        cache = self._get_cache(target_wxid)
        
        friend_names = set()
        for f in friends:
            n = (f.get('name') or '').strip()
            r = (f.get('remark') or '').strip()
            if n:
                friend_names.add(n)
            if r:
                friend_names.add(r)
        group_names = {(g.get('name') or '').strip() for g in groups} - {''}
        known_names = friend_names | group_names

        corrected = []
        with self._lock:
            for name, cached_type in list(cache.items()):
                # 将被误判为 official_account 但实际在通讯录中的记录清除（让系统重新判断）
                if cached_type == 'official_account' and name in known_names:
                    corrected.append(name)
                    del cache[name]
                # 将被误判为 group 但实际在好友通讯录中的记录清除（防止带冒号消息导致的永久误判）
                elif cached_type == 'group' and name in friend_names:
                    corrected.append(name)
                    del cache[name]
                    logger.info(f"[SessionCache] 误判纠正: '{name}' 被缓存为 group 但实际属于好友，已清除缓存")
            if corrected:
                self._save_cache(target_wxid)
                
        if corrected:
            logger.info(
                f"[SessionCache] wxid={target_wxid} 订正误判会话记录 {len(corrected)} 条: {corrected}"
            )

    def clear_session_type(self, name: str, wxid: Optional[str] = None):
        """删除指定会话名称的缓存记录，让系统下次自动重新判断类型"""
        target_wxid = self._resolve_wxid(wxid)
        cache = self._get_cache(target_wxid)
        with self._lock:
            if name in cache:
                del cache[name]
                self._save_cache(target_wxid)
                logger.info(f"[SessionCache] 已清除会话缓存: '{name}' (wxid={target_wxid})")
                return True
        return False

    # ==================== 云端异步同步层 (基于 T14: user_settings 统一 KV) ====================

    def _async_pull_from_cloud(self, wxid: str, cache_data: dict):
        """异步从云端设置数据库拉取并合并会话类型映射"""
        if wxid == 'default':
            return
            
        def pull_job():
            try:
                from src.utils.cloud_sync import get_cloud_client
                client = get_cloud_client()
                logger.info(f"[SessionCache] 开始从云端设置库拉取微信实例 {wxid} 的会话映射...")
                res = client._get(f"/api/v1/settings/session_types_{wxid}", need_auth=True)
                if res and isinstance(res, dict):
                    modified = False
                    with self._lock:
                        for name, stype in res.items():
                            if not name or not stype:
                                continue
                            old_type = cache_data.get(name)
                            # 本地防降级防御
                            if old_type in ("friend", "group", "chat") and stype in ("official_account", "unknown"):
                                continue
                            if old_type != stype:
                                cache_data[name] = stype
                                modified = True
                        if modified:
                            self._save_cache(wxid)
                    logger.info(f"[SessionCache] 云端会话配置合并成功 (wxid={wxid})")
            except Exception as e:
                logger.debug(f"[SessionCache] 异步拉取云端设置失败 (非阻塞正常机制): {e}")

        threading.Thread(target=pull_job, daemon=True).start()

    def _async_push_to_cloud(self, wxid: str, name: str, type_str: str):
        """异步将当前微信实例的完整会话映射全量同步到云端数据库"""
        if wxid == 'default':
            return
            
        def push_job():
            try:
                from src.utils.cloud_sync import get_cloud_client
                client = get_cloud_client()
                
                # 获取当前最新的完整本地缓存数据，全量覆盖同步后端
                cache = self._get_cache(wxid)
                payload = {
                    "value": cache
                }
                logger.info(f"[SessionCache] 正在向云端同步全量会话配置 (wxid={wxid})...")
                client._put(f"/api/v1/settings/session_types_{wxid}", payload, need_auth=True)
            except Exception as e:
                logger.debug(f"[SessionCache] 异步推送配置到云端失败 (非阻塞正常机制): {e}")

        threading.Thread(target=push_job, daemon=True).start()

# 全局单例
session_type_cache = SessionTypeCache()
