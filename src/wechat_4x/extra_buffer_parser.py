import logging
from .extra_buffer_utils import (
    parse_contact_extra_buffer,
    source_scene_label,
    build_region,
    clean_group_name,
    get_actual_wxid,
    _SYSTEM_WXIDS
)

logger = logging.getLogger("ExtraBufferParser")

def process_contact_rows(rows, username_col, label_dict, group_members_dict, sync_time, self_info):
    friends = []
    groups_dict = {}
    
    # 提取配置中配置过的白名单与黑名单 WXID，以便保留它们的昵称和头像
    config_special_wxids = set()
    try:
        from src.api.config_api.base_config import _load_configs
        cfg = _load_configs() or {}
        keys = [
            "auto_chat_friend_whitelist",
            "auto_chat_friend_excludes",
            "moment_interact_friend_whitelist",
            "moment_interact_friend_excludes",
            "auto_chat_group_whitelist",
            "auto_chat_group_excludes"
        ]
        for k in keys:
            lst = cfg.get(k)
            if isinstance(lst, list):
                for item in lst:
                    if isinstance(item, str):
                        item = item.strip()
                        if item.startswith("wxid:"):
                            item = item[5:].strip()
                        elif item.startswith("namecat:"):
                            continue
                        if item:
                            config_special_wxids.add(item)
    except Exception:
        pass

    self_wxid = ""
    self_nickname = ""
    if isinstance(self_info, dict):
        self_wxid = self_info.get("wxid") or ""
        self_nickname = self_info.get("nickname") or ""
    else:
        self_wxid = get_actual_wxid(self_info)
        
    for row in rows:
        wxid = row[username_col] or ""
        if not wxid:
            continue
        wxid_lower = wxid.lower()
        if wxid_lower in _SYSTEM_WXIDS or wxid_lower.startswith("fake_"):
            continue
        if "@kefu.openim" in wxid_lower or "@openim" in wxid_lower or "service_" in wxid_lower:
            continue

        def _get_val(r, col, default=""):
            try:
                return r[col] or default
            except Exception:
                return default

        nick = _get_val(row, "nick_name")
        alias = _get_val(row, "alias")
        remark = _get_val(row, "remark")
        local_type = int(_get_val(row, "local_type", 0))
        verify_flag = int(_get_val(row, "verify_flag", 0))
        extra_buffer = _get_val(row, "extra_buffer", None)

        flag = 0
        has_flag = False
        try:
            if hasattr(row, "keys"):
                has_flag = "flag" in row.keys()
            else:
                has_flag = "flag" in row
        except Exception:
            pass

        if has_flag:
            flag = int(_get_val(row, "flag", 0))

        if wxid.endswith("@chatroom"):
            m_list = group_members_dict.get(wxid, [])
            groups_dict[wxid] = {
                "wxid": wxid,
                "name": clean_group_name(nick or remark, wxid, m_list, self_wxid=self_wxid, self_nickname=self_nickname),
                "syncTime": sync_time,
                "members": m_list,
            }
        else:
            if wxid.startswith("gh_") or verify_flag != 0:
                continue

            is_friend = False
            if has_flag:
                is_friend = (local_type in (1, 5)) and ((flag & 1) != 0)
            else:
                is_friend = local_type in (1, 5)

            if not is_friend and wxid in config_special_wxids:
                is_friend = True

            if is_friend:
                ext_data = parse_contact_extra_buffer(extra_buffer)
                region = build_region(ext_data["country"], ext_data["province"], ext_data["city"])
                source = source_scene_label(ext_data["source_scene"])

                wx_tags = []
                raw_label_ids = ext_data.get("label_ids", "")
                if raw_label_ids:
                    for lid_str in raw_label_ids.split(","):
                        lid_str = lid_str.strip()
                        if lid_str in label_dict:
                            wx_tags.append(label_dict[lid_str])

                friends.append({
                    "wxid": wxid,
                    "name": remark or nick or alias or wxid,
                    "nickname": nick,
                    "remark": remark,
                    "alias": alias,
                    "region": region,
                    "source": source,
                    "signature": ext_data["signature"],
                    "category": "联系人",
                    "index": "#",
                    "syncTime": sync_time,
                    "tags": wx_tags,
                })
    return friends, groups_dict


def merge_group_rows(group_rows, groups_dict, group_members_dict, sync_time, self_info):
    self_wxid = ""
    self_nickname = ""
    if isinstance(self_info, dict):
        self_wxid = self_info.get("wxid") or ""
        self_nickname = self_info.get("nickname") or ""
    else:
        self_wxid = get_actual_wxid(self_info)
        
    for gr in group_rows:
        try:
            cr_wxid = gr[0] or ""
        except Exception:
            continue
        if not cr_wxid:
            continue
        try:
            disp = gr["disp_name"] or ""
        except Exception:
            disp = ""

        m_list = group_members_dict.get(cr_wxid, [])
        cleaned_disp = clean_group_name(disp, cr_wxid, m_list, self_wxid=self_wxid, self_nickname=self_nickname)
        if cr_wxid in groups_dict:
            curr_name = groups_dict[cr_wxid]["name"]
            if curr_name.startswith("群聊_"):
                groups_dict[cr_wxid]["name"] = cleaned_disp
            elif "、" in curr_name and not ("、" in cleaned_disp or cleaned_disp.startswith("群聊_")):
                groups_dict[cr_wxid]["name"] = cleaned_disp
            groups_dict[cr_wxid]["members"] = m_list
        else:
            groups_dict[cr_wxid] = {
                "wxid": cr_wxid,
                "name": cleaned_disp,
                "syncTime": sync_time,
                "members": m_list,
            }
