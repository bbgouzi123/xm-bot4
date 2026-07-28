"""
朋友圈互动名单过滤服务 - 高容错归一化过滤
解耦自 moment_config.py 以符合单文件 300 行限制规约。
"""
import re

def is_author_blacklisted(author_name: str, settings: dict) -> bool:
    """兼容旧版 moment_settings.author_blacklist（手输昵称列表）。"""
    raw = settings.get("author_blacklist")
    if not isinstance(raw, list) or not raw:
        return False
    a = (author_name or "").strip()
    if not a:
        return False
    
    def normalize_name(s: str) -> str:
        return re.sub(r'[^\w\u4e00-\u9fa5]', '', s).lower()
    norm_a = normalize_name(a)
    for entry in raw:
        if not isinstance(entry, str):
            continue
        e = entry.strip()
        if not e:
            continue
        if a == e:
            return True
        norm_e = normalize_name(e)
        if norm_a and norm_e:
            if norm_a == norm_e or (len(norm_e) >= 2 and (norm_e in norm_a or norm_a in norm_e)):
                return True
    return False


def is_wechat_wxid(s: str) -> bool:
    s = s.lower().strip()
    if s.endswith("chatroom"):
        return True
    if s.startswith("wxid_"):
        return True
    import re
    return bool(re.match(r'^[a-zA-Z0-9_-]{6,32}$', s))


def _match_global_contact_excludes(author_name: str, account_id: str, excludes: list) -> bool:
    """与 chat_monitor 好友排除一致：会话/通讯录昵称 或 wxid 命中即排除。
    
    高容错率优化：支持标点/Emoji剔除、去空格以及相似度包含匹配，以防止昵称波动导致白/黑名单漏判。
    """
    if not excludes or not isinstance(excludes, list):
        return False
    name = (author_name or "").strip()
    if not name:
        return False

    def _normalize(s: str) -> str:
        import re as _re
        return _re.sub(r'[^\w\u4e00-\u9fa5]', '', s).lower().strip()

    f_wxid = ""
    friends = []
    try:
        from src.utils.contacts_cache import contacts_cache
        friends = contacts_cache.get_friends(account_id) or []
        name_to_wxid = {}
        norm_name_to_wxid = {}
        
        for f in friends:
            w = f.get("wxid", "")
            if not w:
                continue
            for field in ("name", "remark", "display_name"):
                val = f.get(field)
                if val:
                    val_str = str(val).strip()
                    name_to_wxid[val_str] = w
                    norm_val = _normalize(val_str)
                    if norm_val:
                        norm_name_to_wxid[norm_val] = w
                        
        f_wxid = name_to_wxid.get(name, "")
        if not f_wxid:
            f_wxid = norm_name_to_wxid.get(_normalize(name), "")
    except Exception:
        pass

    # 💡 备用方案：通过 ProfileManager 从数据库画像中查找 wxid 
    if not f_wxid:
        try:
            from src.crm.profile_manager import ProfileManager
            pm = ProfileManager(account_id=account_id)
            norm_name = _normalize(name)
            for p in pm.get_all_profiles():
                if _normalize(p.nickname) == norm_name or _normalize(p.remark) == norm_name:
                    f_wxid = p.wxid
                    break
        except Exception:
            pass

    # 1. 优先尝试高精度精确匹配
    possible_keys = {name}
    if f_wxid:
        possible_keys.add(f_wxid)
        
    # 2. 如果直接匹配成功，立即返回
    for x in excludes:
        if not x:
            continue
        x_clean = str(x).strip()
        if x_clean in possible_keys:
            return True
        # 剥离可能的前缀（如 wxid: 或者是 uid_ 等）
        stripped = x_clean
        if x_clean.startswith("wxid:"):
            stripped = x_clean[5:].strip()
        elif x_clean.startswith("uid_"):
            stripped = x_clean[4:].strip()
        elif x_clean.startswith("namecat:"):
            stripped = x_clean[8:].split("::")[0].strip()
            
        if stripped in possible_keys:
            return True

        # 💡 增加反向解析：如果排除项配置了 wxid（例如 wxid:xxx 或直接是 wxid）
        # 我们反查该 wxid 对应的所有可能名字，再与当前的 author_name (name) 做匹配
        is_wxid_style = x_clean.startswith("wxid:") or is_wechat_wxid(x_clean)
        if is_wxid_style:
            x_wxid = x_clean[5:].strip() if x_clean.startswith("wxid:") else x_clean
            x_names = set()
            for f in friends:
                if f.get("wxid") == x_wxid:
                    if f.get("name"): x_names.add(f.get("name").strip())
                    if f.get("remark"): x_names.add(f.get("remark").strip())
                    if f.get("display_name"): x_names.add(f.get("display_name").strip())
            try:
                from src.crm.profile_manager import ProfileManager
                pm = ProfileManager(account_id=account_id)
                p = pm.get_profile(x_wxid)
                if p:
                    if p.nickname: x_names.add(p.nickname.strip())
                    if p.remark: x_names.add(p.remark.strip())
            except Exception:
                pass
            
            norm_name = _normalize(name)
            norm_x_names = {_normalize(n) for n in x_names if n}
            if norm_name in norm_x_names:
                return True

    # 3. 归一化与模糊相似度匹配
    def normalize_name(s: str) -> str:
        # 去除标点、表情、特殊符号，仅保留汉字、字母、数字并转为小写
        import re as _re
        return _re.sub(r'[^\w\u4e00-\u9fa5]', '', s).lower()

    normalized_possibles = {normalize_name(p) for p in possible_keys if p}

    for x in excludes:
        if not x:
            continue
        x_clean = str(x).strip()
        
        # 剥离前缀后的文本
        stripped = x_clean
        if x_clean.startswith("wxid:"):
            stripped = x_clean[5:].strip()
        elif x_clean.startswith("uid_"):
            stripped = x_clean[4:].strip()
        elif x_clean.startswith("namecat:"):
            stripped = x_clean[8:].split("::")[0].strip()

        is_raw_wxid = is_wechat_wxid(stripped)
        if not is_raw_wxid:
            norm_stripped = normalize_name(stripped)
            if norm_stripped:
                if norm_stripped in normalized_possibles:
                    return True
                # 子串双向包含判定
                if len(norm_stripped) >= 2:
                    for norm_p in normalized_possibles:
                        if norm_p and (norm_stripped in norm_p or norm_p in norm_stripped):
                            return True
        else:
            # 💡 如果是 wxid，依然可以尝试反查出的名字的归一化模糊匹配
            x_names = set()
            for f in friends:
                if f.get("wxid") == stripped:
                    if f.get("name"): x_names.add(f.get("name").strip())
                    if f.get("remark"): x_names.add(f.get("remark").strip())
                    if f.get("display_name"): x_names.add(f.get("display_name").strip())
            try:
                from src.crm.profile_manager import ProfileManager
                pm = ProfileManager(account_id=account_id)
                p = pm.get_profile(stripped)
                if p:
                    if p.nickname: x_names.add(p.nickname.strip())
                    if p.remark: x_names.add(p.remark.strip())
            except Exception:
                pass
            
            for x_n in x_names:
                norm_xn = normalize_name(x_n)
                if norm_xn:
                    if norm_xn in normalized_possibles:
                        return True
                    if len(norm_xn) >= 2:
                        for norm_p in normalized_possibles:
                            if norm_p and (norm_xn in norm_p or norm_p in norm_xn):
                                return True

    return False
