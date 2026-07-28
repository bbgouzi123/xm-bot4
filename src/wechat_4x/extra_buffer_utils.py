import re
from typing import Any, Optional

def _decode_varint(raw: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    pos = int(offset)
    n = len(raw)
    while pos < n:
        byte = raw[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if (byte & 0x80) == 0:
            return value, pos
        shift += 7
        if shift > 63:
            return 0, n
    return 0, n

def _decode_proto_text(raw: bytes) -> str:
    if not raw:
        return ""
    try:
        text = raw.decode("utf-8", errors="ignore")
    except Exception:
        return ""
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text).strip()

def parse_contact_extra_buffer(extra_buffer_val: Any) -> dict:
    out = {
        "gender": 0,
        "signature": "",
        "country": "",
        "province": "",
        "city": "",
        "source_scene": None,
        "label_ids": "",
    }
    if extra_buffer_val is None:
        return out

    raw: bytes
    if isinstance(extra_buffer_val, memoryview):
        raw = extra_buffer_val.tobytes()
    elif isinstance(extra_buffer_val, (bytes, bytearray)):
        raw = bytes(extra_buffer_val)
    else:
        return out

    if not raw:
        return out

    idx = 0
    n = len(raw)
    while idx < n:
        tag, idx_next = _decode_varint(raw, idx)
        if tag == 0 and idx_next == n:
            break
        idx = idx_next
        field_no = tag >> 3
        wire_type = tag & 0x7

        if wire_type == 0:
            val, idx_next = _decode_varint(raw, idx)
            idx = idx_next
            if field_no == 2:
                out["gender"] = int(val)
            elif field_no == 8:
                out["source_scene"] = int(val)
            continue

        if wire_type == 2:
            size, idx_next = _decode_varint(raw, idx)
            idx = idx_next
            end = idx + int(size)
            if end > n:
                break
            chunk = raw[idx:end]
            idx = end

            if field_no in {4, 5, 6, 7}:
                text = _decode_proto_text(chunk)
                if field_no == 4:
                    out["signature"] = text
                elif field_no == 5:
                    out["country"] = text
                elif field_no == 6:
                    out["province"] = text
                elif field_no == 7:
                    out["city"] = text
            elif field_no == 30:
                out["label_ids"] = _decode_proto_text(chunk)
            continue

        if wire_type == 1:
            idx += 8
            continue
        if wire_type == 5:
            idx += 4
            continue

        break
    return out

_SOURCE_SCENE_LABELS = {
    1: "通过QQ号添加",
    3: "通过微信号添加",
    6: "通过手机号添加",
    10: "通过名片添加",
    14: "通过群聊添加",
    30: "通过扫一扫添加",
}

_COUNTRY_LABELS = {
    "CN": "中国",
    "HK": "中国香港",
    "MO": "中国澳门",
    "TW": "中国台湾",
}

def source_scene_label(source_scene: Any) -> str:
    if source_scene is None:
        return "其他方式添加"
    try:
        scene_int = int(source_scene)
    except Exception:
        return "其他方式添加"
    if scene_int in _SOURCE_SCENE_LABELS:
        return _SOURCE_SCENE_LABELS[scene_int]
    return "其他方式添加"

def build_region(country: str, province: str, city: str) -> str:
    parts = []
    c = (country or "").strip().upper()
    c_label = _COUNTRY_LABELS.get(c, country) if c else ""
    p = (province or "").strip()
    ct = (city or "").strip()
    if c_label:
        parts.append(c_label)
    if p:
        parts.append(p)
    if ct:
        parts.append(ct)
    return " ".join(parts)

_SYSTEM_WXIDS = {
    "fmessage", "medianote", "floatbottle", "filehelper", "newsapp", 
    "helper_entry", "weibo", "qqmail", "tmessage", "notifymessage", 
    "systemnotify", "weixin", "brandsessionholder", "brandservicesessionholder", 
    "opencustomerservicemsg", "notification_messages", "userexperience_alarm"
}

def clean_group_name(name_str: str, wxid_str: str, members_list: list = None, self_wxid: str = None, self_nickname: str = None) -> str:
    val = (name_str or "").strip()
    if not val or val == wxid_str or val.endswith("@chatroom") or val.startswith("群聊_"):
        if members_list:
            member_names = []
            for m in members_list:
                m_wxid = m.get("wxid") or ""
                if self_wxid and m_wxid == self_wxid:
                    continue
                m_name = m.get("remark") or m.get("nickname") or ""
                if self_nickname and (m_name == self_nickname or m.get("nickname") == self_nickname):
                    continue
                if m_name:
                    member_names.append(m_name)
                if len(member_names) >= 4:
                    break
            if member_names:
                return "、".join(member_names)
        return f"群聊_{wxid_str.split('@')[0][-4:]}"
    return val

def get_actual_wxid(account_id: str) -> str:
    if not account_id:
        return ""
    import os
    import json
    meta_path = os.path.expanduser(f"~/.xm-ai-bot/accounts/{account_id}/account_meta.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
                actual_wxid = meta.get("wxid")
                if actual_wxid:
                    return actual_wxid
        except Exception:
            pass
    return account_id
