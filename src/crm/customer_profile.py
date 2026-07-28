"""
客户画像实体类
"""
from typing import List, Optional
from .tag_manager import TagManager, TagEntry

class CustomerProfile:
    """单个客户的完整画像"""

    def __init__(self, wxid: str):
        self.wxid = wxid
        self.nickname = ""
        self.remark = ""            # 微信备注名
        self.region = ""            # 地区
        self.signature = ""         # 个性签名
        self.avatar_path = ""
        self.first_contact = ""     
        self.last_active = ""       
        self.chat_count = 0         
        self.tags: List[TagEntry] = []
        self.wx_synced_tags: List[str] = []  
        self.conversation_summary = ""  
        self.notes: List[str] = []  
        self.source = ""            

    def to_dict(self) -> dict:
        return {
            "wxid": self.wxid,
            "nickname": self.nickname,
            "remark": self.remark,
            "region": self.region,
            "signature": self.signature,
            "avatar_path": self.avatar_path,
            "first_contact": self.first_contact,
            "last_active": self.last_active,
            "chat_count": self.chat_count,
            "tags": [t.to_dict() for t in self.tags],
            "wx_synced_tags": self.wx_synced_tags,
            "conversation_summary": self.conversation_summary,
            "notes": self.notes,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CustomerProfile":
        profile = cls(d.get("wxid", ""))
        profile.nickname = d.get("nickname", "")
        profile.remark = d.get("remark", "")
        profile.region = d.get("region", "")
        profile.signature = d.get("signature", "")
        profile.avatar_path = d.get("avatar_path", "")
        profile.first_contact = d.get("first_contact", "")
        profile.last_active = d.get("last_active", "")
        profile.chat_count = d.get("chat_count", 0)
        profile.tags = [
            TagEntry.from_dict(t) for t in d.get("tags", [])
        ]
        profile.wx_synced_tags = d.get("wx_synced_tags", [])
        profile.conversation_summary = d.get("conversation_summary", "")
        profile.notes = d.get("notes", [])
        profile.source = d.get("source", "")
        return profile

    def get_tag(self, subcategory: str) -> Optional[TagEntry]:
        for t in self.tags:
            if t.subcategory == subcategory:
                return t
        return None

    def get_tags_by_category(self, category: str) -> List[TagEntry]:
        return [t for t in self.tags if t.category == category]

    def get_tag_summary(self) -> str:
        if not self.tags:
            return "暂无标签"
        top = TagManager.get_top_tags(self.tags, 5)
        return " | ".join(t.value for t in top)

    def __repr__(self):
        return (
            f"Profile({self.wxid}, nickname={self.nickname!r}, "
            f"tags={len(self.tags)}, chats={self.chat_count})"
        )
