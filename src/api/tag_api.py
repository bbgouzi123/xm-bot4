import logging
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from src.utils.db_manager import WeChatDBManager
from src.utils.response import ok, err

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tags", tags=["tags"])

class TagCreate(BaseModel):
    name: str
    color: Optional[str] = "brand"

@router.get("/list")
async def get_tags():
    """获取所有的客户标签池数据（手工系统配置表 + 聊天AI画像标签 + 朋友圈微信标签）"""
    db = WeChatDBManager()
    manual_tags = list(db.get_all_tags())
    
    # 获取系统已定义的标签名集合，用于去重
    manual_names = {t.get("name") for t in manual_tags if t.get("name")}
    all_merged_tags = list(manual_tags)
    
    # 1. 聊天过程中打的标签 (CRM画像)
    try:
        from src.crm.profile_manager import ProfileManager
        from src.crm.account_data import get_active_account
        active_aid = get_active_account()
        pm = ProfileManager(account_id=active_aid)
        profiles = pm.get_all_profiles()
        
        crm_names = set()
        for p in profiles:
            for t in p.tags:
                if t.value and t.value.strip():
                    crm_names.add(t.value.strip())
            if p.wx_synced_tags:
                for t in p.wx_synced_tags:
                    if t and t.strip():
                        crm_names.add(t.strip())
                        
        for name in sorted(crm_names):
            if name not in manual_names:
                import hashlib
                tag_id = f"crm_{hashlib.md5(name.encode('utf-8')).hexdigest()[:6]}"
                all_merged_tags.append({
                    "id": tag_id,
                    "name": name,
                    "color": "purple",  # CRM/AI识别出来的标签用紫色
                    "created_at": ""
                })
                manual_names.add(name)
    except Exception as e:
        logger.warning(f"[Tags] 获取聊天CRM画像标签失败: {e}")
        
    # 2. 朋友圈或通讯录打的标签 (微信原生标签)
    try:
        from src.utils.contacts_cache import contacts_cache
        from src.crm.account_data import get_active_account
        active_aid = get_active_account()
        wx_names = set()
        friends = contacts_cache.get_friends(active_aid)
        for f in friends:
            ftags = f.get("tags") or f.get("tag")
            if ftags:
                if isinstance(ftags, list):
                    for t in ftags:
                        if t and t.strip():
                            wx_names.add(t.strip())
                elif isinstance(ftags, str):
                    for t in ftags.split(","):
                        if t.strip():
                            wx_names.add(t.strip())
                            
        for name in sorted(wx_names):
            if name not in manual_names:
                import hashlib
                tag_id = f"wx_{hashlib.md5(name.encode('utf-8')).hexdigest()[:6]}"
                all_merged_tags.append({
                    "id": tag_id,
                    "name": name,
                    "color": "orange",  # 微信/朋友圈同步出的标签用橙色
                    "created_at": ""
                })
                manual_names.add(name)
    except Exception as e:
        logger.warning(f"[Tags] 获取微信朋友圈/通讯录标签失败: {e}")

    return ok({"tags": all_merged_tags})


@router.post("/create")
async def create_tag(data: TagCreate):
    """创建全新的业务人员定义标签或 AI 自动发现标签"""
    name = data.name.strip()
    if not name:
        return err(40001, "标签名不能为空")
    
    db = WeChatDBManager()
    # 查重
    existing = [t for t in db.get_all_tags() if t.get("name") == name]
    if existing:
        return err(40002, "该标签名称已经存在，无需重复建立")
        
    tag = db.add_tag(name, data.color)
    return ok({"tag": tag, "message": "新标签圈定成功！"})

@router.delete("/{tag_id}")
async def delete_tag(tag_id: str):
    """抹除不需要的旧标签体系"""
    db = WeChatDBManager()
    success = db.delete_tag(tag_id)
    if success:
        return ok({"message": "已成功移除该目标标签"})
    return err(40004, "无效的标签ID，可能已被系统清理")
