"""
任务日志记录器（移植自 xm-bot4 utils/logger.py — 47行部分反编译）

原始文件: utils/logger.py (PARTIAL(1), 47 lines)
注意：文件名改为 task_logger.py 避免与 Python 标准库 logging 冲突。
"""
import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List


class TaskLogger:
    """任务日志管理器（从反编译骨架重建）

    记录任务执行日志到 JSON 文件，支持持久化存储。
    """

    def __init__(self, log_dir: str = None):
        if log_dir:
            self._log_dir = Path(log_dir)
        else:
            self._log_dir = Path.home() / '.xm-ai-bot' / 'task_logs'
        self._log_dir.mkdir(parents=True, exist_ok=True)

    def log(self, task_type: str, action: str, detail: dict = None,
            status: str = 'info'):
        """记录任务日志"""
        entry = {
            'timestamp': datetime.now().isoformat(),
            'task_type': task_type,
            'action': action,
            'status': status,
            'detail': detail or {},
        }
        try:
            today = datetime.now().strftime('%Y%m%d')
            log_file = self._log_dir / f'task_{today}.json'
            logs = []
            if log_file.exists():
                try:
                    logs = json.loads(log_file.read_text(encoding='utf-8'))
                except Exception:
                    logs = []
            logs.append(entry)
            log_file.write_text(
                json.dumps(logs, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
        except Exception as e:
            print(f'[日志] 写入失败: {e}')

        # P1：本地日志保留的同时，异步镜像到同步后端事件流（失败不阻塞主流程）
        self._async_report_to_cloud(entry)

    def _async_report_to_cloud(self, entry: Dict[str, Any]):
        """异步上报任务日志到同步后端事件流"""
        def _push():
            try:
                from src.utils.cloud_sync import get_cloud_client
                get_cloud_client().report_event(
                    "task_log",
                    {
                        "task_type": entry.get("task_type", ""),
                        "action": entry.get("action", ""),
                        "status": entry.get("status", "info"),
                        "detail": entry.get("detail", {}) or {},
                        "created_at": entry.get("timestamp", ""),
                    },
                )
            except Exception:
                # 日志上报失败时保持静默，避免影响业务线程
                pass

        threading.Thread(target=_push, daemon=True, name="task-log-cloud").start()

    def get_today_logs(self, task_type: str = None) -> List[Dict]:
        """获取今日日志"""
        today = datetime.now().strftime('%Y%m%d')
        log_file = self._log_dir / f'task_{today}.json'
        try:
            if log_file.exists():
                logs = json.loads(log_file.read_text(encoding='utf-8'))
                if task_type:
                    return [l for l in logs if l.get('task_type') == task_type]
                return logs
        except Exception:
            pass
        return []


# 全局单例（对标 xm-bot4 的 task_logger = TaskLogger()）
task_logger = TaskLogger()


def get_logger(name: str = None) -> logging.Logger:
    """获取指定名称的日志记录器（完整移植自 xm-bot4）

    Args:
        name: 日志记录器名称

    Returns:
        logging.Logger: 配置好的日志记录器
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    log_dir = Path('logs')
    log_dir.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(
        log_dir / f'{name}.log', encoding='utf-8'
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logger.setLevel(logging.INFO)
    file_handler.setLevel(logging.INFO)
    console_handler.setLevel(logging.INFO)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.propagate = False

    return logger
