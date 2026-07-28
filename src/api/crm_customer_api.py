"""
CRM 客户画像与标签 API 子路由 — 拆离自 crm_api.py 以满足 300 行限额红线
"""
from fastapi import APIRouter, Request
import logging
from src.utils.response import ok, err, ok_msg
from src.crm.account_data import ready_barrier

router = APIRouter()
logger = logging.getLogger(__name__)


def _get_profile_manager():
    """随 get_active_account() 切换；禁止缓存首次实例。"""
    from src.crm.profile_manager import ProfileManager
    return ProfileManager()


# ==================== 客户画像 ====================

@router.get("/api/crm/customers")
async def list_customers(limit: int = 50, tag: str = ""):
    """获取客户画像列表"""
    await ready_barrier.wait_until_ready()
    pm = _get_profile_manager()
    if tag:
        profiles = pm.search_by_tag(value_contains=tag)
    else:
        profiles = pm.get_all_profiles()

    # 转为前端友好的格式
    result = []
    for p in profiles[:limit]:
        result.append({
            "wxid": p.wxid,
            "nickname": p.nickname,
            "tags": p.get_tag_summary(),
            "tag_count": len(p.tags),
            "chat_count": p.chat_count,
            "last_chat": p.last_active,
            "created_at": p.first_contact,
        })

    return ok({
        "customers": result,
        "total": len(result),
    })


@router.get("/api/crm/customer/{wxid}")
async def get_customer_detail(wxid: str):
    """获取客户画像详情"""
    await ready_barrier.wait_until_ready()
    pm = _get_profile_manager()
    
    # 优先从通讯录缓存中匹配该好友的昵称，以在 get_profile 时触发临时画像平滑迁移/自愈
    nickname = None
    try:
        from src.crm.account_data import get_active_account
        from src.utils.contacts_cache import contacts_cache
        active_id = get_active_account()
        friends = contacts_cache.get_friends(active_id)
        nickname = next((f.get("name") or f.get("remark") for f in friends if f.get("wxid") == wxid), None)
    except Exception as e:
        logger.debug(f"[API] 匹配好友昵称异常: {e}")
        
    profile = pm.get_profile(wxid, nickname=nickname)
    if not profile:
        return err(40400, "客户不存在")

    # 详细的标签列表
    from src.crm.tag_manager import TagManager
    tag_list = []
    for t in profile.tags:
        tag_list.append({
            "category": t.category,
            "category_name": TagManager.get_category_name(t.category),
            "subcategory": t.subcategory,
            "subcategory_name": TagManager.get_subcategory_name(
                t.category, t.subcategory
            ),
            "value": t.value,
            "confidence": t.confidence,
            "source": t.source,
            "updated_at": getattr(t, "updated_at", None) or getattr(t, "updated", "") or "",
        })

    return ok({
        "customer": {
            "wxid": profile.wxid,
            "nickname": profile.nickname,
            "tags": tag_list,
            "tag_summary": profile.get_tag_summary(),
            "chat_count": profile.chat_count,
            "last_chat": profile.last_active,
            "notes": profile.notes,
            "created_at": profile.first_contact,
        },
    })


@router.post("/api/crm/customer/{wxid}/note")
async def add_customer_note(wxid: str, request: Request):
    """添加客户备注"""
    await ready_barrier.wait_until_ready()
    data = await request.json()
    note = data.get("note", "")
    pm = _get_profile_manager()
    profile = pm.add_note(wxid, note)
    if profile:
        return ok({"notes": profile.notes})
    return err(40400, "客户不存在")


@router.post("/api/crm/customer/{wxid}/summary")
async def generate_customer_summary(wxid: str):
    """一键生成 AI 客户摘要并分析画像（意向等级与画像标签）"""
    await ready_barrier.wait_until_ready()

    from src.utils.chat_history import ChatHistoryManager
    from src.crm.account_data import get_active_account
    import app.state as app_state
    import json
    import re

    account_id = get_active_account()
    history_mgr = ChatHistoryManager(account_id)

    # 1. 获取聊天上下文（最多 50 条消息）
    messages = history_mgr.get_context(wxid, window_size=50, max_chars=8000)
    if not messages:
        return err(40001, "暂无该联系人的聊天记录，无法生成 AI 摘要。")

    formatted_chat = ""
    for msg in messages:
        role_name = "客户" if msg["role"] == "user" else "我方"
        formatted_chat += f"{role_name}: {msg['content']}\n"

    # 2. 检查 AI 模块配置
    if not app_state.ai_service or not app_state.ai_service.is_configured():
        return err(50001, "未配置 AI 服务通道，请在系统设置中配置后再试。")

    # 3. 制定 Prompt 并调用 AI
    system_prompt = (
        "你是一个专业的客户关系管理(CRM)助手。请分析以下给出的微信聊天记录上下文，并以 JSON 格式输出该客户的画像洞察。\n"
        "输出的 JSON 必须严格包含以下字段，键名必须完全相同：\n"
        "1. \"summary\": 1-2句话简明扼要地总结该客户最新的核心诉求、购买意愿以及当前跟进状态。\n"
        "2. \"intent_level\": 客户意向等级，必须且只能为以下三个字母之一：\n"
        "   - \"A\": 意向强烈（明确有购买/付费/合作意愿、咨询下单支付方式）\n"
        "   - \"B\": 普通咨询（对产品功能、方案表现出兴趣，正在了解和对比中）\n"
        "   - \"C\": 意向微弱或无意向（闲聊、打招呼、已拒绝、仅加好友无实质对话）\n"
        "3. \"tags\": 数组类型，包含1到5个最能代表该客户特性的短标签（例如：[\"关注价格\", \"咨询bot4\", \"代理合作\"]）。\n\n"
        "请仅输出合法的 JSON 字符串，不要包含任何 Markdown 格式标记（如 ```json）或多余的解释文本。"
    )
    user_prompt = f"以下是与客户的聊天记录：\n\n{formatted_chat}"
    message_payload = f"{system_prompt}\n\n---\n\n{user_prompt}"

    result = await app_state.ai_service.start_chat(
        agent_id="",
        message=message_payload,
        session_id=f"crm_summary_{wxid}",
        cache_session=False
    )

    if not result.get("success") or not result.get("content"):
        return err(50002, f"AI 分析失败: {result.get('error', '未知错误')}")

    content = result["content"].strip()
    json_match = re.search(r'\{.*\}', content, re.DOTALL)
    if not json_match:
        return err(50003, f"AI 返回的格式不正确: {content}")

    try:
        parsed = json.loads(json_match.group(0))
    except Exception as ex:
        return err(50003, f"AI 返回解析 JSON 失败: {content}, Error: {ex}")

    summary_text = parsed.get("summary", "")
    intent_val = parsed.get("intent_level", "C")
    tags_val = parsed.get("tags", [])

    # 4. 将意向等级映射成系统的分类标签
    intent_map = {
        "A": "意向-强烈",
        "B": "意向-中等",
        "C": "意向-观望"
    }
    system_intent = intent_map.get(intent_val, "意向-观望")

    # 5. 更新本地 CRM 画像
    pm = _get_profile_manager()
    profile = pm.get_profile(wxid)
    profile.conversation_summary = summary_text

    # 手动构造 TagEntry 列表进行合并
    from src.crm.tag_manager import TagEntry
    new_tag_entries = [
        TagEntry(category="business", subcategory="intent", value=system_intent, confidence=0.9, source="chat")
    ]
    for t in tags_val:
        new_tag_entries.append(
            TagEntry(category="business", subcategory="need", value=t, confidence=0.8, source="chat")
        )

    pm.update_tags(wxid, new_tag_entries, source="chat")

    # 再次保存，确保 summary 保存成功并推送到同步云端
    profile.conversation_summary = summary_text
    pm.save_profile(profile)

    # 6. 转成前端友好的返回值
    from src.crm.tag_manager import TagManager
    tag_list = []
    for t in profile.tags:
        tag_list.append({
            "category": t.category,
            "category_name": TagManager.get_category_name(t.category),
            "subcategory": t.subcategory,
            "subcategory_name": TagManager.get_subcategory_name(t.category, t.subcategory),
            "value": t.value,
            "confidence": t.confidence,
            "source": t.source,
            "updated_at": getattr(t, "updated_at", None) or getattr(t, "updated", "") or "",
        })

    return ok({
        "customer": {
            "wxid": profile.wxid,
            "nickname": profile.nickname,
            "tags": tag_list,
            "tag_summary": profile.get_tag_summary(),
            "chat_count": profile.chat_count,
            "last_chat": profile.last_active,
            "notes": profile.notes,
            "created_at": profile.first_contact,
        }
    })


@router.delete("/api/crm/customer/{wxid}")
async def delete_customer(wxid: str):
    """删除客户画像"""
    await ready_barrier.wait_until_ready()
    pm = _get_profile_manager()
    success = pm.delete_profile(wxid)
    if success:
        return ok_msg("删除成功")
    return err(40400, "客户不存在")


# ==================== 标签体系 ====================

@router.get("/api/crm/tag-system")
async def get_tag_system():
    """获取完整的标签分类体系"""
    from src.crm.tag_manager import TAG_CATEGORIES

    categories = {}
    for cat_id, cat_info in TAG_CATEGORIES.items():
        subcats = {}
        for sub_id, sub_name in cat_info["subcategories"].items():
            subcats[sub_id] = sub_name
        categories[cat_id] = {
            "name": cat_info["name"],
            "icon": cat_info["icon"],
            "subcategories": subcats,
        }

    return ok({"categories": categories})
