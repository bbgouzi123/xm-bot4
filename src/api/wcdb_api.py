"""
WCDB 数据库密钥提取相关 API 接口
"""
import os
import threading
import logging
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from src.utils.response import ok, err

logger = logging.getLogger("WcdbApi")
router = APIRouter(prefix="/api/wcdb", tags=["wcdb"])

_extract_lock = threading.Lock()
_extract_logs = []
_extract_active = False
_extracted_key = None

@router.get("/extract-progress")
async def get_wcdb_extract_progress(instance_id: str = None):
    """获取当前密钥提取进度日志"""
    global _extract_logs, _extract_active, _extracted_key
    
    # 当密钥提取任务在活跃运行中时，绝对不要返回老密钥或环境变量里的密钥，
    # 否则前端一轮询就会误以为提取已完成，从而把重启微信中的提取终端 Dialog 自动关闭
    if _extract_active:
        key = _extracted_key
    else:
        # 优先获取内存中刚提取的 key，如果没有，尝试从 KeyStore/环境变量中恢复
        key = _extracted_key
        if not key:
            key = os.environ.get("WECHAT_4X_KEY_HEX") or os.environ.get("WCDB_HEX_KEY")
        if not key and instance_id:
            try:
                from src.utils.instance_manager import InstanceManagerV2
                from src.utils.wechat_key_store import get_persisted_wechat_key
                manager = InstanceManagerV2.get_instance()
                inst_data = manager.get_all_instances().get(instance_id)
                if inst_data:
                    target_wxid = inst_data.get("wxid")
                    persisted_key = get_persisted_wechat_key(target_wxid)
                    if persisted_key and len(persisted_key) == 64:
                        key = persisted_key
            except Exception:
                pass

    return ok({
        "active": _extract_active,
        "logs": _extract_logs,
        "key": key
    })

@router.get("/key-status")
async def get_wcdb_key_status():
    """获取本地 WCDB 密钥状态"""
    from src.utils.instance_manager import InstanceManagerV2
    from src.utils.wechat_key_store import get_persisted_wechat_key

    # 🌟 核心拦截：如果当前没有任何在线托管 of 微信实例，强制设为 false
    manager = InstanceManagerV2.get_instance()
    active_inst = manager.get_active_instance()
    if not active_inst:
        return ok({
            "has_key": False,
            "key_preview": None
        })

    active_wxid = active_inst.get("wxid") or manager.get_active_instance_id() or "default"
    hex_key = get_persisted_wechat_key(active_wxid)

    # 【优化】平常获取密钥状态时，只要 hex_key 存在且长度/格式正确即可直接返回，
    # 彻底避免重度 CPU 哈希 (PBKDF2 HMAC 256000次迭代) 解密计算阻塞 FastAPI 主事件循环与 GIL 锁。
    # 真正的解密匹配会在触发同步通讯录 (/sync-contacts) 时执行。
    has_key = False
    if hex_key and len(hex_key) == 64:
        try:
            bytes.fromhex(hex_key)
            has_key = True
        except ValueError:
            pass

    return ok({
        "has_key": has_key,
        "key_preview": f"{hex_key[:6]}******{hex_key[-6:]}" if has_key and hex_key else None
    })

@router.post("/extract-key")
async def extract_wcdb_key(request: Request):
    """强杀微信并重新注入 Hook 提取密钥"""
    global _extract_logs, _extract_active, _extracted_key
    _extracted_key = None
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    
    instance_id = body.get("instance_id")
    if not instance_id:
        # 🌟 兜底：如果未显式传递，尝试使用当前活跃的实例
        from src.utils.instance_manager import InstanceManagerV2
        manager = InstanceManagerV2.get_instance()
        instance_id = manager.get_active_instance_id()

    if not _extract_lock.acquire(blocking=False):
        return err(400, "已有密钥提取任务正在运行，请勿重复操作")
        
    _extract_logs = []
    _extract_active = True
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        
        def run_extraction():
            try:
                from src.wechat_4x.wechat_hook_controller import WeChatHookController
                controller = WeChatHookController()
                
                def log_cb(msg):
                    _extract_logs.append(msg)
                    
                key = controller.auto_get_key(timeout=90, instance_id=instance_id, log_cb=log_cb)
                if key and len(key) == 64:
                    global _extracted_key
                    _extracted_key = key
                    os.environ["WECHAT_4X_KEY_HEX"] = key
                    os.environ["WCDB_HEX_KEY"] = key
                    os.environ["WECHAT_4X_KEY_HEX_DYNAMIC"] = "1"
                    
                    # 确定对应的 wxid
                    target_wxid = None
                    if instance_id:
                        from src.utils.instance_manager import InstanceManagerV2
                        manager = InstanceManagerV2.get_instance()
                        inst_data = manager.get_all_instances().get(instance_id)
                        if inst_data:
                            target_wxid = inst_data.get("wxid")
                    
                    # 同步写入 key_store 持久化，并优先绑定到该账号的 wxid
                    try:
                        from src.utils.wechat_key_store import persist_wechat_key
                        persist_wechat_key(key, target_wxid)
                        
                        # 联动更新共享内存状态，将 has_key 更新为 True，防止前端加载异常
                        if instance_id and target_wxid:
                            manager.update_instance(instance_id, {"has_key": True})
                    except Exception as ks_err:
                        logger.error(f"[WCDB API] 写入 KeyStore 异常: {ks_err}")

                    # ✅ 密钥提取成功后，将操作账号（instance_id）设为活跃实例。
                    # 用户点击某账号的「数据通道」→ 提取密钥成功 → 该账号就应该是 active。
                    # 不能依赖后续 do_scan_sync 的扫描结果来决定 active_id，因为扫描顺序不确定。
                    if instance_id:
                        try:
                            manager.set_active_instance(instance_id)
                            if target_wxid:
                                from src.crm.account_data import set_active_account
                                inst_data_now = manager.get_all_instances().get(instance_id, {})
                                _nick_now = inst_data_now.get("nickname") or target_wxid
                                set_active_account(target_wxid, _nick_now)
                                logger.info(f"[WCDB API] ✅ 密钥提取成功后，主动将 {instance_id}({target_wxid}) 设为活跃账号")
                        except Exception as e_act:
                            logger.warning(f"[WCDB API] 设置活跃账号异常: {e_act}")

                    # 🌟 成功截获密钥后，立即执行同步扫描，建立新窗口句柄与微信号的正确绑定
                    try:
                        from src.api.instance_helpers import do_scan_sync
                        do_scan_sync()
                    except Exception as e_scan:
                        logger.error(f"[WCDB API] 密钥提取后执行 do_scan_sync 异常: {e_scan}")
                        
                    # 💡 成功截获密钥后，立刻驱动该实例重新从解密数据库中提取真实的头像和昵称，结束卡片空白状态
                    try:
                        from app.state import account_manager
                        from src.monitor.account_profile_helper import restore_account_profile
                        from src.utils.instance_manager import InstanceManagerV2
                        
                        manager = InstanceManagerV2.get_instance()
                        for inst in list(account_manager._instances.values()):
                            # 关联 InstanceManager 中的 window_handle
                            matched = False
                            if instance_id:
                                inst_data = manager.get_all_instances().get(instance_id)
                                if inst_data and inst_data.get("window_handle") == inst.hwnd:
                                    matched = True
                            
                            if matched or (target_wxid and inst.wxid == target_wxid) or (target_wxid and inst.driver._wxid == target_wxid):
                                print(f"[WCDB API] 🎯 正在通过新提取的密钥重新同步实例 (hwnd={inst.hwnd}) 的头像与昵称...")
                                # 强制将当前驱动器密钥更新为新截获的密钥
                                inst.driver.hex_key = key
                                if inst.driver.hwnd:
                                    # 调用恢复函数，触发解密 and 头像写入
                                    restore_account_profile(inst, inst.driver.hwnd)
                    except Exception as refresh_err:
                        logger.error(f"[WCDB API] 重新同步头像昵称时发生异常: {refresh_err}")
                    
                    api_dir = os.path.dirname(os.path.abspath(__file__))
                    product_dir = os.path.dirname(os.path.dirname(os.path.dirname(api_dir)))
                    env_path = os.path.join(product_dir, '.env')
                    lines = []
                    if os.path.exists(env_path):
                        with open(env_path, 'r', encoding='utf-8') as f:
                            lines = f.readlines()
                    new_lines = []
                    updated_key = False
                    updated_enable = False
                    for line in lines:
                        if line.startswith("WECHAT_4X_KEY_HEX="):
                            new_lines.append(f"WECHAT_4X_KEY_HEX={key}\n")
                            updated_key = True
                        elif line.startswith("WECHAT_ENHANCED_4X="):
                            new_lines.append("WECHAT_ENHANCED_4X=1\n")
                            updated_enable = True
                        else:
                            new_lines.append(line)
                    if not updated_key:
                        new_lines.append(f"\nWECHAT_4X_KEY_HEX={key}\n")
                    if not updated_enable:
                        new_lines.append("WECHAT_ENHANCED_4X=1\n")
                        
                    with open(env_path, 'w', encoding='utf-8') as f:
                        f.writelines(new_lines)
                    return True, key
                return False, "提取超时，请确认在弹出的微信中完成了登录扫码"
            except Exception as e:
                return False, f"提取发生异常: {str(e)}"
                
        success, res = await loop.run_in_executor(None, run_extraction)
        if success:
            # 使用显式 JSONResponse 而非 dict 返回，
            # 避免 Starlette BaseHTTPMiddleware + run_in_executor 组合时的
            # "Response content shorter than Content-Length" 已知缺陷：
            # dict 返回由 FastAPI 延迟序列化，长时间阻塞后中间件层的缓冲区可能已关闭。
            # JSONResponse 在发送前就完整序列化并确定 Content-Length，安全。
            return JSONResponse(content=ok({"key": res}))
        else:
            return JSONResponse(content=err(500, res))
    finally:
        _extract_active = False
        _extract_lock.release()

# Note: _detect_all_db_storage_dirs, _detect_db_path, and _match_db_storage_by_key
# have been extracted to src/utils/wcdb_helpers.py to optimize code organization and file length.

@router.post("/sync-contacts")
async def sync_wcdb_contacts(account_id: str = "default"):
    """利用捕获的密钥直接读取 contact.db 并同步通讯录缓存"""
    from src.utils.instance_manager import InstanceManagerV2
    from src.crm.account_data import normalize_to_real_wxid
    
    # 🌟 核心拦截：当系统没有任何在线微信实例时，拒绝手动或静默解密通讯录
    manager = InstanceManagerV2.get_instance()
    active_inst = manager.get_active_instance()
    if not active_inst:
        logger.warning("[WCDB API] 当前无在线接管微信实例，拒绝同步通讯录数据库")
        return err(400, "当前无在线托管的微信实例，请先在账号管理中连接微信")
        
    active_id = active_inst.get("wxid") or manager.get_active_instance_id() or "default"
    if account_id == "default" and active_id and active_id != "default":
        account_id = active_id

    # 🌟 强力归一化：将虚拟 ID 映射为真实 wxid，保证校验的一致性，防止安全检查误判
    account_id = normalize_to_real_wxid(account_id)

    from src.utils.wechat_key_store import get_persisted_wechat_key
    hex_key = get_persisted_wechat_key(account_id)
    if not hex_key:
        hex_key = os.environ.get("WECHAT_4X_KEY_HEX") or os.environ.get("WCDB_HEX_KEY")
    if not hex_key:
        try:
            api_dir = os.path.dirname(os.path.abspath(__file__))
            product_dir = os.path.dirname(os.path.dirname(os.path.dirname(api_dir)))
            env_path = os.path.join(product_dir, '.env')
            if os.path.exists(env_path):
                with open(env_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.startswith("WECHAT_4X_KEY_HEX="):
                            val = line.split("=", 1)[1].strip()
                            if len(val) == 64:
                                hex_key = val
                                break
        except Exception:
            pass

    if not hex_key:
        return err(400, "本地暂无有效 WCDB 密钥，请先一键激活高能通道")

    try:
        import asyncio
        loop = asyncio.get_event_loop()
        from src.wechat_4x.db_contact_syncer import sync_contacts_from_db

        # 主动手动同步时，清空该账号已删除的联系人黑名单，允许重新从微信数据库全量拉回
        try:
            from src.utils.contacts_cache.delete_store import clear_deleted_contacts
            clear_deleted_contacts(account_id)
        except Exception as e:
            logger.warning(f"手动同步清空被删黑名单失败: {e}")

        from src.utils.wcdb_helpers import match_db_storage_by_key, detect_db_path

        # 关键修复：先用 HMAC 校验找到密钥真正对应的 db_storage 目录
        # 避免多账号环境下密钥与 wxid 目录不匹配导致解密失败
        db_storage = await loop.run_in_executor(None, match_db_storage_by_key, hex_key)
        if not db_storage:
            # 降级：找不到匹配目录时尝试通过环境变量兜底
            fallback_path = detect_db_path()
            if not fallback_path:
                return err(400, "未找到与当前密钥匹配的微信数据库目录，请重新提取密钥")
            db_storage = os.path.dirname(os.path.dirname(fallback_path))  # session.db → db_storage

        # 🌟 强制安全性校验：确保解密匹配成功的数据库目录所对应的 wxid 与我们当前操作的 account_id 一致！
        # 防止密钥错乱/多账号时，将 A 账号的联系人同步到 B 账号的缓存/界面中
        if db_storage and account_id and account_id != "default":
            from src.utils.wechat_key_store import clean_wxid
            import hashlib
            folder_wxid = clean_wxid(os.path.basename(os.path.dirname(db_storage)))
            cleaned_account_id = clean_wxid(account_id)
            cleaned_account_id_md5 = hashlib.md5(cleaned_account_id.encode('utf-8')).hexdigest()
            if folder_wxid not in (cleaned_account_id, cleaned_account_id_md5):
                logger.error(f"[WCDB API] ❌ 严重安全拦截：密钥匹配到的数据库目录属于账号 {folder_wxid}，但当前请求同步的账号为 {cleaned_account_id}。禁止同步以防串号！")
                return err(400, "同步失败：当前解密出的密钥所属微信账号与当前操作的微信号不一致，请点击「清除失效密钥」后重新提取")

        # 使用 run_in_executor 避免阻塞 FastAPI 主线程
        await loop.run_in_executor(None, sync_contacts_from_db, db_storage, hex_key, account_id)
        return ok({"message": "通讯录已成功通过高能通道快速同步"})
    except Exception as e:
        return err(500, f"高速同步通讯录失败: {str(e)}")


@router.post("/purge-key")
async def purge_wcdb_key(request: Request):
    """手动清除指定微信账号的历史缓存密钥，强制重置"""
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    
    wxid = body.get("wxid")
    if not wxid:
        return err(400, "参数缺少 wxid")
        
    try:
        from src.utils.wechat_key_store import clear_persisted_wechat_key
        clear_persisted_wechat_key(wxid)
        
        # 同步更新共享内存状态，让前端能立刻发现 has_key 为 False
        try:
            from src.utils.instance_manager import InstanceManagerV2
            mgr = InstanceManagerV2.get_instance()
            for inst_id, inst_data in mgr.get_all_instances().items():
                if inst_data.get("wxid") == wxid:
                    mgr.update_instance(inst_id, {"has_key": False})
                    break
        except Exception as e_mgr:
            logger.warning(f"[WCDB API] 清除密钥更新共享内存失败: {e_mgr}")
            
        return ok({"success": True, "message": f"账号 {wxid} 的数据库密钥清除成功"})
    except Exception as e:
        logger.error(f"[WCDB API] 清除密钥异常: {e}")
        return err(500, f"清除密钥失败: {str(e)}")

