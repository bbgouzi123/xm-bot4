import logging

logger = logging.getLogger(__name__)

class CleanupMixin:
    """缓存清理与声音播放辅助逻辑"""

    def _cleanup_stale_caches(self, now: float):
        """清理过期的内存缓存，防止长时间运行导致 OOM"""
        try:
            # 清理过期的人工干预记录（1 小时后强制清除）
            expired = [k for k, v in self._manual_interventions.items() if now - v > 3600]
            for k in expired:
                self._manual_interventions.pop(k, None)
            # 限制 _fingerprints 总会话数
            if len(self._fingerprints) > 500:
                for k in list(self._fingerprints.keys())[:-300]:
                    self._fingerprints.pop(k, None)
            # 限制 _initialized 集合大小
            if len(self._initialized) > 1000:
                self._initialized = set(list(self._initialized)[-500:])
            # 清理过期的悬疑消息
            for k in [k for k, v in self._suspicious_pending.items() if now - v.get("time", 0) > 120]:
                self._suspicious_pending.pop(k, None)
            if expired or len(self._fingerprints) > 400:
                logger.debug(f"[监控] 缓存清理: fp={len(self._fingerprints)} init={len(self._initialized)}")
        except Exception:
            pass

    def _play_notification_sound(self):
        """播放程序自定义的提示音效（后端已屏蔽，统一由前端网页播放，防止音效混合重叠）"""
        pass
