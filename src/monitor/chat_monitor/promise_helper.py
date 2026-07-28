import logging
from datetime import datetime, timedelta
from typing import Any
from src.utils.db_manager import WeChatDBManager

logger = logging.getLogger(__name__)

def register_promise_tasks_from_reply(name: str, user_name: str, account_id: str, reply: str, file_to_send: str = None) -> bool:
    """分析AI回复文本及物料参数，识别承诺动作并自动向承诺任务池注册待办任务"""
    try:
        db = WeChatDBManager()
        
        reply_lower = reply.lower()
        has_video_promise = any(
            kw in reply_lower 
            for kw in ["录制", "实时录", "演示视频", "操作视频", "实操视频", "录个视频", "视频发给你", "录屏"]
        )
        
        has_material_promise = any(
            kw in reply_lower
            for kw in ["专属系统功能与拓客营销物料", "系统功能介绍与拓客营销物料", "全自动拓客获客流程图", "专属功能图", "发资料", "发白皮书", "发文档", "把资料发给", "把文档发给", "发送文档", "发送文件", "发给你", "资料发给", "手册发给"]
        )
        
        # 1. 优先创建录屏任务
        if has_video_promise:
            now = datetime.now()
            duplicate = False
            for t in db.get_promise_tasks():
                if (t.get("target_wxid") == name and 
                    t.get("task_type") == "send_live_record" and 
                    t.get("status") in ("pending", "processing")):
                    c_time = datetime.fromisoformat(t.get("created_at"))
                    if now - c_time < timedelta(minutes=5):
                        duplicate = True
                        break
            
            if not duplicate:
                db.add_promise_task({
                    "target_wxid": name,
                    "target_name": user_name or name,
                    "account_id": account_id,
                    "task_type": "send_live_record",
                    "reply_text": reply,
                    "status": "pending",
                    "retry_count": 0,
                    "created_at": datetime.now().isoformat()
                })
                logger.info(f"[承诺任务提取] 成功提取到录屏承诺，已为客户 {user_name or name} 创建后台待办任务")
                return True
                
        # 2. 如果没有录屏任务但有物料任务
        elif has_material_promise:
            materials_path = file_to_send if (file_to_send and file_to_send != "__live_record__") else None
                
            duplicate = False
            for t in db.get_promise_tasks():
                if (t.get("target_wxid") == name and 
                    t.get("task_type") == "send_materials" and 
                    t.get("status") in ("pending", "processing")):
                    duplicate = True
                    break
                    
            if not duplicate:
                db.add_promise_task({
                    "target_wxid": name,
                    "target_name": user_name or name,
                    "account_id": account_id,
                    "task_type": "send_materials",
                    "materials_path": materials_path,
                    "reply_text": reply,
                    "status": "pending",
                    "retry_count": 0,
                    "created_at": datetime.now().isoformat()
                })
                logger.info(f"[承诺任务提取] 成功提取到物料承诺，已为客户 {user_name or name} 创建后台待办任务")
                return True

        # 3. 动态检查自定义能力承诺
        else:
            capabilities = db.get_fulfillment_capabilities()
            for cap in capabilities:
                if cap.get("is_custom") and cap.get("enabled"):
                    config = cap.get("config") or {}
                    keywords_str = config.get("intent_keywords", "")
                    if keywords_str:
                        keywords = [k.strip().lower() for k in keywords_str.split(",") if k.strip()]
                        if any(kw in reply_lower for kw in keywords):
                            task_type = cap.get("key")
                            duplicate = False
                            for t in db.get_promise_tasks():
                                if (t.get("target_wxid") == name and 
                                    t.get("task_type") == task_type and 
                                    t.get("status") in ("pending", "processing")):
                                    duplicate = True
                                    break
                            if not duplicate:
                                db.add_promise_task({
                                    "target_wxid": name,
                                    "target_name": user_name or name,
                                    "account_id": account_id,
                                    "task_type": task_type,
                                    "reply_text": reply,
                                    "status": "pending",
                                    "retry_count": 0,
                                    "created_at": datetime.now().isoformat()
                                })
                                logger.info(f"[承诺任务提取] 成功提取到自定义物理承诺【{cap.get('name')}】，已为客户 {user_name or name} 创建后台待办任务")
                                return True

    except Exception as e:
        logger.error(f"[承诺任务提取] 识别或注册待办承诺任务异常: {e}", exc_info=True)
    return False



async def handle_remote_approval_command(message: str, engine: Any = None) -> tuple[bool, str]:
    """
    处理来自微信文件传输助手的远程控制与人工审批指令。
    返回 (is_command, response_text)
    """
    import re
    from src.utils.websocket_manager import ws_manager
    msg_clean = message.strip()

    # 0. 远程快捷加白/黑名单指令
    add_white_match = re.match(r'^(加白前缀|加白|加黑前缀|加黑)\s*[:：]?\s*(.+)$', msg_clean, re.IGNORECASE)
    if add_white_match:
        cmd_type = add_white_match.group(1)
        target_val = add_white_match.group(2).strip()
        if not target_val:
            return True, "❌ 操作失败：加白/加黑对象不能为空。"
        try:
            account_id = "default"
            if engine:
                account_id = getattr(engine.driver, 'bot_wxid', None) or getattr(engine.driver, '_wxid', None) or 'default'
            
            from src.crm.account_data import get_account_settings, save_account_settings
            from src.utils.contacts_cache import contacts_cache
            
            is_group = "@chatroom" in target_val or target_val.endswith("@chatroom")
            is_prefix_cmd = "前缀" in cmd_type
            
            found_wxid = None
            if not is_prefix_cmd:
                import asyncio
                loop = asyncio.get_running_loop()
                found_wxid = await loop.run_in_executor(None, contacts_cache.find_wxid_with_db_sync, account_id, target_val, is_group)
            
            settings = get_account_settings(account_id, force_reload=True)
            reply = settings.get("reply", {})
            
            if "加白" in cmd_type:
                whitelist_key = "auto_chat_group_whitelist" if is_group else "auto_chat_friend_whitelist"
                lst = list(reply.get(whitelist_key, []))
                
                if is_prefix_cmd:
                    target_id = f"prefix:{target_val}"
                else:
                    target_id = f"wxid:{found_wxid}" if found_wxid else f"namecat:{target_val}::{'群聊' if is_group else '联系人'}"
                
                excludes_key = "auto_chat_group_excludes" if is_group else "auto_chat_friend_excludes"
                ex_lst = list(reply.get(excludes_key, []))
                
                variants = {target_val, target_id}
                if found_wxid:
                    variants.add(f"wxid:{found_wxid}")
                    variants.add(found_wxid)
                variants.add(f"namecat:{target_val}::{'群聊' if is_group else '联系人'}")
                
                new_ex_lst = [x for x in ex_lst if x not in variants]
                if len(new_ex_lst) != len(ex_lst):
                    reply[excludes_key] = new_ex_lst
                    
                if target_id not in lst:
                    lst.append(target_id)
                    reply[whitelist_key] = lst
                    
                settings["reply"] = reply
                save_account_settings(settings, account_id)
                
                if not is_prefix_cmd:
                    try:
                        from src.api.friend_filter_api import trigger_whitelist_retry
                        import asyncio
                        asyncio.create_task(trigger_whitelist_retry(account_id, target_val, is_group, found_wxid))
                    except Exception as retry_ex:
                        logger.error(f"[FileHelper Remote] 触发加白重试异常: {retry_ex}")
                
                msg_suffix = f" (锁定WXID: {found_wxid})" if found_wxid else ""
                if is_prefix_cmd:
                    msg_suffix = " (前缀匹配规则)"
                return True, f"✅ 已成功将「{target_val}」加入白名单{msg_suffix}。"
            else:
                excludes_key = "auto_chat_group_excludes" if is_group else "auto_chat_friend_excludes"
                lst = list(reply.get(excludes_key, []))
                
                if is_prefix_cmd:
                    target_id = f"prefix:{target_val}"
                else:
                    target_id = f"wxid:{found_wxid}" if found_wxid else f"namecat:{target_val}::{'群聊' if is_group else '联系人'}"
                
                whitelist_key = "auto_chat_group_whitelist" if is_group else "auto_chat_friend_whitelist"
                wl_lst = list(reply.get(whitelist_key, []))
                
                variants = {target_val, target_id}
                if found_wxid:
                    variants.add(f"wxid:{found_wxid}")
                    variants.add(found_wxid)
                variants.add(f"namecat:{target_val}::{'群聊' if is_group else '联系人'}")
                
                new_wl_lst = [x for x in wl_lst if x not in variants]
                if len(new_wl_lst) != len(wl_lst):
                    reply[whitelist_key] = new_wl_lst
                    
                if target_id not in lst:
                    lst.append(target_id)
                    reply[excludes_key] = lst
                    
                settings["reply"] = reply
                save_account_settings(settings, account_id)
                
                msg_suffix = f" (锁定WXID: {found_wxid})" if found_wxid else ""
                if is_prefix_cmd:
                    msg_suffix = " (前缀排除规则)"
                return True, f"🚫 已成功将「{target_val}」加入黑名单{msg_suffix}。"
        except Exception as add_ex:
            logger.error(f"[FileHelper Remote] 快捷加白黑名单异常: {add_ex}", exc_info=True)
            return True, f"❌ 操作失败: {add_ex}"

    # 1. 列表指令
    list_keywords = ["查看待审批", "审批列表", "待审批", "待审批列表", "list"]
    if msg_clean.lower() in list_keywords:
        try:
            db = WeChatDBManager()
            tasks = db.get_promise_tasks()
            # 过滤出待审批的任务
            pending = [t for t in tasks if t.get("status") == "pending_approval"]
            
            if not pending:
                return True, "💡 暂无需要审批的高危物理承诺任务。"
                
            resp_lines = [f"📋 待审批物理承诺任务列表 (共 {len(pending)} 个)：\n"]
            for idx, t in enumerate(pending, 1):
                task_id = t.get("id", "")
                task_type = t.get("task_type", "")
                target_name = t.get("target_name") or t.get("target_wxid", "")
                reply = t.get("reply_text") or ""
                
                # 动态获取已注册能力的中文名称，支持自定义能力
                capabilities = db.get_fulfillment_capabilities()
                cap_info = next((c for c in capabilities if c.get("key") == task_type), None)
                if cap_info:
                    type_cn = cap_info.get("name", "未命名能力")
                else:
                    type_cn = "未知任务"

                    
                resp_lines.append(
                    f"{idx}️⃣ 任务ID: {task_id}\n"
                    f"   类型: {type_cn}\n"
                    f"   申请人: @{target_name}\n"
                    f"   背景承诺: \"{reply[:40]}...\"\n"
                )
            
            resp_lines.append(
                "👉 回复「批准 任务ID」或「通过 任务ID」来授权执行该任务。\n"
                "👉 回复「驳回 任务ID」或「拒绝 任务ID」来作废并移除任务。"
            )
            return True, "\n".join(resp_lines)
        except Exception as ex:
            logger.error(f"[FileHelper Remote] 获取审批列表异常: {ex}", exc_info=True)
            return True, f"❌ 获取待审批列表失败: {ex}"

    # 2. 批准指令
    approve_match = re.match(r'^(批准|通过|同意|approve|ok)\s*(.+)$', msg_clean, re.IGNORECASE)
    if approve_match:
        target_id_input = approve_match.group(2).strip()
        try:
            db = WeChatDBManager()
            tasks = db.get_promise_tasks()
            pending = [t for t in tasks if t.get("status") == "pending_approval"]
            
            target_task = None
            for t in pending:
                t_id = t.get("id", "")
                if t_id == target_id_input or t_id.endswith(target_id_input):
                    target_task = t
                    break
                    
            if not target_task:
                return True, f"❌ 未找到匹配的任务 ID: {target_id_input}。请发送「查看待审批」核对ID。"
                
            task_id = target_task.get("id")
            success = db.update_promise_task(task_id, {
                "status": "pending",
                "approval_status": "approved",
                "error_message": ""
            })
            if success:
                # 广播 WS 事件同步前端
                await ws_manager.broadcast_json({
                    "type": "promise_approval_change",
                    "task_id": task_id,
                    "status": "approved"
                })
                return True, f"✅ 已成功批准任务【{task_id}】。机器人正在串行队列中排队调度执行此动作！"
            return True, "❌ 数据库更新状态失败，请稍后重试。"
        except Exception as ex:
            logger.error(f"[FileHelper Remote] 批准任务异常: {ex}", exc_info=True)
            return True, f"❌ 批准任务失败: {ex}"

    # 3. 驳回指令
    deny_match = re.match(r'^(驳回|拒绝|deny|cancel)\s*(.+)$', msg_clean, re.IGNORECASE)
    if deny_match:
        target_id_input = deny_match.group(2).strip()
        try:
            db = WeChatDBManager()
            tasks = db.get_promise_tasks()
            pending = [t for t in tasks if t.get("status") == "pending_approval"]
            
            target_task = None
            for t in pending:
                t_id = t.get("id", "")
                if t_id == target_id_input or t_id.endswith(target_id_input):
                    target_task = t
                    break
                    
            if not target_task:
                return True, f"❌ 未找到匹配的任务 ID: {target_id_input}。"
                
            task_id = target_task.get("id")
            success = db.update_promise_task(task_id, {
                "status": "failed",
                "approval_status": "denied",
                "finished_at": datetime.now().isoformat(),
                "error_message": "管理员通过手机微信远程拒绝执行"
            })
            if success:
                # 广播 WS 事件同步前端
                await ws_manager.broadcast_json({
                    "type": "promise_approval_change",
                    "task_id": task_id,
                    "status": "denied"
                })
                return True, f"🚫 已成功拒绝并作废任务【{task_id}】。"
            return True, "❌ 数据库更新状态失败，请稍后重试。"
        except Exception as ex:
            logger.error(f"[FileHelper Remote] 拒绝任务异常: {ex}", exc_info=True)
            return True, f"❌ 拒绝任务失败: {ex}"

    return False, ""
