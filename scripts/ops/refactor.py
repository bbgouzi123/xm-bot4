import ast
import json
import uuid

with open('src/crm/industry_config.py', 'r', encoding='utf-8') as f:
    source = f.read()

# Find the `defaults` definition
tree = ast.parse(source)
defaults_node = None
for node in ast.walk(tree):
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == 'defaults':
                defaults_node = node.value

if defaults_node:
    defaults_code = ast.unparse(defaults_node)
    
    # We will safely eval this list
    defaults = eval(defaults_code)

    print(f"Got {len(defaults)} default profiles!")
    
    # Let's generate the SYSTEM_TEMPLATES code
    templates_str = "SYSTEM_TEMPLATES = [\n"
    for i, d in enumerate(defaults):
        # assign fixed ID
        sys_id = f"sys_{i:03d}"
        d["id"] = sys_id
        templates_str += f"    {repr(d)},\n"
    templates_str += "]\n"

    new_source = source[:source.find("class IndustryConfigManager:")]
    
    manager_code = f"""
{templates_str}

class IndustryConfigManager:
    \"\"\"xm-core：公共模板池 + 账号级私有重写 (SQLite) · 产品 xm-bot4\"\"\"
    
    def __init__(self, config_path: str = None, account_id: str = "main"):
        self.account_id = account_id
        if self.account_id == "main":
            from src.crm.account_data import get_active_account
            self.account_id = get_active_account()
        from src.utils.account_db import AccountDatabaseManager
        self.db = AccountDatabaseManager(self.account_id)
        
        self._profiles: List[IndustryProfile] = []
        self._active_id: str = ""
        self._custom_profiles_dict = {{}} # id -> dict
        
        self._load()

    def _load(self):
        \"\"\"从 SQLite 加载私有配置，合并系统模板\"\"\"
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT config_value FROM bot_config WHERE config_key = 'industry_config_data'")
                row = cursor.fetchone()
                if row and row['config_value']:
                    data = json.loads(row['config_value'])
                    self._active_id = data.get("active_profile_id", "")
                    # Load custom profiles (newly created or modified system ones)
                    self._custom_profiles_dict = {{p["id"]: p for p in data.get("profiles", [])}}
        except Exception as e:
            logger.error(f"[CRM] 加载行业私有配置失败: {{e}}")

        # Merge Phase
        self._profiles = []
        sys_ids = set()
        
        # 1. Load System templates. If customized, use custom dict; else use default.
        for tmpl in SYSTEM_TEMPLATES:
            sys_id = tmpl["id"]
            sys_ids.add(sys_id)
            if sys_id in self._custom_profiles_dict:
                self._profiles.append(IndustryProfile.from_dict(self._custom_profiles_dict[sys_id]))
            else:
                self._profiles.append(IndustryProfile.from_dict(tmpl))
                
        # 2. Append Pure User-created Profiles (id not starting with sys_)
        # Sort them by their position in custom_profiles to maintain order
        for p_id, p_dict in self._custom_profiles_dict.items():
            if p_id not in sys_ids:
                self._profiles.append(IndustryProfile.from_dict(p_dict))
                
        if not self._profiles:
            # Fallback if somehow empty, should never happen with templates
            pass
            
        # Ensure active_id is valid
        if self._active_id not in [p.id for p in self._profiles]:
            self._active_id = self._profiles[0].id if self._profiles else ""
            
        logger.debug(f"[CRM] 行业数据混流完成: {{len(self._profiles)}}个模板加载, 活跃={{self._active_id}}")

    def _save(self):
        \"\"\"只保存 [被修改的系统模板] + [用户自己新增的模板]\"\"\"
        try:
            # Only serialize what is in self._custom_profiles_dict
            data = {{
                "active_profile_id": self._active_id,
                "profiles": list(self._custom_profiles_dict.values()),
            }}
            json_str = json.dumps(data, ensure_ascii=False)
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO bot_config (config_key, config_value, updated_at)
                    VALUES ('industry_config_data', ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(config_key) DO UPDATE SET 
                    config_value = excluded.config_value, 
                    updated_at = CURRENT_TIMESTAMP
                ''', (json_str,))
                conn.commit()
            logger.debug(f"[CRM] 私有行业配置固化成功 (私有库中包含 {{len(data['profiles'])}} 个覆写档案)")
        except Exception as e:
            logger.error(f"[CRM] 保存行业配置失败: {{e}}")

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
                logger.info(f"[CRM] 切换行业: {{p.icon}} {{p.name}}")
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
        icon: str = "🤖",
    ) -> IndustryProfile:
        profile = IndustryProfile()
        profile.id = f"profile_{{uuid.uuid4().hex[:8]}}"
        profile.name = name
        profile.icon = icon
        profile.created = datetime.now().strftime("%Y-%m-%d")
        profile.product = product
        profile.selling_point = selling_point
        profile.persona = persona
        profile.forbidden = forbidden
        profile.knowledge = knowledge
        profile.intensity = intensity

        self._profiles.insert(0, profile)  # Prepend user ones to top
        self._custom_profiles_dict[profile.id] = profile.to_dict()
        
        self._active_id = profile.id
        self._save()
        logger.info(f"[CRM] 创建纯私有行业流: {{profile}}")
        return profile

    def update_profile(self, profile_id: str, updates: dict) -> bool:
        for p in self._profiles:
            if p.id == profile_id:
                for key, value in updates.items():
                    if hasattr(p, key):
                        setattr(p, key, value)
                
                # Flag this profile as customized and save to DB
                self._custom_profiles_dict[profile_id] = p.to_dict()
                self._save()
                logger.info(f"[CRM] 更新并覆写私有行业配置: {{p.name}}")
                return True
        return False

    def delete_profile(self, profile_id: str) -> bool:
        # If it's a sys_ id, deleting simply means "Restore to default"
        is_sys = str(profile_id).startswith("sys_")
        
        if is_sys:
            if profile_id in self._custom_profiles_dict:
                del self._custom_profiles_dict[profile_id]
                self._save()
                self._load() # reload to grab sys default back
                logger.info(f"[CRM] 恢复系统模板: {{profile_id}}")
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
                logger.info(f"[CRM] 删除私有行业配置: {{p.name}}")
                return True
        return False
"""
    with open('src/crm/industry_config.py', 'w', encoding='utf-8') as f:
        f.write(new_source + manager_code)
    print("DONE Refactoring!")
