"""
xm-bot4引擎：朋友圈互动配置服务 — 碾压 xm-bot4 的硬编码防封参数

xm-bot4 把概率写死在代码里 (random.random() < 0.3)，
用户无法调整、开发者无法A/B测试。

我们的设计：
    - 所有防封参数可配置、可热更新
    - 动态概率衰减（越往后刷概率越低）
    - 心跳指纹延迟（正态分布+随机走神）
    - 互动去重指纹（同一朋友圈不重复互动）
    - 评论安全网（情感/敏感词/长度三层过滤）
"""

import json
import math
import random
import hashlib
import logging
import datetime
from typing import Dict, Optional, Any

logger = logging.getLogger(__name__)


# ===== 默认朋友圈互动配置 =====
DEFAULT_MOMENT_SETTINGS = {
    # 开关
    "like_enabled": True,
    "like_cover_enabled": False,
    "comment_enabled": True,
    "skip_self_moments": True,
    "skip_ads": True,
    "ignore_white": False,
    "ignore_black": False,
    # 不自动点赞/评论这些好友的朋友圈（与列表中的「发布者昵称」精确匹配；一行一个）
    "author_blacklist": [],

    # 基础概率（用于动态衰减的起始值）
    "like_probability": 0.30,
    "comment_probability": 0.15,

    # 巡游参数
    "scroll_count": 5,              # 每次巡游刷几屏
    "patrol_interval_min": 300,     # 巡游间隔最小秒数 (5分钟)
    "patrol_interval_max": 900,     # 巡游间隔最大秒数 (15分钟)

    # 每日上限（与 DailyCounter 联动）
    "daily_like_limit": 30,
    "daily_comment_limit": 10,
    "daily_moment_post_limit": 5,

    # 冷却时间（仿人类的停顿）
    "cooldown_after_like": 3,       # 点赞后等待秒数
    "cooldown_after_comment": 8,    # 评论后等待秒数（比点赞长——因为要"思考"）

    # AI 评论配置
    "ai_comment_agent_id": "",
    "comment_max_length": 40,       # 评论最大长度
    "comment_min_length": 4,        # 评论最小长度

    # 评论安全网
    "comment_sensitive_words": [
        "政治", "领导人", "反对", "抗议",
        "去世", "死亡", "葬礼", "悼念",
    ],

    # ===== 高级防封与过滤策略 =====
    "interaction_mode": "safe",     # safe (安全模式) / advanced (进阶模式)
    "cooling_hours": 48,            # 同一好友互动冷却期 (小时)
    "skip_ad_words": ["广告", "推广", "链接", "优惠", "券", "打折", "点击", "下单", "微商", "代理"],
    
    # 精准标签交互控制
    "moment_interact_tag_mode": "none",  # none (不按标签过滤) / white (白名单) / black (黑名单)
    "moment_interact_tags": [],          # 过滤好友所使用的微信标签列表
}


from src.utils.moment_filter import is_author_blacklisted, _match_global_contact_excludes


def _get_friend_by_author_name(author_name: str, account_id: str) -> dict:
    try:
        from src.utils.contacts_cache import contacts_cache
        friends = contacts_cache.get_friends(account_id)
        name = (author_name or "").strip()
        if not name:
            return None
        for f in friends:
            if (
                f.get("name") == name or 
                f.get("remark") == name or 
                f.get("display_name") == name or 
                f.get("wxid") == name
            ):
                return f
    except Exception:
        pass
    return None


def is_moment_interact_excluded(author_name: str, account_id: str) -> bool:
    """朋友圈不点赞/不评论名单：按账号隔离读取，支持黑/白名单模式与标签模式。
    
    安全修复：不再回退到全局 config.json，防止多账号场景下朋友圈互动名单串号。
    """
    configs = {}
    try:
        from src.api.config_api.privacy_shield import _get_reply_config_isolated
        configs = _get_reply_config_isolated(account_id)
    except Exception:
        # 配置读取失败时安全放行（不排除），避免功能中断
        return False

    settings = get_moment_settings(account_id)
    ignore_white = settings.get("ignore_white", False)
    ignore_black = settings.get("ignore_black", False)

    # ===== 1. 微信好友标签精准过滤 =====
    tag_mode = settings.get("moment_interact_tag_mode", "none")
    if tag_mode in ("white", "black"):
        allowed_tags = settings.get("moment_interact_tags", []) or []
        if tag_mode == "white" and not allowed_tags:
            # 标签白名单模式且配置 of 白名单标签为空
            if not ignore_white:
                return True
            
        friend = _get_friend_by_author_name(author_name, account_id)
        if friend:
            friend_tag = (friend.get("tag") or "").strip()
            # 支持英文逗号或中文逗号分隔的多个标签
            friend_tag_list = [t.strip() for t in friend_tag.replace("，", ",").split(",") if t.strip()]
            has_intersect = any(t in allowed_tags for t in friend_tag_list)
            
            if tag_mode == "white" and not has_intersect:
                if not ignore_white:
                    return True
            elif tag_mode == "black" and has_intersect:
                if not ignore_black:
                    return True
        else:
            # 没找到画像的联系人：白名单模式下安全起见排除，黑名单模式放行
            if tag_mode == "white":
                if not ignore_white:
                    return True

    # ===== 2. 原有的联系人黑/白名单过滤 =====
    mode = configs.get("moment_interact_friend_mode", "black")

    if mode == "white":
        excludes = configs.get("moment_interact_friend_whitelist", []) or []
        if not excludes:
            # 白名单为空意味着"只对白名单中的人互动"，但名单为空则应当全部跳过
            if not ignore_white:
                return True
        else:
            matched = _match_global_contact_excludes(author_name, account_id, excludes)
            if not matched and not ignore_white:
                return True
        return False
    else:
        excludes = configs.get("moment_interact_friend_excludes", []) or []
        if excludes:
            matched = _match_global_contact_excludes(author_name, account_id, excludes)
            if matched and not ignore_black:
                return True
        else:
            # 兼容旧版本 settings.author_blacklist
            settings = get_moment_settings(account_id)
            matched = is_author_blacklisted(author_name, settings)
            if matched and not ignore_black:
                return True
        return False




def get_moment_settings(account_id: str = None) -> dict:
    """获取朋友圈互动配置（内存缓存优先，带本地容灾，多账号隔离）"""
    target_wxid = account_id
    if not target_wxid:
        target_wxid = "main"
        # 如果有 driver_mock，尽量获取真实微信 id
        try:
            from src.api.config_api import _driver
            if _driver and hasattr(_driver, 'bot_wxid') and _driver.bot_wxid:
                target_wxid = _driver.bot_wxid
        except:
            pass

    result = DEFAULT_MOMENT_SETTINGS.copy()
    try:
        from src.utils.config_cache import config_cache
        import os
        from src.crm.account_data import get_account_data_dir
        
        cache_key = f"moment_settings_{target_wxid}"
        cached = config_cache.get(cache_key)
        
        # 本地读取兜底
        path = os.path.join(get_account_data_dir(target_wxid), "moment_settings.json")
        if not cached and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached:
                config_cache.set(cache_key, cached, sync_cloud=False)

        if cached and isinstance(cached, dict):
            result.update(cached)
    except Exception as e:
        logger.warning(f"读取朋友圈配置异常: {e}")

    # ===== 微信风控限额安全锁 =====
    mode = result.get("interaction_mode", "safe")
    if mode == "safe":
        result["daily_like_limit"] = min(int(result.get("daily_like_limit", 30)), 30)
        result["daily_comment_limit"] = min(int(result.get("daily_comment_limit", 10)), 10)
    elif mode == "advanced":
        result["daily_like_limit"] = min(int(result.get("daily_like_limit", 80)), 80)
        result["daily_comment_limit"] = min(int(result.get("daily_comment_limit", 30)), 30)
    elif mode == "flagship":
        result["daily_like_limit"] = min(int(result.get("daily_like_limit", 1500)), 1500)
        result["daily_comment_limit"] = min(int(result.get("daily_comment_limit", 500)), 500)
    else:
        result["daily_like_limit"] = min(int(result.get("daily_like_limit", 1500)), 1500)
        result["daily_comment_limit"] = min(int(result.get("daily_comment_limit", 500)), 500)

    return result


def save_moment_settings(config: dict, account_id: str = None):
    """保存朋友圈互动配置（内存缓存 + 同步后端持久化 + 本地落盘隔离）"""
    target_wxid = account_id
    if not target_wxid:
        target_wxid = "main"
        try:
            from src.api.config_api import _driver
            if _driver and hasattr(_driver, 'bot_wxid') and _driver.bot_wxid:
                target_wxid = _driver.bot_wxid
        except:
            pass

    try:
        from src.utils.config_cache import config_cache
        import os
        from src.crm.account_data import get_account_data_dir
        
        cache_key = f"moment_settings_{target_wxid}"
        config_cache.set(cache_key, config)
        
        path = os.path.join(get_account_data_dir(target_wxid), "moment_settings.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

        logger.info(f"[朋友圈配置] 已保存({target_wxid}): 点赞={config.get('like_probability')}, "
                    f"评论={config.get('comment_probability')}")
    except Exception as e:
        logger.error(f"[朋友圈配置] 保存失败: {e}")
        raise


# ===== 碾压级功能 1：动态概率衰减引擎 =====

def get_decayed_probability(base_prob: float, scroll_position: int) -> float:
    """真人刷圈越往后越懒，概率指数衰减

    xm-bot4 的概率始终固定——第1条和第50条概率一样，机器行为特征明显。
    我们模拟真人：前几屏认真看（概率高），后面就划水了（概率低）。

    Args:
        base_prob: 基础概率（如 0.3）
        scroll_position: 当前第几屏（从 0 开始）

    Returns:
        衰减后的概率值
    """
    # 指数衰减：第0屏=100%, 第2屏≈55%, 第4屏≈30%, 第6屏≈16%
    decay = math.exp(-0.3 * scroll_position)
    # 加入随机抖动 ±20%，避免规律性
    jitter = random.uniform(0.8, 1.2)
    return base_prob * decay * jitter


# ===== 碾压级功能 2：心跳指纹模拟器 =====

def human_delay(action_type: str) -> float:
    """模拟真人操作的时间分布（正态分布 + 偶尔走神）

    xm-bot4 用 random.uniform(3, 6)，范围固定——微信反作弊可检测。
    我们用正态分布 + 走神机制，让操作间隔的统计分布更接近真人。

    Args:
        action_type: 操作类型

    Returns:
        延迟秒数
    """
    # 不同操作类型有不同的自然节奏
    profiles = {
        "like":    {"mu": 2.0, "sigma": 1.5, "min": 0.5, "max": 8.0},
        "comment": {"mu": 15.0, "sigma": 8.0, "min": 5.0, "max": 45.0},
        "scroll":  {"mu": 3.0, "sigma": 2.0, "min": 0.8, "max": 12.0},
        "typing":  {"mu": 1.5, "sigma": 0.8, "min": 0.3, "max": 5.0},
        "read":    {"mu": 5.0, "sigma": 3.0, "min": 1.0, "max": 20.0},
    }

    p = profiles.get(action_type, {"mu": 3.0, "sigma": 2.0, "min": 1.0, "max": 10.0})

    # 正态分布 + 截断
    delay = max(p["min"], min(p["max"], random.gauss(p["mu"], p["sigma"])))

    # 5% 概率插入长停顿（模拟刷手机时走神、看别的消息）
    if random.random() < 0.05:
        distraction = random.uniform(10, 30)
        logger.debug(f"[心跳模拟] 插入走神停顿: {distraction:.1f}秒")
        delay += distraction

    return delay


# ===== 互动去重（已拆分到 moment_dedup.py）=====
# 向后兼容 re-export
from src.utils.moment_dedup import (                    # noqa: F401
    generate_moment_fingerprint,
    has_interacted,
    record_interaction,
)


# ===== 碾压级功能 4：评论安全网 =====

def validate_comment(comment: str, post_content: str, settings: dict = None) -> tuple:
    """三层评论安全检查

    xm-bot4 让 AI 生成评论后直接发出，没有任何安全校验。
    我们提供三层过滤：敏感词 / 长度 / 基础情感匹配。

    Args:
        comment: AI 生成的评论
        post_content: 原始朋友圈内容
        settings: 配置（可选）

    Returns:
        (is_safe: bool, reason: str, cleaned_comment: str)
    """
    if not settings:
        settings = DEFAULT_MOMENT_SETTINGS

    max_len = settings.get("comment_max_length", 40)
    min_len = settings.get("comment_min_length", 4)
    sensitive_words = settings.get("comment_sensitive_words", [])

    # 清理评论
    cleaned = comment.strip()
    # 去掉可能的引号包裹
    if cleaned.startswith('"') and cleaned.endswith('"'):
        cleaned = cleaned[1:-1]
    if cleaned.startswith("'") and cleaned.endswith("'"):
        cleaned = cleaned[1:-1]
    cleaned = cleaned.strip()

    # 第一层：长度校验
    if len(cleaned) < min_len:
        return False, f"评论太短({len(cleaned)}字 < {min_len}字)", cleaned
    if len(cleaned) > max_len:
        # 不直接拒绝，截断到合适长度
        cleaned = cleaned[:max_len]
        logger.debug(f"[评论安全网] 文本超长，已截断至{max_len}字")

    # 第二层：敏感词过滤
    for word in sensitive_words:
        if word in cleaned:
            return False, f"包含敏感词「{word}」", cleaned

    # 第三层：基础情感匹配
    # 检查朋友圈内容是否包含丧事关键词
    sad_keywords = ["去世", "离开", "走了", "一路走好", "天堂", "怀念", "悼念",
                    "RIP", "安息", "逝世", "离世"]
    is_sad_post = any(kw in post_content for kw in sad_keywords)

    if is_sad_post:
        # 丧事帖子不应出现欢快评论
        happy_words = ["恭喜", "棒棒", "太好了", "开心", "哈哈", "厉害",
                       "🎉", "🎊", "👏", "太赞"]
        for hw in happy_words:
            if hw in cleaned:
                return False, f"丧事帖子出现欢快词「{hw}」", cleaned

    return True, "通过", cleaned
