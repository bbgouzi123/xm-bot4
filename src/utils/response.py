"""
统一 API 响应工具 — 黄金信封格式

后端规范要求所有 API 必须返回：
{
    "code": 20000,
    "msg": "操作成功",
    "data": { ... }
}

用法：
    from src.utils.response import ok, err, ok_msg

    @router.get("/api/xxx")
    async def handler():
        return ok({"key": "value"})           # 成功 + 数据
        return ok_msg("操作完成")              # 成功 + 无数据
        return err(40000, "参数错误")          # 失败
        return err(50000, "内部错误", data)    # 失败 + 附加数据
"""
from typing import Any, Optional


def ok(data: Any = None, msg: str = "操作成功") -> dict:
    """成功响应（code: 20000）"""
    return {
        "code": 20000,
        "msg": msg,
        "data": data,
    }


def ok_msg(msg: str) -> dict:
    """成功响应（无数据）"""
    return {
        "code": 20000,
        "msg": msg,
        "data": None,
    }


def err(code: int = 50000, msg: str = "操作失败", data: Any = None) -> dict:
    """错误响应"""
    return {
        "code": code,
        "msg": msg,
        "data": data,
    }
