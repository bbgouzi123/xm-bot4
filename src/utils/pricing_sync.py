"""
定价数据同步器 — 从 xm-user 拉取套餐定价，注入到 sys_001 行业话术

【设计决策】
定价数据的唯一真相源是 xm-user 的 plan_definitions 表。
本模块在 xm-bot4 启动时从 xm-user 拉取最新定价，
格式化为自然语言后追加到"xm-bot4系统"行业的知识库。
确保 AI 被问到价格时能给出精准报价，而非含糊其辞。

【数据流】
xm-user GET /api/plans?product=xm-bot4
    → 格式化为自然语言定价段落
    → 追加到 SYSTEM_TEMPLATES[sys_001].knowledge
    → PromptBuilder.build() 自动注入到 AI System Prompt
"""
import logging
import threading
import time
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

# 缓存：避免重复拉取
_cached_pricing_text: Optional[str] = None
_last_sync_time: float = 0
_SYNC_INTERVAL = 3600  # 每小时刷新一次


def fetch_plans_from_license() -> Optional[List[Dict[str, Any]]]:
    """从 xm-user 后端拉取 xm-bot4 产品的套餐定义

    Returns:
        套餐列表（按 sort_order 排序），失败返回 None
    """
    try:
        from src.utils.license_validator import SA_LICENSE_API
        from src.utils.http_client import XMClient
        from src.utils.const import PRODUCT_KEY

        client = XMClient(SA_LICENSE_API)
        result = client.post("/api/plans/_query", {"product": PRODUCT_KEY})

        if result and result.get("success") is True:
            plans = result.get("data", [])
            if isinstance(plans, list) and len(plans) > 0:
                logger.info(f"[定价同步] 从 xm-user 获取到 {len(plans)} 个套餐")
                return plans
            else:
                logger.warning("[定价同步] xm-user 返回空套餐列表")
                return None
        else:
            msg = result.get("message", "未知错误") if result else "无响应"
            logger.warning(f"[定价同步] xm-user 返回失败: {msg}")
            return None
    except Exception as e:
        logger.warning(f"[定价同步] 拉取套餐异常: {e}")
        return None


def format_plans_to_knowledge(plans: List[Dict[str, Any]]) -> str:
    """将套餐定价数据格式化为自然语言，供 AI 知识库使用

    Args:
        plans: xm-user 返回的套餐列表

    Returns:
        格式化后的定价知识文本
    """
    lines = [
        "",
        "【产品定价（官方最新价格，客户问价格时使用）】",
    ]

    for i, plan in enumerate(plans, 1):
        name = plan.get("name", "未知")
        code = plan.get("code", "")
        price_monthly_cents = plan.get("price_monthly", 0)
        price_yearly_monthly_cents = plan.get("price_yearly", 0)
        max_wechat = plan.get("max_wechat", 1)
        max_industries = plan.get("max_industries", 0)
        ai_daily_limit = plan.get("ai_daily_limit", 0)
        trial_days = plan.get("trial_days", 0)

        # 把分转换为元
        price_monthly_yuan = int(price_monthly_cents / 100)
        price_yearly_monthly_yuan = int(price_yearly_monthly_cents / 100)

        # 体验版/试用版特殊处理
        if code in ("trial", "free") or price_monthly_yuan == 0:
            trial_text = f"免费试用{trial_days}天" if trial_days > 0 else "免费"
            lines.append(
                f"  {_num_circle(i)} {name}：{trial_text}，"
                f"含{max_wechat}个号，可体验全部核心功能"
            )
        else:
            # 计算年付优惠
            price_text = f"月付{price_monthly_yuan}元"
            if price_yearly_monthly_yuan > 0:
                price_yearly_total = price_yearly_monthly_yuan * 12
                if price_yearly_monthly_yuan < price_monthly_yuan:
                    save_months = round(12 - (price_yearly_total / price_monthly_yuan), 1)
                    if save_months.is_integer():
                        save_months = int(save_months)
                    price_text += f"/年费{price_yearly_total}元（折合每月{price_yearly_monthly_yuan}元，省{save_months}个月）"
                else:
                    price_text += f"/年费{price_yearly_total}元"

            # 功能描述
            features_parts = []
            if max_wechat > 0:
                features_parts.append(f"{max_wechat}个号")
            if max_industries == 0 or max_industries >= 99:
                features_parts.append("无限行业")
            elif max_industries > 0:
                features_parts.append(f"{max_industries}个行业")
            if ai_daily_limit < 0 or ai_daily_limit >= 9999:
                features_parts.append("无限AI对话")
            elif ai_daily_limit > 0:
                features_parts.append(f"每日{ai_daily_limit}次AI对话")

            features_text = " + ".join(features_parts) if features_parts else ""
            line = f"  {_num_circle(i)} {name}：{price_text}"
            if features_text:
                line += f"，{features_text}"
            lines.append(line)

    # 报价话术指导
    lines.extend([
        "",
        "【报价话术策略】",
        "- 客户问价格时，先了解需求（管几个号、什么行业），再推荐合适套餐",
        "- 强调投入产出比：一个xm-bot4年费 < 一个真人销售一个月工资",
        "- 客户犹豫时，推荐先免费试用体验版，用效果说话",
        "- 绝不主动报最高价，根据客户规模推荐性价比最优方案",
    ])

    return "\n".join(lines)


def _num_circle(n: int) -> str:
    """数字转带圈序号：1→①, 2→②..."""
    circles = "①②③④⑤⑥⑦⑧⑨⑩"
    return circles[n - 1] if 1 <= n <= len(circles) else f"{n}."


def inject_pricing_to_sys001(force: bool = False) -> bool:
    """将定价知识注入到 sys_001（xm-bot4系统）的 knowledge 中

    Args:
        force: 是否强制刷新（忽略缓存时间）

    Returns:
        是否成功注入
    """
    global _cached_pricing_text, _last_sync_time

    # 缓存检查
    if not force and _cached_pricing_text and (time.time() - _last_sync_time < _SYNC_INTERVAL):
        return True  # 使用缓存，不重复拉取

    plans = fetch_plans_from_license()
    if not plans:
        if _cached_pricing_text:
            logger.info("[定价同步] xm-user 不可达，使用上次缓存的定价数据")
            return True
        logger.warning("[定价同步] 无法获取定价数据，AI 将无法精确报价")
        return False

    pricing_text = format_plans_to_knowledge(plans)
    _cached_pricing_text = pricing_text
    _last_sync_time = time.time()

    # 从套餐列表解析旗舰版最大多开微信号数量作为唯一数据源
    flagship_wechat = 10
    flagship_plan = next((p for p in plans if p.get("code") == "flagship"), None)
    if flagship_plan and flagship_plan.get("max_wechat") is not None:
        flagship_wechat = flagship_plan.get("max_wechat")
    else:
        valid_limits = [p.get("max_wechat") for p in plans if isinstance(p.get("max_wechat"), int)]
        if valid_limits:
            flagship_wechat = max(valid_limits)

    if flagship_wechat == -1:
        wechat_desc = "无限"
        wechat_desc_plus = "无限"
    else:
        wechat_desc = f"{flagship_wechat}号"
        wechat_desc_plus = f"{flagship_wechat}+个"

    # 注入到当前活跃的 IndustryConfigManager 中的 sys_001
    try:
        import re
        from src.crm.industry_config import IndustryConfigManager
        from src.crm.industry_config.templates import SYSTEM_TEMPLATES

        # 定价注入标记（用于识别和替换已注入的定价段落）
        PRICING_MARKER = "【产品定价（官方最新价格，客户问价格时使用）】"

        # 1. 更新内存内置系统模板，保持后续新增或重置的一致性
        for t in SYSTEM_TEMPLATES:
            if t.get("id") == "sys_001":
                t["selling_point"] = re.sub(r"\d+号多开", f"{wechat_desc}多开", t.get("selling_point", ""))
                k = t.get("knowledge", "")
                if PRICING_MARKER in k:
                    idx = k.index(f"\n{PRICING_MARKER}") if f"\n{PRICING_MARKER}" in k else k.index(PRICING_MARKER)
                    k = k[:idx].rstrip()
                k = re.sub(r"\d+号多开", f"{wechat_desc}多开", k)
                k = re.sub(r"\d+\+个微信号", f"{wechat_desc_plus}微信号", k)
                k = re.sub(r"\d+\+个号", f"{wechat_desc_plus}号", k)
                t["knowledge"] = k

        # 2. 更新当前生效的行业配置
        mgr = IndustryConfigManager(account_id="global")

        # 自动清理之前由于 Bug 重复克隆出的 "xm-bot4系统" 冗余行业配置
        redundant_ids = []
        for p_id, p_dict in list(mgr._custom_profiles_dict.items()):
            if p_id.startswith("profile_") and p_dict.get("name") == "xm-bot4系统":
                redundant_ids.append(p_id)
                mgr._custom_profiles_dict.pop(p_id, None)

        has_changed = len(redundant_ids) > 0
        updated_sys001 = False

        for p in mgr._profiles:
            if p.id == "sys_001":
                # 动态替换核心卖点中的多开微信号数量
                selling_point = p.selling_point or ""
                selling_point = re.sub(r"\d+号多开", f"{wechat_desc}多开", selling_point)

                # 动态替换知识库中的多开微信号数量
                knowledge = p.knowledge or ""
                if PRICING_MARKER in knowledge:
                    idx = knowledge.index(f"\n{PRICING_MARKER}") if f"\n{PRICING_MARKER}" in knowledge else knowledge.index(PRICING_MARKER)
                    knowledge = knowledge[:idx].rstrip()

                knowledge = re.sub(r"\d+号多开", f"{wechat_desc}多开", knowledge)
                knowledge = re.sub(r"\d+\+个微信号", f"{wechat_desc_plus}微信号", knowledge)
                knowledge = re.sub(r"\d+\+个号", f"{wechat_desc_plus}号", knowledge)

                # 追加最新定价
                new_knowledge = knowledge + "\n" + pricing_text

                # 直接保存到 sys_001 的覆写字典中，避免因调用 update_profile 克隆新行业
                p_dict = p.to_dict()
                p_dict["selling_point"] = selling_point
                p_dict["knowledge"] = new_knowledge
                mgr._custom_profiles_dict["sys_001"] = p_dict

                updated_sys001 = True
                has_changed = True
                break

        if has_changed:
            mgr._save()
            mgr._load()

        if updated_sys001:
            if redundant_ids:
                logger.info(f"[定价同步] 🧹 成功清理了 {len(redundant_ids)} 个冗余行业配置并更新了定价数据")
            else:
                logger.info(f"[定价同步] ✅ 已更新微信号限制({wechat_desc_plus})并将套餐定价同步注入到 sys_001 知识库")
            return True

        logger.warning("[定价同步] 未找到 sys_001 行业模板")
        return False
    except Exception as e:
        logger.error(f"[定价同步] 注入知识库与动态多开更新异常: {e}")
        return False


def start_background_sync():
    """启动后台定时同步线程（每小时刷新定价）"""

    def _sync_loop():
        # 首次同步延迟 10 秒（等 xm-user 启动就绪）
        time.sleep(10)
        while True:
            try:
                inject_pricing_to_sys001(force=True)
            except Exception as e:
                logger.error(f"[定价同步] 后台同步异常: {e}")
            time.sleep(_SYNC_INTERVAL)

    t = threading.Thread(target=_sync_loop, daemon=True, name="pricing-sync")
    t.start()
    logger.info("[定价同步] 后台定时同步已启动（每小时刷新）")
