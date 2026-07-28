from fastapi import APIRouter
from pydantic import BaseModel
from typing import List, Optional
from src.utils.db_manager import WeChatDBManager
from src.utils.response import ok, err

router = APIRouter(prefix="/api/keyword-replies", tags=["keyword-replies"])

class KeywordReplyCreate(BaseModel):
    keywords: List[str]
    match_type: str  # 'exact' 或 'fuzzy'
    reply_content: str
    is_active: Optional[bool] = True
    scope: Optional[str] = "all"  # 'all', 'friend', 'group'
    delete_on_reply: Optional[bool] = False

class KeywordReplyUpdate(BaseModel):
    keywords: Optional[List[str]] = None
    match_type: Optional[str] = None
    reply_content: Optional[str] = None
    is_active: Optional[bool] = None
    scope: Optional[str] = None
    delete_on_reply: Optional[bool] = None

@router.get("/list")
async def get_keyword_replies():
    """获取所有关键词自动回复规则列表"""
    db = WeChatDBManager()
    replies = db.get_all_keyword_replies()
    return ok({"replies": replies})

@router.post("/create")
async def create_keyword_reply(data: KeywordReplyCreate):
    """创建全新的关键词自动回复规则"""
    if not data.keywords or not [k for k in data.keywords if k.strip()]:
        return err(40001, "关键词列表不能为空")
    if data.match_type not in ["exact", "fuzzy"]:
        return err(40002, "匹配类型无效，必须为 exact 或 fuzzy")
    if not data.reply_content.strip():
        return err(40003, "自动回复内容不能为空")
    if data.scope not in ["all", "friend", "group", "moment"]:
        return err(40005, "适用范围无效，必须为 all, friend, group 或 moment")

    db = WeChatDBManager()
    item = db.add_keyword_reply(
        keywords=data.keywords,
        match_type=data.match_type,
        reply_content=data.reply_content,
        is_active=data.is_active,
        scope=data.scope,
        delete_on_reply=bool(data.delete_on_reply)
    )
    return ok({"reply": item, "message": "关键词回复规则创建成功！"})

@router.post("/update/{reply_id}")
async def update_keyword_reply(reply_id: str, data: KeywordReplyUpdate):
    """更新已有的关键词自动回复规则"""
    db = WeChatDBManager()
    
    # 提取传来的更新字段
    updates = {}
    if data.keywords is not None:
        updates["keywords"] = data.keywords
    if data.match_type is not None:
        if data.match_type not in ["exact", "fuzzy"]:
            return err(40002, "匹配类型无效，必须为 exact 或 fuzzy")
        updates["match_type"] = data.match_type
    if data.reply_content is not None:
        if not data.reply_content.strip():
            return err(40003, "自动回复内容不能为空")
        updates["reply_content"] = data.reply_content
    if data.is_active is not None:
        updates["is_active"] = data.is_active
    if data.scope is not None:
        if data.scope not in ["all", "friend", "group", "moment"]:
            return err(40005, "适用范围无效，必须为 all, friend, group 或 moment")
        updates["scope"] = data.scope
    if data.delete_on_reply is not None:
        updates["delete_on_reply"] = data.delete_on_reply

    item = db.update_keyword_reply(reply_id, updates)
    if item:
        return ok({"reply": item, "message": "规则已成功更新！"})
    return err(40004, "无效的规则ID，该回复规则可能已被清理")

@router.delete("/{reply_id}")
async def delete_keyword_reply(reply_id: str):
    """删除指定的关键词回复规则"""
    db = WeChatDBManager()
    db.delete_keyword_reply(reply_id)
    return ok({"message": "关键词回复规则已成功移除"})


# ======= 身份智能引导分流拉群 API =======

class IdentityRoutingRule(BaseModel):
    keywords: List[str]
    tag_name: str
    group_name: str
    backup_group_name: Optional[str] = ""
    join_method: Optional[str] = "qrcode"  # "qrcode" 或 "invite"
    qrcode_path: Optional[str] = ""        # 本地二维码图片路径

class TagMappingRule(BaseModel):
    ai_category: str
    ai_subcategory: str
    match_type: str
    match_value: str
    wx_tag_name: str

class IdentityRoutingConfig(BaseModel):
    enabled: bool
    ask_prompt: str
    fallback_prompt: str
    invite_success_reply: Optional[str] = ""
    invite_fail_reply: Optional[str] = ""
    continuous_detection: Optional[bool] = False
    rules: List[IdentityRoutingRule]
    tag_mappings: Optional[List[TagMappingRule]] = []

@router.get("/identity-routing")
async def get_identity_routing():
    """获取新友智能引导与分流拉群配置"""
    db = WeChatDBManager()
    return ok(db.get_identity_routing())

@router.post("/identity-routing")
async def update_identity_routing(data: IdentityRoutingConfig):
    """更新新友智能引导与分流拉群配置"""
    db = WeChatDBManager()
    cfg = db.update_identity_routing(data.dict())
    return ok({"config": cfg, "message": "新友分流拉群配置更新成功！"})
