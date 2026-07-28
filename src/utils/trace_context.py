"""
请求链路 Trace 上下文工具

用于在同一请求链路中透传 trace_id，供事件上报复用。
"""

from __future__ import annotations

import contextvars
import uuid

_trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "xm_trace_id", default=""
)


def new_trace_id() -> str:
    """生成新的 trace_id"""
    return f"trc_{uuid.uuid4().hex}"


def set_trace_id(trace_id: str) -> contextvars.Token:
    """设置当前上下文 trace_id"""
    return _trace_id_var.set(trace_id)


def get_trace_id() -> str:
    """获取当前上下文 trace_id"""
    return _trace_id_var.get()


def reset_trace_id(token: contextvars.Token):
    """恢复到设置前的 trace_id 状态"""
    _trace_id_var.reset(token)

