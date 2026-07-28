"""
好友与通讯录防封灾备 API（从原 friend_api.py 拆分以对齐单文件 300 行限额）
"""
import logging
import os
import json
import time
import datetime
from fastapi import APIRouter, Request
from src.utils.response import ok, err

logger = logging.getLogger(__name__)
router = APIRouter()


def get_avatar_base64(wxid: str) -> str:
    """获取好友本地头像的 Base64 编码"""
    if not wxid:
        return ""
    from src.crm.account_data import ACCOUNTS_DIR
    avatar_path = os.path.join(ACCOUNTS_DIR, f"{wxid}.png")
    if os.path.exists(avatar_path):
        try:
            import base64
            with open(avatar_path, "rb") as image_file:
                return base64.b64encode(image_file.read()).decode('utf-8')
        except Exception:
            pass
    return ""


def save_avatar_base64(wxid: str, avatar_base64: str):
    """解码 Base64 并写回本地头像文件"""
    if not avatar_base64 or not wxid:
        return
    from src.crm.account_data import ACCOUNTS_DIR
    os.makedirs(ACCOUNTS_DIR, exist_ok=True)
    avatar_path = os.path.join(ACCOUNTS_DIR, f"{wxid}.png")
    try:
        import base64
        if "," in avatar_base64:
            avatar_base64 = avatar_base64.split(",")[1]
        img_data = base64.b64decode(avatar_base64)
        if not os.path.exists(avatar_path) or os.path.getsize(avatar_path) != len(img_data):
            with open(avatar_path, "wb") as f:
                f.write(img_data)
    except Exception as e:
        logger.error(f"写入还原头像失败 {wxid}: {e}")


@router.get("/api/friend/backup/export")
async def export_friend_backup():
    """导出当前登录微信号的所有好友及画像备注备份（用于防封灾备）"""
    try:
        from src.utils.contacts_cache import contacts_cache
        from src.crm.account_data import get_active_account
        
        active_id = get_active_account()
        if not active_id:
            return err(40000, "未检测到当前活动微信号，请先登录微信")
            
        friends = contacts_cache.get_friends(active_id)
        if not friends:
            return ok({"bot_wxid": active_id, "data": [], "total": 0, "message": "当前微信号暂无好友缓存"})
            
        # 仅保留真实的好友，清洗掉系统公众号等
        sys_prefixes = ("新的朋友", "公众号", "企业微信联系人", "群聊", "标签", "服务号", "我的企业", "联系人", "文件传输助手")
        backup_list = []
        for f in friends:
            name = f.get("name", "").strip()
            if not name:
                continue
            is_sys = False
            if len(name) == 1 and name.isascii() and name.isalpha():
                is_sys = True
            for pre in sys_prefixes:
                if name.startswith(pre) or name == pre:
                    is_sys = True
                    break
            if is_sys:
                continue
                
            tags_val = f.get("tags") or []
            if isinstance(tags_val, list):
                tag_str = ",".join(tags_val)
            else:
                tag_str = str(tags_val)

            wxid = f.get("wxid", "")
            item_data = {
                "wxid": wxid,
                "alias": f.get("alias", ""),
                "name": name,
                "nickname": f.get("nickname", ""),
                "remark": f.get("remark", ""),
                "tag": tag_str,
                "region": f.get("region", ""),
                "source": f.get("source", ""),
                "signature": f.get("signature", "")
            }
            
            # 完整备份头像
            av_b64 = get_avatar_base64(wxid)
            if av_b64:
                item_data["avatar_base64"] = av_b64
                
            backup_list.append(item_data)
            
        return ok({
            "bot_wxid": active_id,
            "total": len(backup_list),
            "data": backup_list
        })
    except Exception as e:
        logger.error(f"导出好友备份失败: {e}")
        return err(40000, f"导出失败: {str(e)}")


@router.post("/api/friend/backup/import")
async def import_friend_backup(request: Request):
    """从备份中导入联系人到加好友队列，用于新号一键重建（封号保障箱）"""
    try:
        data = await request.json()
        backup_data = data.get("data", [])
        if not backup_data:
            return err(40000, "导入数据不能为空")
            
        from src.friend import friend_queue
        
        # 转换为 friend_queue 格式
        contacts_to_import = []
        for index, item in enumerate(backup_data):
            wxid = item.get("wxid") or item.get("alias")
            if not wxid:
                continue
                
            # 还原头像文件缓存
            av_b64 = item.get("avatar_base64")
            if av_b64:
                save_avatar_base64(wxid, av_b64)
                
            remark = item.get("remark") or ""
            name = item.get("name") or ""
            tag_str = item.get("tag") or ""
            
            # 解析标签
            tags = [t.strip() for t in tag_str.split(",") if t.strip()] if isinstance(tag_str, str) else (tag_str or [])
            
            contacts_to_import.append({
                "wechat_id": wxid,
                "company_name": name,
                "legal_person": remark,
                "row_index": index + 1,
                "extra_fields": {"is_backup_restore": True, "original_name": name, "original_remark": remark}
            })
            
        if not contacts_to_import:
            return err(40000, "没有提取到有效的待重建联系人数据")
            
        import_batch_id = f"restore_{int(time.time())}"
        import_result = friend_queue.import_contacts(
            contacts_to_import,
            source_file="防封灾备重建",
            original_filename="backup_restore.json",
            tags=["灾备重建"],
            import_batch_id=import_batch_id
        )
        
        return ok({
            "imported": import_result.get("imported", 0),
            "skipped": import_result.get("skipped", 0),
            "batch_id": import_batch_id,
            "total_in_queue": friend_queue.get_queue_stats().get("pending", 0)
        })
    except Exception as e:
        logger.error(f"导入备份失败: {e}")
        return err(40000, f"导入失败: {str(e)}")


@router.get("/api/friend/backup/history")
async def get_backup_history():
    """获取所有自动备份和手动备份历史记录"""
    try:
        from src.crm.account_data import get_active_account
        active_id = get_active_account()
        
        from src.monitor.chat_monitor.auto_backup import BACKUP_DIR
        if not os.path.exists(BACKUP_DIR):
            return ok([])
            
        files = os.listdir(BACKUP_DIR)
        history = []
        for file in files:
            if not file.endswith(".json") or file == "manifest.json":
                continue
                
            filepath = os.path.join(BACKUP_DIR, file)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                    
                bot_wxid = meta.get("bot_wxid", "未知")
                
                # 如果有活动微信号，过滤出属于当前微信号的备份文件
                if active_id and bot_wxid != active_id:
                    continue
                    
                created_at = meta.get("created_at")
                if not created_at:
                    stat = os.stat(filepath)
                    created_at = datetime.datetime.fromtimestamp(stat.st_mtime).isoformat()
                    
                history.append({
                    "filename": file,
                    "bot_wxid": bot_wxid,
                    "created_at": created_at,
                    "type": meta.get("type", "manual"),
                    "total": meta.get("total", len(meta.get("data", [])))
                })
            except Exception as fe:
                logger.error(f"解析备份文件 '{file}' 失败: {fe}")
                
        # 按创建时间倒序排列
        history.sort(key=lambda x: x["created_at"], reverse=True)
        return ok(history)
    except Exception as e:
        logger.error(f"获取备份历史失败: {e}")
        return err(40000, f"获取备份历史失败: {str(e)}")


@router.get("/api/friend/backup/history/detail")
async def get_backup_history_detail(filename: str):
    """查看具体某次备份的联系人明细"""
    try:
        from src.monitor.chat_monitor.auto_backup import BACKUP_DIR
        
        # 安全检查，防止路径穿越
        filename = os.path.basename(filename)
        filepath = os.path.join(BACKUP_DIR, filename)
        if not os.path.exists(filepath):
            return err(40000, "备份文件不存在")
            
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        return ok(data)
    except Exception as e:
        logger.error(f"获取备份详情失败: {e}")
        return err(40000, f"获取备份详情失败: {str(e)}")


@router.post("/api/friend/backup/history/restore")
async def restore_from_backup_file(request: Request):
    """选择备份记录执行一键灾备重建"""
    try:
        body = await request.json()
        filename = body.get("filename")
        if not filename:
            return err(40000, "参数 filename 不能为空")
            
        filename = os.path.basename(filename)
        from src.monitor.chat_monitor.auto_backup import BACKUP_DIR
        filepath = os.path.join(BACKUP_DIR, filename)
        if not os.path.exists(filepath):
            return err(40000, "备份文件不存在")
            
        with open(filepath, "r", encoding="utf-8") as f:
            backup_data = json.load(f)
            
        data_list = backup_data.get("data", [])
        if not data_list:
            return err(40000, "该备份记录内无可重建联系人数据")
            
        from src.friend import friend_queue
        
        contacts_to_import = []
        for index, item in enumerate(data_list):
            wxid = item.get("wxid") or item.get("alias")
            if not wxid:
                continue
                
            # 还原头像文件缓存
            av_b64 = item.get("avatar_base64")
            if av_b64:
                save_avatar_base64(wxid, av_b64)
                
            remark = item.get("remark") or ""
            name = item.get("name") or ""
            tag_str = item.get("tag") or ""
            
            tags = [t.strip() for t in tag_str.split(",") if t.strip()] if isinstance(tag_str, str) else (tag_str or [])
            
            contacts_to_import.append({
                "wechat_id": wxid,
                "company_name": name,
                "legal_person": remark,
                "row_index": index + 1,
                "extra_fields": {"is_backup_restore": True, "original_name": name, "original_remark": remark}
            })
            
        if not contacts_to_import:
            return err(40000, "没有提取到有效的待重建联系人数据")
            
        import_batch_id = f"restore_hist_{int(time.time())}"
        import_result = friend_queue.import_contacts(
            contacts_to_import,
            source_file=f"历史备份_{filename}",
            original_filename=filename,
            tags=["灾备重建"],
            import_batch_id=import_batch_id
        )
        
        return ok({
            "imported": import_result.get("imported", 0),
            "skipped": import_result.get("skipped", 0),
            "batch_id": import_batch_id,
            "total_in_queue": friend_queue.get_queue_stats().get("pending", 0)
        })
    except Exception as e:
        logger.error(f"历史备份重建失败: {e}")
        return err(40000, f"重建失败: {str(e)}")


@router.post("/api/friend/backup/open-dir")
async def open_backup_dir():
    """在系统资源管理器中打开备份文件夹"""
    try:
        from src.monitor.chat_monitor.auto_backup import BACKUP_DIR
        if not os.path.exists(BACKUP_DIR):
            os.makedirs(BACKUP_DIR, exist_ok=True)
            
        import platform
        import subprocess
        system_name = platform.system()
        if system_name == "Windows":
            os.startfile(BACKUP_DIR)
        elif system_name == "Darwin":  # macOS
            subprocess.Popen(["open", BACKUP_DIR])
        else:  # Linux / Unix
            subprocess.Popen(["xdg-open", BACKUP_DIR])
            
        return ok({"message": "已在系统资源管理器中打开备份文件夹"})
    except Exception as e:
        logger.error(f"打开备份文件夹失败: {e}")
        return err(40000, f"打开失败: {str(e)}")

