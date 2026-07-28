import os
import json
import logging
from .base import PrivacyShieldBase

logger = logging.getLogger(__name__)

class ConfigMixin(PrivacyShieldBase):
    """配置管理相关逻辑"""
    
    def set_config_path(self, path: str):
        """设置配置文件路径"""
        if not path:
            # 兜底使用全局统一配置
            from pathlib import Path
            path = str(Path.home() / ".xm-ai-bot" / "privacy_shield.json")
        
        self._config_path = path
        logger.info(f"[隐私遮罩] 配置文件路径已设置为: {path}")

    def _load_config_enabled(self) -> bool:
        """从配置文件读取隐私保护状态（默认为 True）"""
        if not self._config_path:
            self.set_config_path("") # 触发兜底
            
        try:
            if os.path.exists(self._config_path):
                with open(self._config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # 默认 True：如果配置文件中没有该字段，默认开启
                val = data.get("privacy_shield_enabled", True)
                return val
        except Exception as e:
            logger.debug(f"[隐私遮罩] 加载配置异常: {e}")
            
        return True  # 默认开启

    def _save_config(self, enabled: bool):
        """保存隐私保护状态到配置文件"""
        if not self._config_path:
            self.set_config_path("") # 触发兜底
            
        try:
            data = {}
            if os.path.exists(self._config_path):
                with open(self._config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            data["privacy_shield_enabled"] = enabled
            os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"[隐私遮罩] 配置已保存: enabled={enabled} -> {self._config_path}")
        except Exception as e:
            logger.error(f"[隐私遮罩] 保存配置失败: {e}")

    def _load_config_record_mode(self) -> bool:
        """从配置文件读取录屏保护模式状态（默认为 False）"""
        if not self._config_path:
            self.set_config_path("") # 触发兜底
            
        try:
            if os.path.exists(self._config_path):
                with open(self._config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data.get("record_mode", False)
        except Exception as e:
            logger.debug(f"[隐私遮罩] 加载录屏配置异常: {e}")
            
        return False

    def _save_record_mode(self, enabled: bool):
        """保存录屏保护模式状态到配置文件"""
        if not self._config_path:
            self.set_config_path("") # 触发兜底
            
        try:
            data = {}
            if os.path.exists(self._config_path):
                with open(self._config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            data["record_mode"] = enabled
            os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"[隐私遮罩] 录屏配置已保存: record_mode={enabled} -> {self._config_path}")
        except Exception as e:
            logger.error(f"[隐私遮罩] 保存录屏配置失败: {e}")
