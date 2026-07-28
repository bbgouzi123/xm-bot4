"""
时间解析工具（移植自 xm-bot4 utils/time_utils.py — 60行部分反编译）

原始文件: utils/time_utils.py (PARTIAL(5), 60 lines)
解析中文时间表达式（凌晨/上午/下午/昨天/星期X 等）。
反编译的 lambda 表达式已重建为正常方法。
"""
from datetime import datetime, timedelta
import re
from typing import Optional


class TimeParser:
    """时间解析器（从反编译的 lambda 表达式重建为正常类方法）"""

    @staticmethod
    def parse_time_period(time_str: str) -> Optional[datetime]:
        """解析时间段格式：'凌晨 3:00', '上午 10:30', '下午 2:00', '晚上 8:30'"""
        if not time_str:
            return None

        parts = time_str.strip().split()
        if len(parts) != 2:
            return None

        period = parts[0]
        time_part = parts[1]

        if ':' in time_part:
            hour, minute = map(int, time_part.split(':'))
        else:
            hour = int(time_part)
            minute = 0

        if not (0 <= hour <= 23):
            return None
        if not (0 <= minute <= 59):
            return None

        # 验证时间段范围
        period_ranges = {
            '凌晨': (0, 6),
            '上午': (6, 12),
            '中午': (12, 14),
            '下午': (14, 18),
            '晚上': (18, 24),
        }

        if period in period_ranges:
            low, high = period_ranges[period]
            if not (low <= hour < high):
                return None

        current_date = datetime.now()
        return current_date.replace(hour=hour, minute=minute, second=0, microsecond=0)

    @staticmethod
    def parse_relative_time(time_str: str) -> Optional[datetime]:
        """解析相对时间格式：'昨天 14:30', '前天 9:00'"""
        if not time_str:
            return None

        if '昨天' in time_str:
            base_time = datetime.now() - timedelta(days=1)
            time_str = time_str.replace('昨天', '').strip()
        elif '前天' in time_str:
            base_time = datetime.now() - timedelta(days=2)
            time_str = time_str.replace('前天', '').strip()
        else:
            return None

        try:
            hour, minute = map(int, time_str.split(':'))
        except (ValueError, TypeError):
            return None

        if not (0 <= hour <= 23):
            return None
        if not (0 <= minute <= 59):
            return None

        return base_time.replace(hour=hour, minute=minute, second=0, microsecond=0)

    @staticmethod
    def parse_weekday_time(time_str: str) -> Optional[datetime]:
        """解析星期格式：'星期一 14:30'"""
        week_map = {
            '一': 1, '二': 2, '三': 3, '四': 4,
            '五': 5, '六': 6, '日': 7, '天': 7,
        }

        if not time_str or not time_str.startswith('星期'):
            return None

        if len(time_str) < 3:
            return None

        target_weekday = week_map.get(time_str[2])
        if target_weekday is None:
            return None

        # 提取时间部分
        time_part = time_str[3:].strip()
        if not time_part or ':' not in time_part:
            return None

        try:
            hour, minute = map(int, time_part.split(':'))
        except (ValueError, TypeError):
            return None

        now = datetime.now()
        current_weekday = now.isoweekday()
        days_diff = current_weekday - target_weekday
        if days_diff <= 0:
            days_diff += 7

        target_date = now - timedelta(days=days_diff)
        return target_date.replace(hour=hour, minute=minute, second=0, microsecond=0)

    @staticmethod
    def parse_absolute_time(time_str: str) -> Optional[datetime]:
        """解析绝对时间格式：'2024年3月15日 14:30'"""
        pattern = r'^(\d{4})年(\d{1,2})月(\d{1,2})日\s+(\d{1,2}):(\d{2})$'
        match = re.match(pattern, time_str)
        if not match:
            return None

        try:
            year, month, day, hour, minute = map(int, match.groups())
            return datetime(year, month, day, hour, minute, 0)
        except (ValueError, TypeError):
            return None

    @classmethod
    def parse_time(cls, time_str: str) -> Optional[datetime]:
        """统一时间解析入口（依次尝试各种格式）"""
        if not time_str:
            return None

        time_str = time_str.strip()

        # 绝对时间
        result = cls.parse_absolute_time(time_str)
        if result:
            return result

        # 相对时间（昨天/前天）
        result = cls.parse_relative_time(time_str)
        if result:
            return result

        # 星期格式
        result = cls.parse_weekday_time(time_str)
        if result:
            return result

        # 时间段格式（上午/下午等）
        result = cls.parse_time_period(time_str)
        if result:
            return result

        return None
