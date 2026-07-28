"""
聊天统计 API 的通用辅助函数与配置映射
"""
import time
import logging

logger = logging.getLogger("ChatStatsHelpers")

TYPE_MAP = {
    1: "文本",
    3: "图片",
    34: "语音",
    43: "视频",
    47: "表情",
    49: "小程序/文件/链接",
    10000: "系统消息"
}

def translate_talker_names(top_talkers, tmp_contact_dec, has_contact, cursor):
    """将微信号翻译为备注/昵称，保留原始 session_id 并增加 display_name"""
    for t in top_talkers:
        t["display_name"] = t["session_id"]  # 默认兜底
        
    if not (has_contact and top_talkers):
        return
    try:
        cursor.execute("ATTACH DATABASE ? AS contact_db;", (tmp_contact_dec,))
        wxids = [t["session_id"] for t in top_talkers if t["session_id"] != "unknown"]
        if wxids:
            placeholders = ",".join(["?"] * len(wxids))
            cursor.execute(
                f"SELECT username, remark, nick_name FROM contact_db.contact WHERE username IN ({placeholders});",
                wxids
            )
            contact_rows = cursor.fetchall()
            contact_map = {
                row["username"]: row["remark"] or row["nick_name"] or row["username"]
                for row in contact_rows
            }
            for t in top_talkers:
                wxid = t["session_id"]
                d_name = contact_map.get(wxid)
                if d_name:
                    t["display_name"] = d_name
        cursor.execute("DETACH DATABASE contact_db;")
    except Exception as e_attach:
        logger.error(f"[统计API] 关联通讯录查询出错: {e_attach}")

def bucket_retention_days(now_ts, last_msg_ts):
    """根据最后消息时间分桶"""
    diff_days = (now_ts - last_msg_ts) / 86400.0
    if diff_days <= 3:
        return "loyal"
    elif diff_days <= 7:
        return "warm"
    elif diff_days <= 14:
        return "sinking"
    return "silent"

def update_retention_counts(category, counts):
    """累加留存分类数量"""
    if category == "loyal":
        counts["loyal_active"] += 1
    elif category == "warm":
        counts["warm_active"] += 1
    elif category == "sinking":
        counts["sinking_churn"] += 1
    else:
        counts["silent_churn"] += 1
