import logging
from typing import List, Dict
from src.utils.db_manager import WeChatDBManager

logger = logging.getLogger(__name__)

PRESET_TAG_TO_CRM_VALUES: Dict[str, tuple] = {
    "高意向客户": (
        "意向-强烈",
        "意向-中等",
        "意向-观望",
        "复购客户",
        "已成交",
        "意图-价格咨询",
        "意图-业务咨询",
        "价格咨询",
        "业务咨询",
        "购买意向",
    ),
    "待跟进": ("首次接触", "意向-观望", "意图-新加好友", "新加好友"),
    "已报价未成交": (
        "意向-观望",
        "意向-中等",
        "意向-强烈",
        "意图-价格咨询",
        "价格咨询",
    ),
    "沉默超过3天": (
        "首次接触",
        "意向-观望",
        "意向-中等",
        "意向-强烈",
        "意向-拒绝",
    ),
    "VIP重要高客单": ("意向-强烈", "复购客户", "已成交"),
    "老客户复购": ("复购客户", "已成交"),
    "老客户复购单": ("复购客户", "已成交"),
}


# 收集所有预设标签和对应的CRM值
ALL_PRESET_TAGS_AND_VALUES = set(PRESET_TAG_TO_CRM_VALUES.keys())
for vals in PRESET_TAG_TO_CRM_VALUES.values():
    ALL_PRESET_TAGS_AND_VALUES.update(vals)



def _expand_tag_names_for_crm_match(tag_names: List[str]) -> set:
    s = set(tag_names)
    for n in tag_names:
        for v in PRESET_TAG_TO_CRM_VALUES.get(n, ()):
            s.add(v)
    return s


def _profile_follow_id(profile) -> str:
    w = (getattr(profile, "wxid", None) or "").strip()
    if w:
        return w
    nick = (getattr(profile, "nickname", None) or "").strip()
    return nick if nick else ""


def resolve_audience_by_tags(targets_input: List[str], *, for_mass_send: bool, target_mode: str = "tag", bot_wxid: str = None) -> List[str]:
    """
    将界面选中的标签名/直传人名，解析为可触达标识。
    for_mass_send=True → 返回列表显示名/昵称（供 UIA 搜索会话）；
    for_mass_send=False → 优先 wxid，缺失则用昵称。
    """
    from src.crm.profile_manager import ProfileManager
    from src.utils.contacts_cache import contacts_cache
    from src.crm.account_data import get_active_account

    db = WeChatDBManager()
    all_sys_tags = [t.get("name") for t in db.get_all_tags()]
    active_aid = bot_wxid or get_active_account()
    pm = ProfileManager(account_id=active_aid)
    profiles = pm.get_all_profiles()
    expanded = _expand_tag_names_for_crm_match(targets_input)
    resolved: List[str] = []
    t_input_matches = {t: 0 for t in targets_input}

    for profile in profiles:
        p_vals = {t.value for t in profile.tags}
        synced = set(getattr(profile, "wx_synced_tags", None) or [])
        
        matched_for_this_profile = []
        for t_input in targets_input:
            t_expanded = _expand_tag_names_for_crm_match([t_input])
            if t_expanded.intersection(p_vals) or {t_input}.intersection(synced):
                matched_for_this_profile.append(t_input)
                t_input_matches[t_input] += 1
                
        if not matched_for_this_profile:
            continue

        if for_mass_send:
            nick = (profile.nickname or "").strip()
            if not nick:
                nick = (getattr(profile, "wxid", None) or "").strip()
            if nick:
                resolved.append(nick)
        else:
            cid = _profile_follow_id(profile)
            if cid:
                resolved.append(cid)

    for tname in targets_input:
        friends = contacts_cache.get_friends(account_id=active_aid, tag=tname)
        if friends:
            t_input_matches[tname] += len(friends)
            for f in friends:
                if for_mass_send:
                    nm = (f.get("nickname") or f.get("name") or "").strip()
                    if nm:
                        resolved.append(nm)
                else:
                    wx = (f.get("wxid") or "").strip()
                    if wx:
                        resolved.append(wx)
                    else:
                        nm = (f.get("name") or f.get("nickname") or "").strip()
                        if nm:
                            resolved.append(nm)

    # 微信端同步的所有标签名
    wx_tags = []
    try:
        wx_tags = [t.get("name") for t in contacts_cache.get_contact_tags(active_aid) if t.get("name")]
    except Exception:
        pass

    for t_input in targets_input:
        # 💡 只要 t_input 被成功解析为标签并匹配到了好友，或者它本身是系统预设标签、已同步的微信标签，或者为显式声明的标签模式，它就绝对是标签而非直传好友
        is_tag = (t_input_matches[t_input] > 0) or (t_input in all_sys_tags) or (t_input in wx_tags) or (t_input in ALL_PRESET_TAGS_AND_VALUES) or (target_mode in ("tag", "wx_tag"))

        if not is_tag and t_input not in resolved:
            # 💡 防错机制：只要 t_input 不是常见的分隔符或带有明显分隔痕迹的非法字符串，
            # 我们就认为它是合法的直传对象（如中文昵称、备注名、英文微信号、wxid等）。
            # 这样既能兼容未同步通讯录的中文白名单好友，又能避免错误解析标签。
            is_valid_target = True
            for c in t_input:
                if c in (',', ';', '\\', '/'):
                    is_valid_target = False
                    break
            
            if is_valid_target and t_input.strip():
                resolved.append(t_input.strip())
            else:
                logger.warning(f"[resolve_audience] 跳过非法直传微信号/解析失败标签: {t_input}")

    return list(dict.fromkeys(resolved))
