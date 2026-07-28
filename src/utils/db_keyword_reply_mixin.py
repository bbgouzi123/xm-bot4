import uuid
from datetime import datetime
from typing import List, Any

class KeywordReplyMixin:
    def get_all_keyword_replies(self) -> List[dict]:
        return self._keyword_replies

    def add_keyword_reply(self, keywords: List[str], match_type: str, reply_content: str, is_active: bool = True, scope: str = "all", delete_on_reply: bool = False) -> dict:
        item = {
            "id": f"kr_{uuid.uuid4().hex[:8]}",
            "keywords": [k.strip() for k in keywords if k.strip()],
            "match_type": match_type,
            "reply_content": reply_content,
            "is_active": is_active,
            "scope": scope,
            "delete_on_reply": delete_on_reply,
            "created_at": datetime.now().isoformat()
        }
        self._keyword_replies.insert(0, item)
        self._persist_snapshot()
        return item

    def delete_keyword_reply(self, reply_id: str) -> bool:
        for idx, item in enumerate(self._keyword_replies):
            if item.get("id") == reply_id:
                self._keyword_replies.pop(idx)
                self._persist_snapshot()
                return True
        return False

    def update_keyword_reply(self, reply_id: str, updates: dict) -> Any:
        for idx, item in enumerate(self._keyword_replies):
            if item.get("id") == reply_id:
                for k, v in updates.items():
                    if k in ["keywords", "match_type", "reply_content", "is_active", "scope", "delete_on_reply"]:
                        if k == "keywords" and isinstance(v, list):
                            item[k] = [x.strip() for x in v if x.strip()]
                        else:
                            item[k] = v
                self._persist_snapshot()
                return item
        return None
