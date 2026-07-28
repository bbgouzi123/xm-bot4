"""
xm-bot4引擎：多维度日计数器

设计：
    - 支持任意维度的每日计数（点赞/评论/发圈/加友/自动回复等）
    - 每个维度独立配置上限
    - 日期切换自动归零
    - 内存高速缓存 + 后端数据库唯一真相源
    - increment 后立即异步上报到后端，确保实时同步

数据架构（v2 简化版）：
    - 唯一真相源：后端数据库（通过 cloud_sync 访问）
    - 内存缓存：仅用于 can_do() 秒级配额判断
    - 已移除：本地 daily_stats.json 文件（易导致多源数据不一致）
"""

import datetime
import logging
import threading
from typing import Dict

logger = logging.getLogger(__name__)


# 默认每日上限（可被用户配置覆盖）
# 注意：这些是"安全保底"默认值，用户可在前端仪表盘自定义调整。
DEFAULT_DAILY_LIMITS = {
    "like":          150,   # 朋友圈点赞（最安全的操作，微信日常用户一天点赞更多）
    "comment":       80,    # 朋友圈评论（中等风险，但正常社交也有大量评论）
    "moment_post":   10,    # 发朋友圈（微信建议每天不超过8-12条）
    "add_friend":    50,    # 添加好友（高风险操作，成熟号日加40-60是安全阈值）
    "auto_reply":    500,   # 自动回复（被动操作，风险低）
    "group_message": 200,   # 群消息回复（被动操作，风险低）
    "total_tokens":  -1,    # 总消耗 Token 数（仅用于展示，默认无上限）
}


class DailyCounter:
    """
    多维度日计数器 — 基于内存的安全自动化频率守卫

    v2 架构：
        - 唯一真相源 = 后端数据库
        - 内存 = 高速缓存（仅用于 can_do 秒级判断）
        - 每次 increment 后立即异步上报到后端

    使用方式:
        counter = DailyCounter()
        if counter.can_do("like", account_id):
            counter.increment("like", account_id)
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, custom_limits=None):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, custom_limits: Dict[str, int] = None):
        if self._initialized:
            return
        self._initialized = True
        self._limits = DEFAULT_DAILY_LIMITS.copy()
        if custom_limits:
            self._limits.update(custom_limits)
        # 内存计数器: {"account_id:dimension:date": count}
        self._counters: Dict[str, int] = {}
        self._loaded_accounts = set()
        self._rw_lock = threading.RLock()

    def _ensure_loaded(self, account_id: str):
        """冷启动时从后端数据库拉取今日用量恢复内存"""
        if account_id in self._loaded_accounts:
            return
        with self._rw_lock:
            if account_id in self._loaded_accounts:
                return
            self._loaded_accounts.add(account_id)

        today = datetime.datetime.now().strftime('%Y%m%d')

        # 从后端拉取今日用量恢复内存
        try:
            from src.utils.cloud_sync import get_cloud_client
            cloud = get_cloud_client()
            cloud_dims = cloud.pull_today_usage(account_id)
            if cloud_dims:
                with self._rw_lock:
                    for dim, report in cloud_dims.items():
                        if isinstance(report, dict):
                            c_count = report.get("count", 0)
                            k = f"{account_id}:{dim}:{today}"
                            self._counters[k] = max(self._counters.get(k, 0), c_count)
                logger.info(f"[计数器] 从后端恢复 {account_id} 今日用量: {len(cloud_dims)} 个维度")
        except Exception as e:
            logger.debug(f"[计数器] 从后端恢复用量异常（静默降级）: {e}")

    def _get_key(self, dimension: str, account_id: str = "main") -> str:
        """生成今日的内存 key"""
        today = datetime.datetime.now().strftime('%Y%m%d')
        return f"{account_id}:{dimension}:{today}"

    def get_count(self, dimension: str, account_id: str = "main") -> int:
        """获取某维度今日已操作次数"""
        if not account_id:
            account_id = "main"
        self._ensure_loaded(account_id)
        key = self._get_key(dimension, account_id)
        with self._rw_lock:
            return self._counters.get(key, 0)

    def get_limit(self, dimension: str) -> int:
        """获取某维度的每日上限"""
        return self._limits.get(dimension, 999999)

    def get_remaining(self, dimension: str, account_id: str = "main") -> int:
        """获取某维度今日剩余配额"""
        current = self.get_count(dimension, account_id)
        limit = self.get_limit(dimension)
        return max(0, limit - current)

    def can_do(self, dimension: str, account_id: str = "main") -> bool:
        """检查某维度今日是否还有配额

        这是最常用的入口方法，在执行任何自动化操作前调用。
        """
        return self.get_remaining(dimension, account_id) > 0

    def increment(self, dimension: str, account_id: str = "main", amount: int = 1) -> int:
        """增加某维度的计数，返回更新后的值，并立即异步上报到后端"""
        if not account_id:
            account_id = "main"
        self._ensure_loaded(account_id)
        key = self._get_key(dimension, account_id)

        with self._rw_lock:
            current = self._counters.get(key, 0)
            new_count = current + amount
            self._counters[key] = new_count

        # 立即异步上报到后端（非阻塞）
        threading.Thread(
            target=self._report_to_backend,
            args=(account_id,),
            daemon=True,
            name=f"usage-report-{dimension}"
        ).start()

        limit = self.get_limit(dimension)
        remaining = max(0, limit - new_count) if limit != -1 else -1

        logger.info(
            f"[计数器] {account_id}/{dimension} +{amount} → {new_count}/{'∞' if limit == -1 else limit} "
            f"(剩余 {'∞' if limit == -1 else remaining})"
        )

        # 接近上限时发出警告
        if remaining <= 3 and remaining > 0:
            logger.warning(
                f"[计数器⚠️] {account_id}/{dimension} 配额即将耗尽！"
                f"剩余 {remaining} 次"
            )
        elif remaining == 0:
            logger.warning(
                f"[计数器🛑] {account_id}/{dimension} 今日配额已耗尽！"
                f"后续操作将被跳过"
            )

        return new_count

    def _report_to_backend(self, account_id: str):
        """立即将当前内存用量上报到后端数据库"""
        try:
            from src.utils.cloud_sync import get_cloud_client
            cloud = get_cloud_client()
            cloud.report_usage(account_id)
        except Exception as e:
            logger.debug(f"[计数器] 异步上报后端异常（不影响主流程）: {e}")

    def get_all_stats(self, account_id: str = "main") -> Dict[str, dict]:
        """获取所有维度的今日统计（用于前端仪表盘）"""
        stats = {}
        # 确保默认维度都在
        for dim, limit in self._limits.items():
            count = self.get_count(dim, account_id)
            remaining = max(0, limit - count) if limit != -1 else -1
            percentage = round(count / limit * 100) if limit > 0 else 0
            stats[dim] = {
                "count": count,
                "limit": limit,
                "remaining": remaining,
                "percentage": min(100, percentage),
            }

        # 补充内存中可能存在的自定义动态维度
        today = datetime.datetime.now().strftime('%Y%m%d')
        prefix = f"{account_id}:"
        with self._rw_lock:
            for k, v in self._counters.items():
                if k.startswith(prefix) and k.endswith(f":{today}"):
                    parts = k.split(":")
                    if len(parts) >= 3:
                        dim = parts[1]
                        if dim not in stats:
                            stats[dim] = {
                                "count": v,
                                "limit": -1,
                                "remaining": -1,
                                "percentage": 0,
                            }
        return stats

    def update_limits(self, new_limits: Dict[str, int]):
        """动态更新上限配置（用户从前端修改后调用）"""
        for dim, val in new_limits.items():
            if isinstance(val, int) and val >= 0:
                self._limits[dim] = val
                logger.info(f"[计数器] 上限更新: {dim} → {val}")

    # ===== 兼容旧接口 =====

    def get_today_count(self, account_id: str) -> int:
        """兼容旧版：获取今日好友添加数"""
        return self.get_count("add_friend", account_id)

    def increment_count(self, account_id: str) -> int:
        """兼容旧版：增加好友添加计数"""
        return self.increment("add_friend", account_id)


# 全局单例
_chat_daily_counter = DailyCounter()