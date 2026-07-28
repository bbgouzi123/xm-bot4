import os
import shutil
import tempfile
import time
import sqlite3
import logging
import hashlib
from collections import defaultdict
from fastapi import APIRouter
from src.utils.response import ok, err
from src.utils.stats_helpers import TYPE_MAP, translate_talker_names, bucket_retention_days, update_retention_counts
from app.state import account_manager
from src.utils.instance_manager import InstanceManagerV2
from src.utils.wechat_key_store import get_persisted_wechat_key
from src.utils.wcdb_helpers import match_db_storage_by_key
from src.utils.wechat_decrypt import WeChatDatabaseDecryptor

logger = logging.getLogger("ChatStatsApi")
router = APIRouter()

HOT_BUSINESS_WORDS = ['价格', '多少钱', '优惠', '退款', '发货', '快递', '正品', '客服', '购买', '加盟', '合作', '定制', '报价', '发票', '规格', '尺寸', '质量']
SENSITIVE_WORDS = ['加微信', '转账', '退钱', '红包', '私单', '垃圾', '加微', '微信转账', '加我', '退款给我']

@router.get("/api/v1/chat/stats")
async def get_chat_stats_local(bot_wxid: str = "", start_date: str = "", end_date: str = ""):
    logger.warning(f"=== [STATS API REQ] bot_wxid={bot_wxid}, start={start_date}, end={end_date} ===")
    inst_list = []
    if bot_wxid == "all":
        inst_list = list({inst.wxid: inst for inst in account_manager._instances.values() if inst.driver.is_connected() and inst.wxid}.values())
    else:
        target = None
        if bot_wxid:
            target = next((inst for inst in account_manager._instances.values() if inst.wxid == bot_wxid or getattr(inst.driver, "bot_wxid", "") == bot_wxid), None)
        if not target:
            act_wxid = (InstanceManagerV2.get_instance().get_active_instance() or {}).get("wxid")
            if act_wxid:
                target = next((inst for inst in account_manager._instances.values() if inst.wxid == act_wxid or getattr(inst.driver, "bot_wxid", "") == act_wxid), None)
        if not target or not target.driver.is_connected():
            target = next((inst for inst in account_manager._instances.values() if inst.driver.is_connected()), None)
        if target: inst_list.append(target)

    if not inst_list:
        return err(40000, "当前无已连接并就绪的微信账号实例")

    merged_daily_volume = defaultdict(lambda: {"self_count": 0, "other_count": 0})
    merged_active_hours = defaultdict(int)
    merged_msg_types = defaultdict(int)
    merged_private_talkers, merged_group_talkers = {}, {}
    merged_response_times, merged_hourly_response_data = [], defaultdict(list)
    merged_ret_counts = {"loyal_active": 0, "warm_active": 0, "sinking_churn": 0, "silent_churn": 0}
    merged_hot_word_counts = defaultdict(int)
    merged_sensitive_alerts = []

    temp_dir = tempfile.mkdtemp(prefix="xm_chat_stats_local_")
    decryptor_cache = {}
    now_ts = int(time.time())
    since_ts = int(time.mktime(time.strptime(start_date, "%Y-%m-%d"))) if start_date else (now_ts - 30 * 86400)
    until_ts = int(time.mktime(time.strptime(end_date, "%Y-%m-%d"))) + 86399 if end_date else now_ts

    try:
        for idx, inst in enumerate(inst_list):
            target_wxid = inst.wxid or getattr(inst.driver, "bot_wxid", "") or f"bot_{idx}"
            hex_key = getattr(inst.driver, "hex_key", None)
            if not hex_key:
                KEYS_FILE_PATH = os.path.expanduser("~/.xm-ai-bot/wechat_keys.json")
                if os.path.exists(KEYS_FILE_PATH):
                    try:
                        with open(KEYS_FILE_PATH, "r", encoding="utf-8") as f:
                            kd = json.load(f)
                        from src.utils.wechat_key_store import clean_wxid
                        hex_key = kd.get(clean_wxid(target_wxid)) or kd.get(target_wxid) or kd.get("last_key")
                    except Exception: pass
            if not hex_key:
                hex_key = get_persisted_wechat_key(target_wxid)
            if not hex_key: continue
            db_storage_dir = match_db_storage_by_key(hex_key)
            if not db_storage_dir: continue

            message_db_path = os.path.join(db_storage_dir, "message", "message_0.db")
            contact_db_path = os.path.join(db_storage_dir, "contact", "contact.db")
            if not os.path.exists(contact_db_path):
                contact_db_path = os.path.join(db_storage_dir, "Contact", "contact.db")
            if not os.path.exists(message_db_path): continue

            tmp_msg_dec = os.path.join(temp_dir, f"message_dec_{idx}.db")
            tmp_contact_dec = os.path.join(temp_dir, f"contact_dec_{idx}.db")

            if hex_key not in decryptor_cache:
                decryptor_cache[hex_key] = WeChatDatabaseDecryptor(hex_key)
            decryptor = decryptor_cache[hex_key]

            tmp_msg_raw = os.path.join(temp_dir, f"message_raw_{idx}.db")
            shutil.copy2(message_db_path, tmp_msg_raw)
            if not decryptor.decrypt_database(tmp_msg_raw, tmp_msg_dec): continue

            has_contact = False
            if os.path.exists(contact_db_path):
                tmp_contact_raw = os.path.join(temp_dir, f"contact_raw_{idx}.db")
                try:
                    shutil.copy2(contact_db_path, tmp_contact_raw)
                    if decryptor.decrypt_database(tmp_contact_raw, tmp_contact_dec):
                        has_contact = True
                except Exception: pass

            conn = sqlite3.connect(tmp_msg_dec)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='Name2Id'")
            has_name2id = cursor.fetchone() is not None
            inst_talkers, text_messages = [], []

            if not has_name2id:
                cursor.execute(
                    "SELECT strftime('%Y-%m-%d', datetime(CreateTime, 'unixepoch', 'localtime')) as date_str, "
                    "SUM(CASE WHEN IsSender = 1 THEN 1 ELSE 0 END) as self_count, "
                    "SUM(CASE WHEN IsSender = 0 THEN 1 ELSE 0 END) as other_count "
                    "FROM message WHERE CreateTime BETWEEN ? AND ? GROUP BY date_str ORDER BY date_str ASC;",
                    (since_ts, until_ts)
                )
                for r in cursor.fetchall():
                    merged_daily_volume[r["date_str"]]["self_count"] += r["self_count"] or 0
                    merged_daily_volume[r["date_str"]]["other_count"] += r["other_count"] or 0

                cursor.execute("SELECT CAST(strftime('%H', datetime(CreateTime, 'unixepoch', 'localtime')) AS INTEGER) as hour_val, COUNT(*) as count_val FROM message WHERE CreateTime BETWEEN ? AND ? GROUP BY hour_val;", (since_ts, until_ts))
                for r in cursor.fetchall(): merged_active_hours[r["hour_val"]] += r["count_val"] or 0

                cursor.execute("SELECT Type, COUNT(*) as c FROM message WHERE CreateTime BETWEEN ? AND ? GROUP BY Type;", (since_ts, until_ts))
                for r in cursor.fetchall(): merged_msg_types[TYPE_MAP.get(r["Type"], "其它")] += r["c"] or 0

                cursor.execute("SELECT StrTalker as session_id, COUNT(*) as count_val FROM message WHERE CreateTime BETWEEN ? AND ? GROUP BY session_id ORDER BY count_val DESC LIMIT 100;", (since_ts, until_ts))
                inst_talkers = [{"session_id": r["session_id"], "count": r["count_val"] or 0} for r in cursor.fetchall()]

                cursor.execute("SELECT CreateTime, StrTalker, Message, IsSender FROM message WHERE CreateTime BETWEEN ? AND ? AND Type = 1;", (since_ts, until_ts))
                for r in cursor.fetchall(): text_messages.append((r["CreateTime"], r["StrTalker"], r["Message"], r["IsSender"] == 1))

                cursor.execute("SELECT StrTalker, CreateTime, IsSender FROM message ORDER BY StrTalker, CreateTime ASC;")
                talker_messages = defaultdict(list)
                for r in cursor.fetchall(): talker_messages[r["StrTalker"]].append((r["CreateTime"], r["IsSender"]))

                for t, msgs in talker_messages.items():
                    update_retention_counts(bucket_retention_days(now_ts, msgs[-1][0]), merged_ret_counts)
                    last_is_self, last_time = None, None
                    for c_time, is_sender in msgs:
                        if since_ts <= c_time <= until_ts:
                            is_self = (is_sender == 1)
                            if last_is_self is not None and not last_is_self and is_self:
                                diff = c_time - last_time
                                if 0 < diff <= 7200:
                                    merged_response_times.append(diff)
                                    merged_hourly_response_data[time.localtime(c_time).tm_hour].append(diff)
                            last_is_self, last_time = is_self, c_time
            else:
                cursor.execute("SELECT rowid, user_name FROM Name2Id")
                rowid_to_wxid = {row["rowid"]: row["user_name"] for row in cursor.fetchall()}
                md5_to_wxid = {f"Msg_{hashlib.md5(w.encode('utf-8')).hexdigest()}": w for w in rowid_to_wxid.values()}

                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%';")
                msg_tables = [row["name"] for row in cursor.fetchall()]
                session_counts = defaultdict(int)

                for t_name in msg_tables:
                    try:
                        cursor.execute(f"SELECT create_time FROM {t_name} ORDER BY local_id DESC LIMIT 1;")
                        last_row = cursor.fetchone()
                        if not last_row: continue
                        update_retention_counts(bucket_retention_days(now_ts, last_row["create_time"]), merged_ret_counts)

                        cursor.execute(f"SELECT local_type, create_time, real_sender_id, message_content FROM {t_name} WHERE create_time BETWEEN ? AND ? ORDER BY local_id ASC;", (since_ts, until_ts))
                        msg_rows = cursor.fetchall()
                        if not msg_rows: continue

                        wxid = md5_to_wxid.get(t_name, "unknown")
                        session_counts[wxid] += len(msg_rows)
                        last_is_self, last_time = None, None

                        for msg in msg_rows:
                            c_time = msg["create_time"]
                            is_self = (rowid_to_wxid.get(msg["real_sender_id"], "") == target_wxid)
                            local_tm = time.localtime(c_time)

                            merged_daily_volume[time.strftime("%Y-%m-%d", local_tm)]["self_count" if is_self else "other_count"] += 1
                            merged_active_hours[local_tm.tm_hour] += 1
                            merged_msg_types[TYPE_MAP.get(msg["local_type"], "其它")] += 1

                            if msg["local_type"] == 1:
                                c_val = msg["message_content"]
                                if isinstance(c_val, bytes):
                                    try:
                                        import zstandard as zstd
                                        c_val = zstd.ZstdDecompressor().decompress(c_val).decode('utf-8', errors='ignore') if c_val.startswith(b'\x28\xb5\x2f\xfd') else c_val.decode('utf-8', errors='ignore')
                                    except Exception:
                                        try: c_val = c_val.decode('utf-8', errors='ignore')
                                        except Exception: pass
                                text_messages.append((c_time, wxid, c_val, is_self))

                            if last_is_self is not None and not last_is_self and is_self:
                                diff = c_time - last_time
                                if 0 < diff <= 7200:
                                    merged_response_times.append(diff)
                                    merged_hourly_response_data[local_tm.tm_hour].append(diff)
                            last_is_self, last_time = is_self, c_time
                    except Exception as e_table:
                        logger.error(f"[统计API] 遍历表 {t_name} 崩溃跳过: {e_table}", exc_info=True)

                inst_talkers = [{"session_id": k, "count": v} for k, v in session_counts.items()]

            inst_private = [t for t in inst_talkers if not t["session_id"].endswith("@chatroom")][:30]
            inst_group = [t for t in inst_talkers if t["session_id"].endswith("@chatroom")][:30]
            translate_talker_names(inst_private, tmp_contact_dec, has_contact, cursor)
            translate_talker_names(inst_group, tmp_contact_dec, has_contact, cursor)

            for t in inst_private:
                sid = t["session_id"]
                if sid not in merged_private_talkers: merged_private_talkers[sid] = {"count": 0, "display_name": t["display_name"]}
                merged_private_talkers[sid]["count"] += t["count"]
            for t in inst_group:
                sid = t["session_id"]
                if sid not in merged_group_talkers: merged_group_talkers[sid] = {"count": 0, "display_name": t["display_name"]}
                merged_group_talkers[sid]["count"] += t["count"]

            for c_time, talker_id, text, is_self in text_messages:
                if not text: continue
                text_str = str(text)
                for w in HOT_BUSINESS_WORDS:
                    if w in text_str: merged_hot_word_counts[w] += 1
                for w in SENSITIVE_WORDS:
                    if not w or not w.strip(): continue
                    if w in text_str:
                        merged_sensitive_alerts.append({"time": c_time, "wxid": target_wxid, "talker": talker_id, "word": w, "text": text_str})

        conn.close()
    finally:
        try: shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception: pass

    # ================= 整理并构造成返回的数据结构 =================
    date_list = sorted(list(merged_daily_volume.keys()))
    daily_volume_list = [{"date": d, "self_count": merged_daily_volume[d]["self_count"], "other_count": merged_daily_volume[d]["other_count"]} for d in date_list]

    top_private = sorted([{"session_id": k, "display_name": v["display_name"], "count": v["count"]} for k, v in merged_private_talkers.items()], key=lambda x: x["count"], reverse=True)[:10]
    top_group = sorted([{"session_id": k, "display_name": v["display_name"], "count": v["count"]} for k, v in merged_group_talkers.items()], key=lambda x: x["count"], reverse=True)[:10]
    active_hours_list = [{"hour": f"{h:02d}:00", "count": merged_active_hours[h]} for h in range(24)]
    msg_types_list = [{"msg_type": k, "count": v} for k, v in merged_msg_types.items()]

    hourly_delays = []
    for h in range(24):
        vals = merged_hourly_response_data[h]
        hourly_delays.append({"hour": f"{h:02d}:00", "avg_delay": int(sum(vals) / len(vals)) if vals else 0})

    total_resp = len(merged_response_times)
    resp_15s = len([t for t in merged_response_times if t <= 15])
    resp_60s = len([t for t in merged_response_times if t <= 60])

    sla_stats = {
        "avg_response_time": int(sum(merged_response_times) / total_resp) if total_resp else 0,
        "total_responses": total_resp,
        "resp_15s_rate": (resp_15s / total_resp) if total_resp else 0.0,
        "resp_60s_rate": (resp_60s / total_resp) if total_resp else 0.0,
        "hourly_delays": hourly_delays
    }

    ret_list = [
        {"days": "3d", "count": merged_ret_counts["loyal_active"]},
        {"days": "7d", "count": merged_ret_counts["warm_active"]},
        {"days": "15d", "count": merged_ret_counts["sinking_churn"]},
        {"days": "silent", "count": merged_ret_counts["silent_churn"]}
    ]
    hot_words_list = sorted([{"word": k, "count": v} for k, v in merged_hot_word_counts.items()], key=lambda x: x["count"], reverse=True)[:5]
    sensitive_alerts_list = sorted(merged_sensitive_alerts, key=lambda x: x["time"], reverse=True)[:30]

    logger.warning(f"=== [STATS API OK] 统计成功返回: 每日条数数={len(daily_volume_list)}, 排行榜数={len(top_private)} ===")
    return ok({
        "daily_volume": daily_volume_list,
        "top_private_talkers": top_private,
        "top_group_talkers": top_group,
        "active_hours": active_hours_list,
        "msg_types": msg_types_list,
        "sla_stats": sla_stats,
        "retention_stats": ret_list,
        "hot_words": hot_words_list,
        "sensitive_alerts": sensitive_alerts_list
    })
