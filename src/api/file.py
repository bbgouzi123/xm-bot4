"""
历史模块 api/file：文件上传与微信侧能力已迁移至 src.api.file_api。

保留本文件仅用于 Cython 全量编译及极少数遗留 import；
新代码请使用 file_api 与 FastAPI 路由。
"""

from src.api.file_api import UPLOAD_DIR

__all__ = ["UPLOAD_DIR"]
