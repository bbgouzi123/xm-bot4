"""
本地存储与试用逻辑模块
"""
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any

logger = logging.getLogger(__name__)

# 本地缓存目录
CONFIG_DIR = Path.home() / ".xm-ai-bot"
LICENSE_FILE = CONFIG_DIR / "license.json"
TRIAL_FILE = CONFIG_DIR / "trial.json"

class StorageMixin:
    @classmethod
    def load_license(cls) -> Dict[str, Any]:
        """读取本地许可证缓存"""
        if LICENSE_FILE.exists():
            try:
                return json.loads(LICENSE_FILE.read_text(encoding='utf-8'))
            except Exception:
                pass
        return {"license_key": "", "status": "unbound", "expire_info": ""}

    @classmethod
    def save_license(cls, data: Dict[str, Any]):
        """保存许可证信息到本地缓存"""
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            LICENSE_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding='utf-8'
            )
        except Exception as e:
            logger.error(f"保存许可证缓存失败: {e}")

    @staticmethod
    def _get_trial_info(user_id: str = None) -> Dict[str, Any]:
        """
        获取体验版试用信息：首次启动时间 + 剩余天数
        """
        default_trial_days = 3  # 默认试用天数（兜底值）
        uid_key = str(user_id) if user_id else "_machine_"
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            all_data = {}
            if TRIAL_FILE.exists():
                all_data = json.loads(TRIAL_FILE.read_text(encoding='utf-8'))
            
            # 兼容旧格式迁移
            if "first_launch" in all_data and "users" not in all_data:
                old_launch = all_data["first_launch"]
                all_data = {"users": {"_machine_": {"first_launch": old_launch, "trial_days": default_trial_days}}}
                TRIAL_FILE.write_text(json.dumps(all_data, ensure_ascii=False, indent=2), encoding='utf-8')
                logger.info("[试用] 旧 trial.json 已自动迁移为多用户格式")
            
            if "users" not in all_data:
                all_data["users"] = {}
            
            users = all_data["users"]
            
            if uid_key in users:
                user_trial = users[uid_key]
                first_launch = datetime.fromisoformat(user_trial.get("first_launch", ""))
                trial_days = user_trial.get("trial_days", default_trial_days)
            else:
                first_launch = datetime.now()
                trial_days = default_trial_days
                users[uid_key] = {
                    "first_launch": first_launch.isoformat(),
                    "trial_days": trial_days,
                }
                TRIAL_FILE.write_text(json.dumps(all_data, ensure_ascii=False, indent=2), encoding='utf-8')
                logger.info(f"[试用] 用户 {uid_key} 首次使用，记录试用开始时间: {first_launch}")

            elapsed = (datetime.now() - first_launch).days
            remaining = max(0, trial_days - elapsed)
            return {
                "first_launch": first_launch.isoformat(),
                "trial_days": trial_days,
                "trial_elapsed": elapsed,
                "trial_remaining": remaining,
                "trial_expired": remaining <= 0,
            }
        except Exception as e:
            logger.error(f"读取试用信息失败: {e}")
            return {
                "first_launch": "",
                "trial_days": default_trial_days,
                "trial_elapsed": 0,
                "trial_remaining": default_trial_days,
                "trial_expired": False,
            }
