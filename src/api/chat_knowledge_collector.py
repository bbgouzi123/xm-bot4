"""
知识库采集核心逻辑 — Cloud 交互 + UIA 采集线程

从 chat_knowledge_api.py 拆出，保持 API 层 < 300 行。
"""
import logging
import time
import threading
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# 全局采集状态（单进程内唯一）
collect_state: Dict[str, Any] = {
    "running": False,
    "task_id": None,
    "friend_name": "",
    "industry_id": "",
    "total": 0,
    "processed": 0,
    "extracted": 0,
    "status": "idle",    # idle/running/done/failed/stopped
    "error": "",
}
collect_lock = threading.Lock()
stop_flag = threading.Event()


def cloud_upload_entries(task_id: int, industry_id: str, friend_name: str,
                         entries: list, bot_wxid: str = "") -> int:
    """将采集到的 Q&A 对通过 Cloud 后端持久化到数据库"""
    try:
        from src.utils.cloud_sync.base import CloudSyncBaseMixin
        sync = CloudSyncBaseMixin()
        data = {
            "bot_wxid": bot_wxid,
            "task_id": task_id,
            "industry_id": industry_id,
            "friend_name": friend_name,
            "entries": entries,
        }
        result = sync._post("/api/v1/chat-knowledge/entries", data, need_auth=True)
        if result and isinstance(result, dict):
            return result.get("created", 0)
        return 0
    except Exception as e:
        logger.error(f"[知识库] 上传条目失败: {e}")
        return 0


def cloud_create_task(friend_name: str, industry_id: str,
                      friend_wxid: str = "", bot_wxid: str = "") -> Optional[int]:
    """在 Cloud 后端创建采集任务"""
    try:
        from src.utils.cloud_sync.base import CloudSyncBaseMixin
        sync = CloudSyncBaseMixin()
        data = {
            "bot_wxid": bot_wxid,
            "friend_wxid": friend_wxid,
            "friend_name": friend_name,
            "industry_id": industry_id,
        }
        result = sync._post("/api/v1/chat-knowledge/tasks", data, need_auth=True)
        if result and isinstance(result, dict):
            return result.get("id")
        return None
    except Exception as e:
        logger.error(f"[知识库] 创建采集任务失败: {e}")
        return None


def cloud_update_task(task_id: int, **kwargs):
    """更新 Cloud 端采集任务状态"""
    try:
        from src.utils.cloud_sync.base import CloudSyncBaseMixin
        sync = CloudSyncBaseMixin()
        sync._request("PATCH", f"/api/v1/chat-knowledge/tasks/{task_id}",
                       data=kwargs, need_auth=True)
    except Exception as e:
        logger.error(f"[知识库] 更新任务状态失败: {e}")


def get_chat_history_from_db(bot_wxid: str, friend_name: str, friend_wxid: str, limit: int, nickname: str = "我") -> list:
    """
    尝试从 DLL 或影子库中读取指定好友的聊天历史记录。
    返回的格式：[{"content": str, "isSelf": bool, "sender": str, "type": "text"}]
    """
    import os
    import re
    
    # 1. 确定 talker_wxid
    talker_wxid = friend_wxid.strip() if friend_wxid else ""
    if not talker_wxid:
        try:
            clean_session_id = re.sub(r'[\(\[\uff08]\d+[\)\]\uff09]$', '', friend_name).strip()
            from src.utils.contacts_cache import contacts_cache
            friends = contacts_cache.get_friends(bot_wxid) or []
            for f in friends:
                if f.get("name") == friend_name or f.get("alias") == friend_name or f.get("wxid") == friend_name:
                    talker_wxid = f.get("wxid")
                    break
            if not talker_wxid:
                groups = contacts_cache.get_groups(bot_wxid) or []
                for g in groups:
                    if g.get("name") == friend_name or g.get("wxid") == friend_name:
                        talker_wxid = g.get("wxid")
                        break
        except Exception as e:
            logger.warning(f"[知识库] 转换好友微信ID异常: {e}")
            
    if not talker_wxid:
        talker_wxid = friend_name

    db_msgs = []
    
    # 1. 检测 DLL 模式
    try:
        from src.wechat_4x.wcdb_monitor import get_wcdb_monitor
        monitor = get_wcdb_monitor(bot_wxid)
        if monitor and monitor.is_active():
            logger.info(f"[知识库] 检测到 DLL 数据库连接在线，开始读取 {talker_wxid} 的历史记录...")
            db_msgs = monitor.get_latest_messages(talker_wxid, limit=limit)
    except Exception as e:
        logger.debug(f"[知识库] DLL 数据库读取异常: {e}")
        
    # 2. 检测 Python 影子拷贝模式
    if not db_msgs:
        try:
            hex_key = os.environ.get("WCDB_HEX_KEY", "") or os.environ.get("WECHAT_4X_KEY_HEX", "")
            if hex_key:
                logger.info(f"[知识库] 检测到影子拷贝配置在线，开始读取 {talker_wxid} 影子库历史记录...")
                from src.wechat_4x.db_message_monitor import MessageDbFallbackMonitor
                temp_sync = MessageDbFallbackMonitor(bot_wxid)
                session_db = os.environ.get("WCDB_SESSION_DB_PATH", "")
                if not session_db:
                    from src.wechat_4x.db_match_helper import auto_detect_db_path
                    session_db = auto_detect_db_path(hex_key, bot_wxid)
                if session_db and os.path.exists(session_db):
                    temp_sync._db_path = os.path.join(os.path.dirname(os.path.dirname(session_db)), "message", "message_0.db")
                    temp_sync._msg_dir = os.path.join(os.path.dirname(os.path.dirname(session_db)), "message")
                    temp_sync._hex_key = hex_key
                    db_msgs = temp_sync.get_latest_messages(talker_wxid, limit=limit)
        except Exception as e:
            logger.debug(f"[知识库] 影子库读取异常: {e}")
            
    if not db_msgs:
        return []

    # 排序：时间由旧到新
    db_msgs = sorted(db_msgs, key=lambda x: x.get("timestamp", 0))
    
    result = []
    for m in db_msgs:
        is_self = m.get("is_self", False)
        content = m.get("content", "").strip()
        if not content:
            continue
        sender = nickname if is_self else friend_name
        result.append({
            "content": content,
            "isSelf": is_self,
            "sender": sender,
            "type": "text",
        })
    return result


def do_collect(target_drv, friend_name: str, industry_id: str,
               max_scroll: int, bot_wxid: str, task_id: int, friend_wxid: str = ""):
    """实际的聊天记录采集工作（在线程中执行）"""
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:
        pass

    try:
        logger.info(f"[知识库] 开始采集 [{friend_name}] 的聊天记录 (行业: {industry_id})")
        collect_state["status"] = "running"
        cloud_update_task(task_id, status="running")

        # 尝试走数据库采集
        db_limit = max_scroll * 20
        if db_limit > 5000:
            db_limit = 5000
        if db_limit <= 0:
            db_limit = 500
            
        nickname = target_drv._nickname or "我"
        all_raw_messages = get_chat_history_from_db(bot_wxid, friend_name, friend_wxid, db_limit, nickname)
        
        if all_raw_messages:
            logger.info(f"[知识库] 成功走数据库采集模式，获取到 {len(all_raw_messages)} 条记录")
            collect_state["total"] = len(all_raw_messages)
            collect_state["processed"] = len(all_raw_messages)
        else:
            logger.info(f"[知识库] 数据库采集不可用或无记录，降级回退到 UIA 模式...")
            
            # --- UIA 兜底模式 ---
            if not target_drv.ChatWith(friend_name):
                raise RuntimeError(f"无法打开与 [{friend_name}] 的聊天窗口")

            time.sleep(0.5)

            # 1. 在微信主窗口中定位聊天列表控件 (MESSAGE_LIST = "消息")
            msg_list = None
            for _ in range(5):
                msg_list = target_drv._walk_find('ListControl', name='消息', class_name='mmui::RecyclerListView', max_depth=8) or \
                           target_drv._walk_find('ListControl', name='消息', max_depth=8)
                if msg_list and msg_list.Exists(0.1):
                    break
                time.sleep(0.2)

            if not msg_list:
                raise RuntimeError("未在微信主聊天窗口中找到消息列表容器")

            # 2. 聚焦至消息列表以使翻页滚动生效
            try:
                msg_list.SetFocus()
                msg_list.Click()
                time.sleep(0.2)
            except Exception as click_err:
                logger.warning(f"[知识库] 无法聚焦至消息列表: {click_err}")

            # 3. 向上滑动获取并解析消息
            seen_contents = set()

            import re
            # 用正则匹配并过滤掉聊天记录行名称末尾的时间后缀
            time_suffix_pattern = re.compile(r'\s+\d{4}年\d{1,2}月\d{1,2}日\s+\d{1,2}:\d{2}$')

            def _collect_history_visible():
                if not msg_list or not msg_list.Exists(0.1):
                    return 0
                
                new_count = 0
                for item in msg_list.GetChildren():
                    try:
                        from src.uia.message import parse_message
                        parsed = parse_message(item)
                        if not parsed:
                            continue
                        content = parsed.get("content", "")
                        msg_type = parsed.get("type", "")
                        is_self = parsed.get("isSelf", False)

                        # 过滤跳过非文本且无实质价值的消息类型
                        if msg_type in ('system', 'greet', 'recall', 'time') or not content:
                            continue

                        # 剔除掉可能附带的时间戳字符串，还原纯净聊天正文
                        if msg_type == "text":
                            content = time_suffix_pattern.sub('', content).strip()

                        sender = nickname if is_self else friend_name
                        
                        dedup_key = f"{sender}:{content}"
                        if dedup_key not in seen_contents and content:
                            seen_contents.add(dedup_key)
                            all_raw_messages.append({
                                "content": content, "isSelf": is_self,
                                "sender": sender, "type": "text",
                            })
                            new_count += 1
                    except Exception:
                        continue
                return new_count

            _collect_history_visible()

            # 4. 循环向上滑动加载历史消息
            # 提示：主聊天窗口中最新消息在底，向上滚动（WheelUp）才是获取历史聊天记录
            no_new_streak = 0
            for scroll_i in range(max_scroll):
                if stop_flag.is_set():
                    logger.info("[知识库] 收到停止信号，终止采集")
                    collect_state["status"] = "stopped"
                    cloud_update_task(task_id, status="failed", error_msg="用户手动终止")
                    return

                try:
                    msg_list.SetFocus()
                    msg_list.WheelUp(wheelTimes=3)
                except Exception:
                    try:
                        import uiautomation as uia
                        uia.SendKeys('{PageUp}')
                    except Exception:
                        pass

                time.sleep(0.8)
                new_count = _collect_history_visible()
                collect_state["total"] = len(all_raw_messages)

                if new_count == 0:
                    no_new_streak += 1
                    if no_new_streak >= 3:
                        break
                else:
                    no_new_streak = 0

                collect_state["processed"] = scroll_i + 1


        # 使用知识提取器处理
        from src.ai.knowledge_extractor import KnowledgeExtractor
        extractor = KnowledgeExtractor(dedup=True)
        
        # 详细打印采集到的原始消息数据，便于排查提取过滤或者 isSelf 判定原因
        logger.info(f"[知识库] 采集完成，准备开始提取。当前共采集到原始消息 {len(all_raw_messages)} 条。详细消息数据: {all_raw_messages}")
        
        qa_pairs = extractor.extract_qa_pairs(all_raw_messages)
        logger.info(f"[知识库] 采集完成: {len(all_raw_messages)} 条消息 → {len(qa_pairs)} 对 Q&A")

        # 脱敏处理
        for qa in qa_pairs:
            qa["question"] = extractor.desensitize(qa["question"])
            qa["answer"] = extractor.desensitize(qa["answer"])
            qa["context"] = extractor.desensitize(qa.get("context", ""))

        # 批量上传
        if qa_pairs:
            batch_size = 50
            total_uploaded = 0
            for i in range(0, len(qa_pairs), batch_size):
                batch = qa_pairs[i:i + batch_size]
                total_uploaded += cloud_upload_entries(task_id, industry_id, friend_name, batch, bot_wxid)
            collect_state["extracted"] = total_uploaded

        collect_state["status"] = "done"
        cloud_update_task(task_id, status="done",
                          total_messages=len(all_raw_messages), processed=len(all_raw_messages))

    except Exception as e:
        logger.error(f"[知识库] 采集异常: {e}", exc_info=True)
        collect_state["status"] = "failed"
        collect_state["error"] = str(e)
        cloud_update_task(task_id, status="failed", error_msg=str(e))
    finally:
        collect_state["running"] = False

