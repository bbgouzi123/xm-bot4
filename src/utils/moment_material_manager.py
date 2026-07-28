"""
兼容占位：与 ``moment_material.py`` 合并，此处仅转发单例类。

避免仓库中同名类两份实现导致 Cython/静态检查混乱。
"""

from src.utils.moment_material import MomentMaterialManager

__all__ = ["MomentMaterialManager"]
