import re
import logging

logger = logging.getLogger(__name__)

def normalize_name(s: str) -> str:
    return re.sub(r'[^\w\u4e00-\u9fa5]', '', s).lower()

def _get_dyn_names(members: list) -> list:
    names = [m.get('remark') or m.get('nickname') or '' for m in (members or [])[:4]]
    names = [x for x in names if x]
    if not names: return []
    dyn = "、".join(names)
    return [dyn, re.sub(r'[\(（]\d+[\)）]$', '', dyn).strip()]

def is_acknowledgement_message(msg: str) -> bool:
    if not msg:
        return False
    msg_strip = msg.strip().lower()
    
    colon_idx = msg_strip.find(':') if msg_strip.find(':') != -1 else msg_strip.find('：')
    if colon_idx != -1 and colon_idx < 30:
        msg_strip = msg_strip[colon_idx + 1:].strip()

    for prefix in ("[@所有人]", "[有人@我]"):
        msg_strip = msg_strip.replace(prefix, "")
    msg_strip = msg_strip.strip()

    msg_clean = re.sub(
        r'[~～！!？?。，,\s\-_+=#@$%^&*()+\[\]{}|\\\/:;"\'<>\.👌👍🤝🙏🌹👌🏻👌🏼👌🏽👌🏾👌🏿'
        r'\U0001F600-\U0001F64F\U0001F300-\U0001F5FF'
        r'\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF'
        r'\U00002702-\U000027B0\U0001f900-\U0001f9FF'
        r'\U0000fe0f]+', '', msg_strip
    )
    if not msg_clean:
        return True

    ack_words = {
        "好的", "好的好的", "好滴", "好的呢", "好的的", "好的哈", "好呀", "好的呀", "好", "行", "行的",
        "收到", "收到收到", "收到了", "知道", "知道了", "知道啦", "ok", "okay", "okk", "ok的", "ok哈", "ok啦",
        "谢谢", "谢谢你", "多谢", "谢了", "客气", "客气了", "不客气", "不用客气", "不用谢", "好的谢谢", "收到谢谢",
        "拜拜", "再见", "回聊", "慢走", "晚安", "早点休息", "休息吧"
    }
    return msg_clean in ack_words

def check_friend_in_list(name: str, f_wxid: str, friend_list: list, account_id: str = None) -> bool:
    if not friend_list:
        return False
    name_clean = name.strip() if name else ""
    f_wxid_clean = f_wxid.strip() if f_wxid else ""
    possible_names = {name_clean}
    if f_wxid_clean:
        possible_names.add(f_wxid_clean)

    normalized_possibles = {normalize_name(p) for p in possible_names if p}
    cached_names_by_wxid = {}

    try:
        from src.utils.contacts_cache import contacts_cache
        from src.crm.account_data import get_active_account
        active_aid = account_id or get_active_account()
        for f in contacts_cache.get_friends(active_aid):
            w = f.get('wxid')
            if w:
                names_set = {str(f.get(field)).strip() for field in ('name', 'remark', 'nickname', 'alias') if f.get(field)}
                if names_set:
                    cached_names_by_wxid[w] = names_set
    except Exception as cache_ex:
        logger.debug(f"[白名单匹配] 反向解析缓存朋友名称异常: {cache_ex}")

    for x in friend_list:
        if not x:
            continue
        x_clean = str(x).strip()
        
        prefix_val = ""
        if x_clean.startswith("prefix:"):
            prefix_val = x_clean[7:].strip()
        elif x_clean.endswith("*") and len(x_clean) > 1:
            prefix_val = x_clean[:-1].strip()

        if prefix_val:
            if any(p.startswith(prefix_val) for p in possible_names if p):
                return True
            if f_wxid_clean:
                cached_ns = cached_names_by_wxid.get(f_wxid_clean)
                if cached_ns and any(cn.startswith(prefix_val) for cn in cached_ns):
                    return True
            continue

        if x_clean in possible_names:
            return True
            
        stripped = ""
        if x_clean.startswith("wxid:"):
            stripped = x_clean[5:].strip()
        elif x_clean.startswith("uid_"):
            stripped = x_clean[4:].strip()
        elif x_clean.startswith("namecat:"):
            stripped = x_clean[8:].split("::")[0].strip()
        else:
            stripped = x_clean

        if not stripped:
            continue

        if stripped in possible_names:
            return True

        if stripped.lower().startswith("wxid_") or (stripped.isalnum() and len(stripped) > 12):
            cached_ns = cached_names_by_wxid.get(stripped)
            if cached_ns:
                for cn in cached_ns:
                    if cn in possible_names or normalize_name(cn) in normalized_possibles:
                        return True

        is_raw_wxid = stripped.lower().startswith("wxid_") or (stripped.isalnum() and len(stripped) > 12)
        if not is_raw_wxid:
            norm_stripped = normalize_name(stripped)
            if norm_stripped:
                if norm_stripped in normalized_possibles:
                    return True
                if len(norm_stripped) >= 2:
                    for norm_p in normalized_possibles:
                        if norm_p and (norm_stripped in norm_p or norm_p in norm_stripped):
                            return True

        for val in possible_names:
            if val and (x_clean == f"uid_{val}" or x_clean == f"wxid:{val}" or x_clean == f"namecat:{val}::联系人" or (x_clean.startswith("namecat:") and x_clean.split("::")[0] == f"namecat:{val}")):
                return True
    return False

def check_group_in_list(name: str, g_wxid: str, group_list: list, account_id: str = None) -> bool:
    if not group_list:
        return False
    name_clean = name.strip() if name else ""
    g_wxid_clean = g_wxid.strip() if g_wxid else ""
    possible_names = {name_clean}
    if g_wxid_clean:
        possible_names.add(g_wxid_clean)

    normalized_possibles = {normalize_name(p) for p in possible_names if p}
    cached_names_by_wxid = {}

    try:
        from src.utils.contacts_cache import contacts_cache
        from src.crm.account_data import get_active_account
        active_aid = account_id or get_active_account()
        for g in contacts_cache.get_groups(active_aid):
            w = g.get('wxid')
            if w:
                names_set = set()
                n = g.get('name')
                if n:
                    names_set.add(str(n).strip())
                    names_set.add(re.sub(r'[\(（]\d+[\)）]$', '', str(n)).strip())
                for dn in _get_dyn_names(g.get('members')):
                    names_set.add(dn)
                if names_set:
                    cached_names_by_wxid[w] = names_set
    except Exception as cache_ex:
        logger.debug(f"[白名单匹配] 反向解析缓存群聊名称异常: {cache_ex}")

    for x in group_list:
        if not x:
            continue
        x_clean = str(x).strip()
        
        prefix_val = ""
        if x_clean.startswith("prefix:"):
            prefix_val = x_clean[7:].strip()
        elif x_clean.endswith("*") and len(x_clean) > 1:
            prefix_val = x_clean[:-1].strip()

        if prefix_val:
            if any(p.startswith(prefix_val) for p in possible_names if p):
                return True
            if g_wxid_clean:
                cached_ns = cached_names_by_wxid.get(g_wxid_clean)
                if cached_ns and any(cn.startswith(prefix_val) for cn in cached_ns):
                    return True
            continue

        if x_clean in possible_names:
            return True
            
        stripped = ""
        if x_clean.startswith("wxid:"):
            stripped = x_clean[5:].strip()
        elif x_clean.startswith("uid_"):
            stripped = x_clean[4:].strip()
        elif x_clean.startswith("namecat:"):
            stripped = x_clean[8:].split("::")[0].strip()
        else:
            stripped = x_clean

        if not stripped:
            continue

        if stripped in possible_names:
            return True

        if stripped.lower().endswith("@chatroom"):
            cached_ns = cached_names_by_wxid.get(stripped)
            if cached_ns:
                for cn in cached_ns:
                    if cn in possible_names or normalize_name(cn) in normalized_possibles:
                        return True
                    clean_cached = re.sub(r'[\(（]\d+[\)）]$', '', cn).strip()
                    if clean_cached in possible_names or normalize_name(clean_cached) in normalized_possibles:
                        return True

        is_raw_wxid = stripped.lower().endswith("@chatroom") or (stripped.isalnum() and len(stripped) > 12)
        if not is_raw_wxid:
            norm_stripped = normalize_name(stripped)
            if norm_stripped:
                if norm_stripped in normalized_possibles:
                    return True
                if len(norm_stripped) >= 2:
                    for norm_p in normalized_possibles:
                        if norm_p and (norm_stripped in norm_p or norm_p in norm_stripped):
                            return True

        for val in possible_names:
            if val and (x_clean == f"uid_{val}" or x_clean == f"wxid:{val}" or x_clean == f"namecat:{val}::群聊" or (x_clean.startswith("namecat:") and x_clean.split("::")[0] == f"namecat:{val}")):
                return True
    return False

def build_identity_maps(all_friends: list, all_groups: list) -> tuple:
    friend_name_to_wxid = {}
    for f in (all_friends or []):
        wxid = f.get('wxid')
        if wxid:
            friend_name_to_wxid[wxid] = wxid
            for field in ('name', 'remark', 'nickname', 'alias'):
                val = f.get(field)
                if val:
                    friend_name_to_wxid[str(val).strip()] = wxid

    group_name_to_wxid = {}
    for g in (all_groups or []):
        wxid = g.get('wxid')
        name = g.get('name')
        if wxid:
            group_name_to_wxid[wxid] = wxid
            if name:
                group_name_to_wxid[name.strip()] = wxid
                group_name_to_wxid[re.sub(r'[\(（]\d+[\)）]$', '', name).strip()] = wxid
            for dn in _get_dyn_names(g.get('members')):
                group_name_to_wxid[dn] = wxid

    for f in (all_friends or []):
        if f.get('category') == '群聊':
            wxid = f.get('wxid')
            name = f.get('name')
            if wxid:
                group_name_to_wxid[wxid] = wxid
                if name:
                    group_name_to_wxid[name.strip()] = wxid
                    group_name_to_wxid[re.sub(r'[\(（]\d+[\)）]$', '', name).strip()] = wxid
                for dn in _get_dyn_names(f.get('members')):
                    group_name_to_wxid[dn] = wxid

    return friend_name_to_wxid, group_name_to_wxid
