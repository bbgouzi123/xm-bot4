"""
聊天知识库 Prompt 注入工具 — 从 Cloud 后端读取 Q&A 知识条目并格式化为 Prompt 段落

从 prompt_builder.py 拆出，保持构建器 < 300 行。
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


import time

# 记录因没有权限、未购买或 403 授权失败而不可用的销冠包 ID 及过期时间 {pkg_id: expire_time}
_UNAUTHORIZED_PACKAGES = {}
_UNAUTHORIZED_TTL = 600.0  # 10 分钟内不再重复尝试请求未购买的销冠包


def build_chat_knowledge_section(industry_id: str, wxid: str = None) -> str:
    """从聊天知识库中提取高质量参考话术，注入到 Prompt 中

    从 Cloud 后端读取当前行业下已启用的 Q&A 对以及当前激活的销冠话术包，
    按质量降序或相关性过滤 TOP 条，总字数不超过 2000。
    """
    # 检查全局开关
    try:
        from src.utils.config_cache import config_cache
        if not config_cache.get("use_chat_knowledge", True):
            logger.debug("[PromptBuilder] 聊天知识库已被用户关闭，跳过注入")
            return ""
    except Exception:
        pass

    active_sales_pkg_id = ""
    if wxid:
        try:
            from src.crm.account_data import get_account_settings
            active_sales_pkg_id = get_account_settings(wxid).get("sales_package_id", "")
        except Exception:
            pass

    if not active_sales_pkg_id:
        try:
            from src.utils.config_cache import config_cache
            configs = config_cache.get("global_api_config") or {}
            active_sales_pkg_id = configs.get("active_sales_package_id", "")
        except Exception:
            active_sales_pkg_id = ""

    if not industry_id and not active_sales_pkg_id:
        return ""

    try:
        from src.utils.cloud_sync.base import CloudSyncBaseMixin
        sync = CloudSyncBaseMixin()
        
        # 1. 加载激活的销冠包话术
        sales_items = []
        if active_sales_pkg_id:
            now = time.time()
            # 清理过期的不可用包记录
            for pid in list(_UNAUTHORIZED_PACKAGES.keys()):
                if _UNAUTHORIZED_PACKAGES[pid] < now:
                    _UNAUTHORIZED_PACKAGES.pop(pid, None)

            if active_sales_pkg_id in _UNAUTHORIZED_PACKAGES:
                logger.debug(f"[PromptBuilder] 销冠话术包 '{active_sales_pkg_id}' 已在不可用缓存中，跳过 API 请求")
            else:
                try:
                    res = sync._request(
                        "GET",
                        f"/api/v1/sales-market/packages/{active_sales_pkg_id}/entries?limit=15",
                        need_auth=True,
                        timeout=5,
                    )
                    if res is None:
                        status = getattr(sync, "last_status_code", None)
                        if status in (401, 403):
                            _UNAUTHORIZED_PACKAGES[active_sales_pkg_id] = now + _UNAUTHORIZED_TTL
                            logger.warning(f"[PromptBuilder] 话术包 '{active_sales_pkg_id}' 请求返回 401/403 授权错误，已被加入未授权/不可用缓存，{_UNAUTHORIZED_TTL}s内不再请求")
                        else:
                            logger.warning(f"[PromptBuilder] 话术包 '{active_sales_pkg_id}' 请求失败 (status={status})，暂不加入不可用缓存")
                    elif isinstance(res, list):
                        sales_items = res
                except Exception as se:
                    logger.debug(f"[PromptBuilder] 销冠话术包读取失败: {se}")

        # 2. 加载用户自己采集的行业话术
        items = []
        if industry_id:
            try:
                result = sync._request(
                    "GET",
                    f"/api/v1/chat-knowledge/entries?industry_id={industry_id}&enabled_only=true&page=1&page_size=15",
                    need_auth=True,
                    timeout=5,
                )
                if result and isinstance(result, dict):
                    items = result.get("items", [])
            except Exception as ie:
                logger.debug(f"[PromptBuilder] 行业采集话术读取失败: {ie}")

        if not sales_items and not items:
            return ""

        lines = ["\n## 【参考话术知识库（来自真实成交对话，请参考话术风格和应对策略）】"]
        total_chars = 0
        max_chars = 2000
        count = 0

        # 优先拼装高情商销冠话术
        for entry in sales_items:
            q = entry.get("question", "").strip()
            a = entry.get("answer", "").strip()
            if not q or not a:
                continue
            pair_len = len(q) + len(a)
            if total_chars + pair_len > max_chars:
                break
            lines.append(f"客户问：{q}")
            lines.append(f"销冠答：{a}\n")
            total_chars += pair_len
            count += 1

        # 拼装本地采集行业话术
        for entry in items:
            q = entry.get("question", "").strip()
            a = entry.get("answer", "").strip()
            if not q or not a:
                continue
            pair_len = len(q) + len(a)
            if total_chars + pair_len > max_chars:
                break
            lines.append(f"客户问：{q}")
            lines.append(f"销冠答：{a}\n")
            total_chars += pair_len
            count += 1

        if count == 0:
            return ""

        logger.info(f"[PromptBuilder] 注入聊天知识库 {count} 条参考话术 (激活包: {active_sales_pkg_id}, 行业: {industry_id})")
        return "\n".join(lines) + "\n"

    except Exception as e:
        logger.debug(f"[PromptBuilder] 聊天知识库读取失败（不影响核心功能）: {e}")
        return ""


def inject_sales_package_to_message(message: str, agent_id: str = "", image_model: str = "", video_model: str = "", wxid: str = None) -> str:
    """自动注入销冠话术包逻辑（适用于聊天回复、朋友圈评论等各类文本对话生成场景）"""
    try:
        # ⚠️ 修复：对于闲聊场景或明确写有降级回答铁律的 message，绝对禁止注入销冠推销话术，避免干扰大模型判定
        if any(kw in message for kw in ("群聊非业务话题防穿帮与降级回答铁律", "高情商社交规则", "CASUAL_PROMPT")):
            return message

        active_sales_pkg_id = ""
        if wxid:
            try:
                from src.crm.account_data import get_account_settings
                active_sales_pkg_id = get_account_settings(wxid).get("sales_package_id", "")
            except Exception:
                pass
        if not active_sales_pkg_id:
            from src.utils.config_cache import config_cache
            global_configs = config_cache.get("global_api_config") or {}
            active_sales_pkg_id = global_configs.get("active_sales_package_id", "")

        if active_sales_pkg_id:
            # 过滤多媒体模型生成
            if agent_id and (agent_id == image_model or agent_id == video_model):
                return message
            if "## 【参考话术知识库" not in message:
                sales_section = build_chat_knowledge_section("", wxid=wxid)
                if sales_section:
                    return f"{sales_section.strip()}\n\n---\n\n{message}"
    except Exception as e:
        import logging
        logging.getLogger("inject_sales").debug(f"自动注入销冠话术异常: {e}")
    return message

