"""
历史模块名保留：手机号验真/同步逻辑已合并至 validate_mobile.ValidateMobileService。

旧仓库曾存在独立 services/validate_mobile_service.py（反编译残片不可维护）。
此处仅作兼容 re-export，供全量 Cython 编译与潜在旧 import 使用。
"""

from src.ai.validate_mobile import ValidateMobileService

__all__ = ["ValidateMobileService"]
