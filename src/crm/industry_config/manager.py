"""
行业配置管理器 — 管理行业配置的 CRUD、切换与同步服务

数据存储：xm-core（xm-bot4 后端）/ AccountDatabaseManager (SQLite) bot_config 表
"""
import uuid
import logging
from typing import List, Optional
from datetime import datetime

from .profile import IndustryProfile, merge_chat_eq
from .templates import SYSTEM_TEMPLATES

logger = logging.getLogger(__name__)


class IndustryConfigManager:
    """xm-core：公共模板池 + 账号级私有重写 (SQLite) · 产品 xm-bot4"""
    
    def __init__(self, config_path: str = None, account_id: str = "main"):
        self.account_id = account_id
        if self.account_id == "main":
            from src.crm.account_data import get_active_account
            self.account_id = get_active_account()
        
        self._profiles: List[IndustryProfile] = []
        self._active_id: str = ""
        self._custom_profiles_dict = {} # id -> dict
        
        self._load()

    def _load(self):
        """加载私有配置，合并系统模板。优先使用本地 JSON 物理文件以保证数据的实时与不丢失，如果不存在则使用内存云端同步缓存。"""
        data = None
        try:
            import os, json
            from src.crm.account_data import get_config_path
            from src.utils.config_cache import config_cache
            
            local_cfg_path = get_config_path(self.account_id)
            local_data = None
            
            # 1. 读取本地物理 JSON 文件
            try:
                if os.path.exists(local_cfg_path):
                    with open(local_cfg_path, "r", encoding="utf-8") as f:
                        local_data = json.load(f)
            except Exception as local_fail:
                logger.warning(f"[CRM] 从本地物理 JSON 加载行业配置失败: {local_fail}")

            # 2. 从内存云端同步缓存 config_cache 中读取
            cache_key = f"industry_config_{self.account_id}"
            cloud_data = config_cache.get(cache_key)
            cloud_updated_at = config_cache.get_updated_at(cache_key)
            
            # fallback: 企业推送/旧版同步的行业配置存为 industry_config_global，
            # 若账号专属 key 没有数据，则 fallback 到 global key（确保"小瑞"等企业行业可见）
            if not cloud_data:
                global_cloud_data = config_cache.get("industry_config_global")
                if global_cloud_data:
                    logger.info(f"[CRM] 账号专属行业配置缓存为空，从 industry_config_global 兜底加载")
                    cloud_data = global_cloud_data
                    cloud_updated_at = config_cache.get_updated_at("industry_config_global")

            # 3. 比较云端与本地配置，选择最新且合法的那个，避免由于本地配置为空或降级造成数据丢失
            if local_data and cloud_data:
                local_updated_at = local_data.get("updated_at", "")
                
                # 如果云端配置的时间戳更晚，或者本地没有时间戳而云端有，则优先采用云端配置
                if cloud_updated_at and (not local_updated_at or cloud_updated_at > local_updated_at):
                    logger.info(f"[CRM] 检测到云端配置较新 ({cloud_updated_at} > {local_updated_at})，优先使用云端配置覆盖本地")
                    data = cloud_data
                    # 将云端配置覆盖回本地文件
                    if local_cfg_path:
                        try:
                            with open(local_cfg_path, "w", encoding="utf-8") as f:
                                json.dump(cloud_data, f, ensure_ascii=False, indent=2)
                        except Exception as write_err:
                            logger.warning(f"[CRM] 自动同步云端配置到本地失败: {write_err}")
                else:
                    data = local_data
            elif local_data:
                data = local_data
            elif cloud_data:
                data = cloud_data
                # 写入本地物理文件以做兜底
                if local_cfg_path:
                    try:
                        with open(local_cfg_path, "w", encoding="utf-8") as f:
                            json.dump(cloud_data, f, ensure_ascii=False, indent=2)
                    except Exception as write_err:
                        logger.warning(f"[CRM] 自动同步云端配置到本地失败: {write_err}")
            
            if not data:
                # 兼容旧版本：尝试读取旧的全局同步后端缓存 key
                data = config_cache.get("industry_config_data")
                if data:
                    config_cache.set(cache_key, data, sync_cloud=False)

            if data and isinstance(data, dict):
                self._active_id = data.get("active_profile_id", "")
                self._custom_profiles_dict = {}
                need_save = False
                for p in data.get("profiles", []):
                    p_id = p.get("id")
                    p_name = p.get("name")
                    if p_id == "sys_001" and p_name in ("批发零售商", "批发零售"):
                        need_save = True
                        continue
                    if p_id == "sys_002" and p_name in ("本地实体店", "实体店"):
                        need_save = True
                        continue
                    if p_id == "sys_003" and p_name in ("企业服务商", "企业服务"):
                        need_save = True
                        continue
                    self._custom_profiles_dict[p_id] = p
                
                if need_save:
                    self._save()
        except Exception as e:
            logger.error(f"[CRM] 加载行业私有配置失败: {e}")

        self._merge_templates()

    def _merge_templates(self):
        """Merge Phase: 同步后端优先，本地兜底"""
        self._profiles = []
        sys_ids = set()
        
        # 1. 优先从同步后端缓存获取模板（由 cloud_sync 启动时写入）
        base_templates = SYSTEM_TEMPLATES
        try:
            from src.utils.cloud_sync import load_cloud_cache_fast
            cloud_templates = load_cloud_cache_fast("industry_templates.json")
            
            # 防御性解析：如果被错误地包装成了 {"code": 20000, "data": [...]}
            if isinstance(cloud_templates, dict) and "data" in cloud_templates:
                cloud_templates = cloud_templates.get("data", [])
                
            if cloud_templates and isinstance(cloud_templates, list) and len(cloud_templates) > 0:
                base_templates = cloud_templates
                logger.info(f"[CRM] 使用同步后端行业模板: {len(cloud_templates)} 条")
        except Exception as e:
            logger.warning(f"[CRM] 同步后端行业模板读取失败: {e}")
        
        # 2. Load templates. If customized, use custom dict; else use default.
        try:
            for tmpl in base_templates:
                if not isinstance(tmpl, dict):
                    continue
                sys_id = tmpl.get("id")
                if not sys_id:
                    continue
                sys_ids.add(sys_id)
                if sys_id in self._custom_profiles_dict:
                    self._profiles.append(IndustryProfile.from_dict(self._custom_profiles_dict[sys_id]))
                else:
                    self._profiles.append(IndustryProfile.from_dict(tmpl))
        except Exception as e:
            logger.error(f"[CRM] 行业模板合并失败: {e}")

        # 3. Append Pure User-created Profiles (id not starting with sys_)
        for p_id, p_dict in self._custom_profiles_dict.items():
            if p_id not in sys_ids:
                self._profiles.append(IndustryProfile.from_dict(p_dict))

        if not self._profiles:
            logger.warning("[CRM] ⚠️ 行业模板为空！请确保同步后端服务已启动且 cloud_sync 已完成首次同步")

        # 🌟 【多开行业隔离】如果传入了具体微信号 of account_id，则优先从该实例的专属设置中读取活跃行业 ID
        if self.account_id and self.account_id not in ("default", "main", "global"):
            try:
                from src.api.instance_settings_api import load_instance_settings
                inst_cfg = load_instance_settings(self.account_id)
                if inst_cfg and inst_cfg.get("industry_profile_id"):
                    self._active_id = inst_cfg["industry_profile_id"]
                    logger.debug(f"[CRM] 优先应用微信号 {self.account_id} 绑定的行业配置 ID: {self._active_id}")
            except Exception as inst_err:
                logger.debug(f"[CRM] 从微信实例配置中读取行业配置失败: {inst_err}")

        # 4. 自动为所有已加载的减肥瘦身相关配置（系统/自定义）注入非手术限制，防止历史遗留或用户自定义配置穿帮
        for p in self._profiles:
            p_name = getattr(p, 'name', '') or ''
            p_product = getattr(p, 'product', '') or ''
            p_persona = getattr(p, 'persona', '') or ''
            if p.id == 'sys_100' or any(kw in p_name or kw in p_product or kw in p_persona for kw in ('减肥', '瘦身', '塑形', '减脂', '体质管理', '肥胖')):
                f_words = getattr(p, 'forbidden', '') or ''
                if f_words and not any(kw in f_words for kw in ('手术', '医学高敏')):
                    p.forbidden = f_words + '；本服务为非手术健康瘦身，绝对禁止向客户使用“手术”、“术后恢复”、“术前”等医学高敏词汇'
                elif not f_words:
                    p.forbidden = '本服务为非手术健康瘦身，绝对禁止向客户使用“手术”、“术后恢复”、“术前”等医学高敏词汇'

        # Ensure active_id is valid
        if self._active_id not in [p.id for p in self._profiles]:
            self._active_id = self._profiles[0].id if self._profiles else ""
            
        logger.debug(f"[CRM] 行业数据混流完成: {len(self._profiles)}个模板加载, 活跃={self._active_id}")

    def _save(self):
        """只保存 [被修改的系统模板] + [用户自己新增的模板]（内存缓存双写本地 + 同步后端持久化）"""
        try:
            from src.utils.config_cache import config_cache
            from datetime import datetime
            
            cache_key = f"industry_config_{self.account_id}"
            now_str = datetime.utcnow().isoformat() + "Z"
            data = {
                "active_profile_id": self._active_id,
                "profiles": list(self._custom_profiles_dict.values()),
                "updated_at": now_str,
            }
            # 1. 保存到内存缓存并触发同步后端异步同步（如果在线）
            config_cache.set(cache_key, data)
            
            # 2. 核心修复：本地 JSON 文件同步防御（确保重启不丢配置）
            try:
                import json
                from src.crm.account_data import get_config_path
                local_cfg_path = get_config_path(self.account_id)
                with open(local_cfg_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.warning(f"[CRM] 行业数据写入本地配置兜底失败: {e}")

            logger.debug(f"[CRM] 私有行业配置固化成功 (私有库中包含 {len(data['profiles'])} 个覆写档案)")
        except Exception as e:
            logger.error(f"[CRM] 保存行业配置失败: {e}")

    def get_active_profile(self) -> Optional[IndustryProfile]:
        if not self._active_id:
            return self._profiles[0] if self._profiles else None
        for p in self._profiles:
            if p.id == self._active_id:
                return p
        return self._profiles[0] if self._profiles else None

    def get_all_profiles(self) -> List[IndustryProfile]:
        return self._profiles.copy()

    def get_profile_by_id(self, profile_id: str) -> Optional[IndustryProfile]:
        for p in self._profiles:
            if p.id == profile_id:
                return p
        return None

    def switch_profile(self, profile_id: str) -> bool:
        for p in self._profiles:
            if p.id == profile_id:
                self._active_id = profile_id
                self._save()
                logger.info(f"[CRM] 切换行业: {p.icon} {p.name}")
                return True
        return False

    def create_profile(
        self,
        name: str,
        product: str,
        selling_point: str = "",
        persona: str = "",
        forbidden: str = "",
        knowledge: str = "",
        intensity: int = 2,
        price_list: list = None,
        icon: str = "i-carbon-bot",
        chat_eq: dict = None,
        materials: list = None,
        homepage_link: str = "",
        enable_live_record: bool = True,
        phone: str = "",
        address: str = "",
    ) -> IndustryProfile:
        profile = IndustryProfile()
        profile.id = f"profile_{uuid.uuid4().hex[:8]}"
        profile.name = name
        profile.icon = icon
        profile.created = datetime.now().strftime("%Y-%m-%d")
        profile.product = product
        profile.selling_point = selling_point
        profile.persona = persona
        profile.forbidden = forbidden
        profile.knowledge = knowledge
        profile.intensity = intensity
        profile.price_list = price_list or []
        profile.chat_eq = merge_chat_eq(chat_eq)
        profile.materials = materials or []
        profile.homepage_link = homepage_link
        profile.enable_live_record = enable_live_record
        profile.phone = phone
        profile.address = address

        self._profiles.insert(0, profile)  # Prepend user ones to top
        self._custom_profiles_dict[profile.id] = profile.to_dict()
        
        self._active_id = profile.id
        self._save()
        logger.info(f"[CRM] 创建纯私有行业流: {profile}")
        return profile

    def update_profile(self, profile_id: str, updates: dict) -> Optional[str]:
        # 普通自定义模板与系统模板均直接进行覆盖式私有重写，通过 _custom_profiles_dict 维护，不会产生重名克隆行业

        # 普通自定义模板修改
        for p in self._profiles:
            if p.id == profile_id:
                for key, value in updates.items():
                    if hasattr(p, key):
                        setattr(p, key, value)
                
                self._custom_profiles_dict[profile_id] = p.to_dict()
                self._save()
                logger.info(f"[CRM] 更新并覆写私有行业配置: {p.name}")
                return profile_id
        return None

    def reset_to_default(self, profile_id: str) -> bool:
        """恢复行业到系统/同步后端默认模板（删除私有覆写）"""
        if profile_id in self._custom_profiles_dict:
            del self._custom_profiles_dict[profile_id]
            self._save()
            self._load()  # 重新加载，恢复到模板默认数据
            logger.info(f"[CRM] 恢复系统模板默认值: {profile_id}")
            return True
        logger.info(f"[CRM] 行业 {profile_id} 不存在私有覆写，无需恢复")
        return False

    def delete_profile(self, profile_id: str) -> bool:
        # If it's a sys_ id, deleting simply means "Restore to default"
        is_sys = str(profile_id).startswith("sys_")
        
        if is_sys:
            if profile_id in self._custom_profiles_dict:
                del self._custom_profiles_dict[profile_id]
                self._save()
                self._load() # reload to grab sys default back
                logger.info(f"[CRM] 恢复系统模板: {profile_id}")
                return True
            return False # Unmodified sys template cannot be deleted
            
        # Pure user profile, completely delete
        for i, p in enumerate(self._profiles):
            if p.id == profile_id:
                self._profiles.pop(i)
                if profile_id in self._custom_profiles_dict:
                    del self._custom_profiles_dict[profile_id]
                    
                if self._active_id == profile_id:
                    self._active_id = (self._profiles[0].id if self._profiles else "")
                self._save()
                logger.info(f"[CRM] 删除私有行业配置: {p.name}")
                return True
        return False
