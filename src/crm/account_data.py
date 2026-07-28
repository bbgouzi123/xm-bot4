"""
多账号数据隔离管理器 — 按微信号分离数据目录

统一数据根目录: ~/.xm-ai-bot/accounts/{wxid}/
    profiles/          ← 客户画像
    config.json        ← 行业配置（CRM）
    contacts.json      ← 通讯录缓存
    chat_history/      ← 聊天记录
    avatars/           ← 头像

全局共享（不区分账号）:
    ~/.xm-ai-bot/config.json        ← AI平台配置（Token等）
    ~/.xm-ai-bot/materials/         ← 朋友圈素材
    ~/.xm-ai-bot/uploads/           ← 上传文件

核心优势：
    - 打包成 EXE 后数据不丢失（存到用户目录而非程序临时目录）
    - 多微信号数据完全隔离
    - 新账号可一键复制其他账号的行业配置
"""
import os
import json
import shutil
import logging
import asyncio
import uuid
from pathlib import Path
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

# ===== 统一数据根目录 =====
# 所有数据存到用户主目录下的 .xm-ai-bot/，打包后也不受影响
APP_DATA_DIR = str(Path.home() / ".xm-ai-bot")
ACCOUNTS_DIR = os.path.join(APP_DATA_DIR, "accounts")

# 当前活跃的微信号
_active_wxid: str = "default"
# 当前活跃账号的昵称（用于展示）
_active_nickname: str = ""


from src.crm.ready_barrier import ready_barrier


def _notify_bot_data_scope_changed():
    """接管微信变更后：配置/通讯录/获客/排期等按 bot_wxid 从同步后端重拉，避免 SSO 账号维度串数据。"""
    try:
        from src.crm.bot_lifecycle_manager import notify_bot_data_scope_changed
        notify_bot_data_scope_changed()
    except Exception as e:
        logger.warning(f"[多账号] 触发业务生命周期变更流程失败: {e}")


def _safe_dirname(name: str) -> str:
    """将微信号/昵称转为安全的目录名"""
    if not name:
        return "default"
    return "".join(c for c in name if c.isalnum() or c in "-_.")


def set_active_account(wxid: str, nickname: str = ""):
    """设置当前活跃的微信账号

    在 main.py 启动 / 切换实例时调用。
    所有 CRM 模块会自动切换 to 对应的数据目录。

    Args:
        wxid: 微信号（如 wxid_abc123），没有就用昵称
        nickname: 昵称（wxid 为空时用作目录名）
    """
    global _active_wxid, _active_nickname
    old = _active_wxid
    
    # 🌟 强力归一化：切换活跃账号时，使用真实 wxid 覆盖虚拟/临时 ID，确保数据目录归一收束
    real_wxid = normalize_to_real_wxid(wxid or nickname or "default")
    new_dir = _safe_dirname(real_wxid)

    # 1. 开启就绪信号屏障
    trace_id = f"switch-{new_dir}-{uuid.uuid4().hex[:6]}"
    logger.info(f"[多账号] 📥 接收到账号切换请求: {old} → {new_dir} (Trace ID: {trace_id})")
    ready_barrier.clear(trace_id)

    try:
        if old != new_dir:
            try:
                from src.utils.db_manager import wechat_db_flush_before_switch
                wechat_db_flush_before_switch(old)
                logger.info(f"[多账号] [Trace ID: {trace_id}] 已成功刷盘旧账号 {old} 数据快照")
            except Exception as e:
                logger.warning(f"[多账号] [Trace ID: {trace_id}] 切换前落盘获客/跟单状态失败: {e}")

        _active_wxid = new_dir
        _active_nickname = nickname or wxid or "default"
        
        # 尝试利用本地元数据恢复真实昵称，防止前端界面回退到 wxid 格式
        is_nick_raw_wxid = _active_nickname.startswith("wxid_") or _active_nickname == new_dir
        if not nickname or is_nick_raw_wxid:
            try:
                meta = _load_account_meta(new_dir)
                if meta and meta.get("nickname") and not (meta.get("nickname").startswith("wxid_") or meta.get("nickname") == new_dir):
                    _active_nickname = meta["nickname"]
            except Exception:
                pass

        # 确保目录结构完整
        account_dir = os.path.join(ACCOUNTS_DIR, _active_wxid)
        for subdir in ["profiles", "chat_history"]:
            os.makedirs(os.path.join(account_dir, subdir), exist_ok=True)

        # 写入账号元信息（方便UI展示）
        _save_account_meta(_active_wxid, _active_nickname, real_wxid)

        if old != _active_wxid:
            logger.info(f"[多账号] [Trace ID: {trace_id}] 开始切换数据目录: {old} → {_active_wxid}")
            
            # 2. 重新加载 WeChatDBManager 内存快照
            try:
                from src.utils.db_manager import wechat_db_reload_after_switch
                wechat_db_reload_after_switch()
                logger.info(f"[多账号] [Trace ID: {trace_id}] WeChatDBManager 状态快照已重载")
            except Exception as e:
                logger.warning(f"[多账号] [Trace ID: {trace_id}] 切换后加载获客/跟单状态失败: {e}")
                
            # 3. 重置与重新加载 ContactsCache 及 Config 缓存
            try:
                _notify_bot_data_scope_changed()
                logger.info(f"[多账号] [Trace ID: {trace_id}] 业务生命周期变更流程与通讯录缓存已重载")
            except Exception as e:
                logger.warning(f"[多账号] [Trace ID: {trace_id}] 切换后刷新同步后端隔离视图失败: {e}")
                
        # 4. 强制实例化新账号的 ProfileManager 并等待其完全加载完成，防止界面闪烁或串数据
        try:
            from src.crm.profile_manager import ProfileManager
            # 先驱逐 "default" 占位单例，确保后续以真实 wxid 正常初始化（避免内存泄漏）
            ProfileManager._evict_placeholder()
            pm = ProfileManager(account_id=_active_wxid)
            logger.info(f"[多账号] [Trace ID: {trace_id}] ⏳ 正在等待新账号画像缓存加载...")
            if hasattr(pm, "loaded_event"):
                pm.loaded_event.wait(timeout=5.0)
            logger.info(f"[多账号] [Trace ID: {trace_id}] 🎉 画像缓存已就绪")
        except Exception as pm_err:
            logger.warning(f"[多账号] [Trace ID: {trace_id}] 触发新账号画像同步加载失败: pm_err: {pm_err}")

        # 广播微信账号切换事件，通知前端实时刷新仪表盘
        try:
            from src.utils.websocket_manager import ws_manager
            import asyncio
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            payload = {"type": "bot_account_changed", "data": {"active_wxid": _active_wxid}}
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(ws_manager.broadcast(payload), loop)
            else:
                loop.run_until_complete(ws_manager.broadcast(payload))
            logger.info(f"[多账号] [Trace ID: {trace_id}] 广播 bot_account_changed 事件: active_wxid={_active_wxid}")
        except Exception as e:
            logger.debug(f"[多账号] [Trace ID: {trace_id}] 广播账号切换事件失败: {e}")

    finally:
        # 5. 关闭就绪信号屏障，释放阻塞 of API 请求
        ready_barrier.set()
        logger.info(f"[多账号] 📤 账号切换处理流程全部结束，栅栏就绪状态已置为 Ready (Trace ID: {trace_id})")


def get_active_account() -> str:
    """获取当前活跃的微信号标识（带共享内存活跃实例动态对齐）"""
    global _active_wxid
    if _active_wxid == "default":
        try:
            from src.utils.instance_manager import InstanceManagerV2
            active_id = InstanceManagerV2.get_instance().get_active_instance_id()
            if active_id and active_id != "default":
                real_id = normalize_to_real_wxid(active_id)
                if real_id and real_id != "default":
                    _active_wxid = real_id
                    logger.info(f"[多账号] 自动从共享内存对齐活跃账号: {_active_wxid}")
        except Exception as e:
            logger.debug(f"[多账号] 自动对齐共享内存活跃账号异常: {e}")
    return _active_wxid



def get_active_nickname() -> str:
    """获取当前活跃账号的昵称"""
    return _active_nickname


def normalize_to_real_wxid(wxid: str) -> str:
    """将虚拟/临时/默认微信 ID (如 instance_1, default, wx_xxx) 转换为真实的微信 wxid"""
    if not wxid:
        return "default"
    
    # 排除部分真实/保留目录标识以节省性能并防止初始化阶段的循环依赖与递归异常
    if wxid.startswith("wxid_") or wxid == "common":
        return wxid

    # 如果既不是虚拟/临时实例分身 ID，也不是默认/主账号，直接返回，不查询实例管理器
    if not (wxid.startswith("instance_") or wxid in ("default", "main")):
        return wxid

    try:
        from src.utils.instance_manager import InstanceManagerV2
        manager = InstanceManagerV2.get_instance()
        all_insts = manager.get_all_instances()
        
        # 1. 直接匹配键名
        inst_data = all_insts.get(wxid)
        if inst_data and inst_data.get("wxid"):
            return inst_data["wxid"]
            
        # 2. 如果是 "default" 或 "main"，看活跃实例的真实 wxid
        if wxid in ("default", "main"):
            active_id = manager.get_active_instance_id()
            if active_id and active_id != wxid:
                res = normalize_to_real_wxid(active_id)
                if res and res != "default":
                    return res
            # 💡 强力自愈兜底：如果活跃实例为空或未对齐，直接遍历所有在线实例，寻找第一个合法的真实 wxid
            for inst_k, inst_v in all_insts.items():
                if inst_v and inst_v.get("wxid") and inst_v.get("wxid").startswith("wxid_"):
                    return inst_v["wxid"]
                
        # 3. 遍历所有实例，寻找 window_handle 匹配或者 wxid 属性匹配
        for k, v in all_insts.items():
            if k == wxid or v.get("wxid") == wxid:
                if v.get("wxid"):
                    return v["wxid"]
    except Exception:
        pass
        
    return wxid


def migrate_virtual_account_dir(virtual_id: str, real_wxid: str):
    """当虚拟/临时账号绑定到真实微信 ID 时，将其本地隔离目录的数据迁移/合并到真实微信 ID 目录下"""
    if not virtual_id or not real_wxid or virtual_id == real_wxid:
        return
    try:
        v_dir = os.path.join(ACCOUNTS_DIR, _safe_dirname(virtual_id))
        r_dir = os.path.join(ACCOUNTS_DIR, _safe_dirname(real_wxid))
        
        if not os.path.exists(v_dir):
            return
            
        logger.info(f"[数据迁移] 检测到虚拟账号 {virtual_id} 绑定到真实微信 {real_wxid}，正在合并数据目录...")
        
        # 递归合并目录
        os.makedirs(r_dir, exist_ok=True)
        for item in os.listdir(v_dir):
            src_path = os.path.join(v_dir, item)
            dst_path = os.path.join(r_dir, item)
            
            if os.path.isdir(src_path):
                os.makedirs(dst_path, exist_ok=True)
                for sub_item in os.listdir(src_path):
                    sub_src = os.path.join(src_path, sub_item)
                    sub_dst = os.path.join(dst_path, sub_item)
                    if not os.path.exists(sub_dst):
                        shutil.move(sub_src, sub_dst)
            else:
                if not os.path.exists(dst_path):
                    shutil.move(src_path, dst_path)
                    
        # 移除已经空的虚拟账号目录
        try:
            shutil.rmtree(v_dir)
            logger.info(f"[数据迁移] 成功清理虚拟账号目录: {v_dir}")
        except Exception as e_rm:
            logger.debug(f"[数据迁移] 清理空虚拟账号目录失败 (可能还有文件在占用): {e_rm}")
            
    except Exception as e:
        logger.error(f"[数据迁移] 迁移虚拟账号 {virtual_id} 到 {real_wxid} 失败: {e}")


# ==================== 路径获取 ====================

def get_account_data_dir(wxid: str = None) -> str:
    """获取指定账号的数据根目录"""
    target_id = wxid if wxid else _active_wxid
    real_id = normalize_to_real_wxid(target_id)
    target = _safe_dirname(real_id)
    path = os.path.join(ACCOUNTS_DIR, target)
    os.makedirs(path, exist_ok=True)
    return path


def get_profiles_dir(wxid: str = None) -> str:
    """获取客户画像目录"""
    path = os.path.join(get_account_data_dir(wxid), "profiles")
    os.makedirs(path, exist_ok=True)
    return path


def get_config_path(wxid: str = None) -> str:
    """获取行业配置文件路径（CRM 行业配置，非 AI 全局配置）"""
    return os.path.join(get_account_data_dir(wxid), "config.json")


def get_contacts_path(wxid: str = None) -> str:
    """获取通讯录缓存路径"""
    return os.path.join(get_account_data_dir(wxid), "contacts.json")


def get_chat_history_dir(wxid: str = None) -> str:
    """获取聊天记录目录"""
    path = os.path.join(get_account_data_dir(wxid), "chat_history")
    os.makedirs(path, exist_ok=True)
    return path


# get_avatars_dir removed because it is taking off pants to fart.


def make_avatar_url(wxid: str) -> str:
    """生成带防缓存版本参数的头像 URL

    使用头像文件的修改时间作为版本参数，确保浏览器在文件更新后重新请求。
    例: /api/avatar/account_001?t=1710556000

    Args:
        wxid: 微信号

    Returns:
        带版本参数的头像 URL 字符串
    """
    url = f"/api/avatar/{wxid}"
    try:
        avatar_path = os.path.join(ACCOUNTS_DIR, f"{wxid}.png")
        if os.path.exists(avatar_path):
            mtime = int(os.path.getmtime(avatar_path))
            url = f"{url}?t={mtime}"
    except Exception:
        pass
    return url


from src.crm.account_settings_store import get_account_settings, save_account_settings


# ==================== 账号元信息 ====================

def _get_meta_path(wxid_dir: str) -> str:
    return os.path.join(ACCOUNTS_DIR, wxid_dir, "account_meta.json")


def _save_account_meta(wxid_dir: str, nickname: str, wxid: str):
    """保存账号元信息"""
    meta_path = _get_meta_path(wxid_dir)
    meta = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            pass
    meta["wxid"] = wxid or wxid_dir
    meta["nickname"] = nickname
    meta["dir_name"] = wxid_dir
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _load_account_meta(wxid_dir: str) -> dict:
    """加载账号元信息"""
    meta_path = _get_meta_path(wxid_dir)
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"wxid": wxid_dir, "nickname": wxid_dir, "dir_name": wxid_dir}


# ==================== 账号管理与数据迁移 (已拆分到 account_ops.py) ====================

from src.crm.account_ops import list_accounts, copy_config_from, migrate_legacy_data
