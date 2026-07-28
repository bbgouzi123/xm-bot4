"""
客户标签微信同步规则匹配逻辑 — 从 profile_manager.py 拆离以满足单文件 300 行限额红线
"""
from typing import List
from .tag_manager import TagEntry

def get_tags_needing_sync(profile, max_tags: int = 3) -> List[str]:
    from src.utils.db_manager import WeChatDBManager
    identity_cfg = WeChatDBManager().get_identity_routing()
    tag_mappings = identity_cfg.get("tag_mappings", [])
    mapped_labels = []
    invalid_words = {
        "business", "casual_chat", "negative", "greeting", 
        "unknown", "未知", "无", "0-100%", "none", "null"
    }
    for t in profile.tags:
        matched = False
        for rule in tag_mappings:
            if _match_tag_rule(t, rule):
                wx_lbl = rule.get("wx_tag_name", "").strip()
                if wx_lbl:
                    mapped_labels.append(wx_lbl)
                    matched = True
                    break
        if not matched:
            label = (t.value or "").strip()
            if not label:
                continue
            if "-" in label:
                parts = label.split("-", 1)
                label = parts[-1].strip()
            if label.lower() in invalid_words:
                continue
            if label.endswith("%") and label[:-1].isdigit():
                continue
            if label:
                mapped_labels.append(label[:15])
    unique_labels = []
    for lbl in mapped_labels:
        if lbl not in unique_labels:
            unique_labels.append(lbl)
    wx_labels = unique_labels[:max_tags]
    need_sync = [label for label in wx_labels if label not in profile.wx_synced_tags]
    return need_sync

def _match_tag_rule(t: TagEntry, rule: dict) -> bool:
    cat = rule.get("ai_category", "any")
    if cat != "any" and t.category != cat:
        return False
    sub = rule.get("ai_subcategory", "any")
    if sub != "any" and t.subcategory != sub:
        return False
    m_type = rule.get("match_type", "equals")
    m_val = str(rule.get("match_value", "")).strip()
    t_val = str(t.value or "").strip()
    if m_type == "equals":
        return t_val.lower() == m_val.lower()
    elif m_type == "contains":
        return m_val.lower() in t_val.lower()
    elif m_type == "range":
        try:
            clean_t_val = "".join([c for c in t_val if c.isdigit() or c == "."])
            val_num = float(clean_t_val) if clean_t_val else 0.0
            if "-" in m_val:
                parts = m_val.split("-")
                low = float(parts[0])
                high = float(parts[1])
                return low <= val_num <= high
            else:
                target = float(m_val)
                return val_num >= target
        except Exception:
            return False
    return False
