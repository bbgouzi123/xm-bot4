import random
import logging

logger = logging.getLogger(__name__)

def resolve_target_industry(account_id: str, batch_seed: str, current_offset: int) -> str:
    """在 xm-bot4系统 推广时，根据微信好友及队列分布加权随机选择目标行业"""
    target_industry_name = ""
    try:
        from src.crm.industry_config import IndustryConfigManager
        icm = IndustryConfigManager(account_id=account_id)
        profiles = icm.get_all_profiles()
        other_names = [
            p.name for p in profiles 
            if p.name != "xm-bot4系统" and p.id != "sys_001" and p.name
        ]
        if other_names:
            # 1. 统计当前微信好友的行业分布 (通过标签)
            industry_counts = {}
            try:
                from src.utils.contacts_cache import contacts_cache
                friends = contacts_cache.get_friends(account_id) or []
                for f in friends:
                    tag = f.get("tag", "") or ""
                    for name in other_names:
                        if name in tag:
                            industry_counts[name] = industry_counts.get(name, 0) + 1
            except Exception as ce:
                logger.warning(f"[日历引擎] 从 contacts_cache 统计行业分布失败: {ce}")

            # 2. 统计好友队列中已添加好友的行业分布
            try:
                from src.friend.friend_queue.stats import get_stats_by_industry
                stats = get_stats_by_industry()
                for row in stats:
                    name = row.get("industry_profile_name", "")
                    st = row.get("status", "")
                    cnt = row.get("cnt", 0)
                    if name in other_names and st == "added":
                        industry_counts[name] = industry_counts.get(name, 0) + cnt
            except Exception as fe:
                logger.warning(f"[日历引擎] 从 friend_queue 统计行业分布失败: {fe}")

            # 3. 构造权重列表
            weights = [industry_counts.get(name, 0) for name in other_names]
            total_weight = sum(weights)

            # 使用局部的 Random 实例防止全局 seed 污染
            local_rand = random.Random(f"{batch_seed}_{current_offset}")
            if total_weight > 0:
                # 按照权重进行加权随机选择
                target_industry_name = local_rand.choices(other_names, weights=weights, k=1)[0]
                logger.info(f"[日历引擎] 检测到“xm-bot4系统”推广，加权选择目标行业为：{target_industry_name} (总权重={total_weight}, 好友标签/队列分布={industry_counts})")
            else:
                # 队列为空或没有行业标签，退回纯随机
                target_industry_name = local_rand.choice(other_names)
                logger.info(f"[日历引擎] 检测到“xm-bot4系统”推广，无行业好友数据，退回纯随机选择目标行业为：{target_industry_name}")
    except Exception as e:
        logger.warning(f"[日历引擎] 随机目标行业生成失败: {e}")
    return target_industry_name
