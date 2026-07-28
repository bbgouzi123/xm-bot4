"""
每日计数器 — 从 xm-bot4 DailyCounter 逆向移植

功能:
    pass
- 按账号 + 日期维度统计每日添加好友数量
- 限制每日添加好友上限，防止触发风控
"""
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional


class DailyCounter:
    """每日计数器（对标 xm-bot4 DailyCounter）"""

    def __init__(self):
        self._file = Path.home() / ".xm-ai-bot" / "daily_friend_count.json"
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict = {}
        self._load_data()

    def _load_data(self):
        """加载计数数据"""
        try:
            if self._file.exists():
                self._data = json.loads(self._file.read_text(encoding="utf-8"))
                if not isinstance(self._data, dict):
                    self._data = {}
        except Exception:
            self._data = {}

    def _save_data(self):
        """保存计数数据"""
        try:
            self._file.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[DailyCounter] 保存失败: {e}")

    def get_today_count(self, account_id: str) -> int:
        """获取今天的添加好友数量"""
        today = datetime.now().strftime("%Y%m%d")
        account_data = self._data.get(account_id, {})
        day_data = account_data.get(today, {})
        return day_data.get("total", 0)

    def increment_count(self, account_id: str, amount: int = 1) -> int:
        """增加计数，返回更新后的值"""
        today = datetime.now().strftime("%Y%m%d")

        if account_id not in self._data:
            self._data[account_id] = {}

        if today not in self._data[account_id]:
            self._data[account_id][today] = {"total": 0}

        self._data[account_id][today]["total"] += amount
        self._save_data()

        return self._data[account_id][today]["total"]

    def can_add_friend(self, account_id: str, max_per_day: int = 20) -> bool:
        """检查是否还能添加好友"""
        return self.get_today_count(account_id) < max_per_day

    def get_remaining(self, account_id: str, max_per_day: int = 20) -> int:
        """获取今天剩余可添加数量"""
        current = self.get_today_count(account_id)
        return max(0, max_per_day - current)

    def get_history(self, account_id: str, days: int = 7) -> list:
        """获取最近几天的历史记录"""
        result = []
        account_data = self._data.get(account_id, {})
        for i in range(days):
            from datetime import timedelta
            date = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
            day_data = account_data.get(date, {})
            result.append({
                "date": date,
                "total": day_data.get("total", 0),
            })
        return result

    def reset_today(self, account_id: str):
        """重置今天的计数（调试用）"""
        today = datetime.now().strftime("%Y%m%d")
        if account_id in self._data and today in self._data[account_id]:
            self._data[account_id][today] = {"total": 0}
            self._save_data()

    def cleanup_old_data(self, keep_days: int = 30):
        """清理超过 N 天的旧数据"""
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=keep_days)).strftime("%Y%m%d")

        for account_id in list(self._data.keys()):
            account_data = self._data[account_id]
            old_keys = [k for k in account_data.keys() if k < cutoff]
            for k in old_keys:
                del account_data[k]

        self._save_data()
