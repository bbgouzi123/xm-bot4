"""UIA 微信操作日志管理器。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from logging.handlers import RotatingFileHandler


class UiaLogger:
    """UIA 微信操作日志管理器"""

    def __init__(
        self, log_dir: Optional[str] = None, logger_name: str = "uiauto"
    ) -> None:
        self.log_dir = Path(log_dir or "logs/uiauto")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        self.logger = logging.getLogger(logger_name)
        if self.logger.handlers:
            self.logger.handlers.clear()
        file_handler = RotatingFileHandler(
            self.log_dir / "wechat_operations.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        self.console_handler = logging.StreamHandler()
        self.console_handler.setFormatter(formatter)
        self.logger.setLevel(logging.DEBUG)
        self.logger.addHandler(file_handler)
        self.logger.addHandler(self.console_handler)
        self.logger.propagate = False

    def get_logger(self) -> logging.Logger:
        return self.logger

    def set_debug(self, debug: bool) -> None:
        if debug:
            self.logger.setLevel(logging.DEBUG)
            self.console_handler.setLevel(logging.DEBUG)
        else:
            self.logger.setLevel(logging.WARNING)
            self.console_handler.setLevel(logging.WARNING)


def get_logger(name: str = "uiauto") -> logging.Logger:
    """模块级便捷函数（供 hotkey_manager 等与 task_logger 对齐的调用方使用）。"""
    return logging.getLogger(f"xm.uia.{name}")
