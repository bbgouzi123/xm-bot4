import logging
from typing import Any, List, Optional

logger = logging.getLogger(__name__)


class CloudSyncPublicMixin:
    """同步服务公共资源 Mixin (Category A)"""

    def pull_industry_templates(self) -> Optional[List[dict]]:
        """从同步后端拉取行业模板库"""
        data = self._get("/api/v1/templates/industries")
        if data is not None:
            logger.info(f"[同步服务] 拉取行业模板: {len(data)} 条")
        return data

    def pull_excel_rules(self) -> Optional[List[dict]]:
        """从同步后端拉取 Excel 解析规则"""
        data = self._get("/api/v1/excel-rules")
        if data is not None:
            logger.info(f"[同步服务] 拉取 Excel 规则: {len(data)} 条")
        return data

    def push_excel_rule(self, fingerprint: str, headers: list,
                        mapping: dict, industry_hint: str = "") -> bool:
        """上传一条 Excel 解析规则（众包贡献）"""
        result = self._post("/api/v1/excel-rules/sync", {
            "rules": [{
                "fingerprint": fingerprint,
                "headers": headers,
                "mapping": mapping,
                "industry_hint": industry_hint
            }]
        })
        return result is not None

    def pull_field_aliases(self) -> Optional[List[dict]]:
        """从同步后端拉取字段别名库"""
        return self._get("/api/v1/field-aliases")

    def pull_verify_templates(self, industry_id: str = "") -> Optional[List[dict]]:
        """从同步后端拉取验证消息模板"""
        path = "/api/v1/verify-templates"
        if industry_id:
            path += f"?industry_id={industry_id}"
        return self._get(path)

    def pull_greeting_templates(self, industry_id: str = "") -> Optional[List[dict]]:
        """从同步后端拉取问候模板"""
        path = "/api/v1/greeting-templates"
        if industry_id:
            path += f"?industry_id={industry_id}"
        return self._get(path)

    def pull_remote_config(self, key: str) -> Optional[Any]:
        """获取远程配置项"""
        return self._get(f"/api/v1/remote-configs/{key}")

    def initial_sync(self):
        """启动时执行初始同步：拉取公共资源 + 恢复用户私有数据"""
        if not self.check_health():
            logger.warning("[同步服务] 同步后端不可达，使用本地缓存")
            return False

        logger.info("[同步服务] 开始初始同步...")

        # 1. 拉取行业模板并缓存到本地
        templates = self.pull_industry_templates()
        if templates:
            self._cache_to_local("industry_templates.json", templates)

        # 2. 拉取 Excel 规则
        rules = self.pull_excel_rules()
        if rules:
            self._cache_to_local("excel_rules.json", rules)

        # 3. 拉取字段别名
        aliases = self.pull_field_aliases()
        if aliases:
            self._cache_to_local("field_aliases.json", aliases)

        # ===== B 类：用户私有数据恢复 =====
        self._restore_user_data()

        self._initial_sync_done = True
        logger.info("[同步服务] 初始同步完成 ✅")
        return True
