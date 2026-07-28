import os
import asyncio
import logging
from typing import Optional
from fastapi import APIRouter

from src.utils.response import ok, err
from .models import StartGroupTaskRequest

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/group-start")
async def start_group_task(req: StartGroupTaskRequest):
    # 延迟导入以防止循环引用
    from .task_service import _task_state, _driver

    if _task_state["running"]:
        return err(40000, "加好友/加群好友任务已在运行中，请先停止当前任务")

    from src.utils.license_validator import LicenseValidator
    features = LicenseValidator.check_features()
    if not features.get("smart_acquisition", False):
        return err(40301, "当前版本不支持智能加好友，请升级套餐")

    from src.friend.group_friend_history import get_today_added_count, sync_from_cloud
    # 启动前前置同步云服务器最新历史，防止用户换电脑导致上限拦截或断点记忆失效
    sync_from_cloud()
    today_count = get_today_added_count()
    if today_count >= 15:
        return err(40003, f"已达到今日群加好友防封安全上限（今日已发申请 {today_count}/15 人）。请明天再试！")

    _task_state["config"] = req.dict()
    _task_state["running"] = True
    _task_state["paused"] = False
    _task_state["progress"] = {
        "total": req.max_add_count,
        "processed": 0,
        "succeeded": 0,
        "failed": 0,
    }

    from .task_engine import save_task_state_to_db
    save_task_state_to_db()
    asyncio.create_task(_run_group_add_friend_loop(req))

    return ok({
        "message": "批量加群好友任务已启动",
        "config": _task_state["config"],
        "today_count": today_count,
        "scope": f"微信群={req.group_name}",
    })


async def _run_group_add_friend_loop(req: StartGroupTaskRequest):
    from .task_service import _driver, _task_state
    try:
        from src.uia.group_add_friend import GroupAddFriendEngine
        engine = GroupAddFriendEngine(_driver)

        interval = (float(req.interval_range[0]), float(req.interval_range[1])) if req.interval_range and len(req.interval_range) >= 2 else (10.0, 20.0)

        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: engine.add_group_members(
                group_name=req.group_name,
                max_add_count=req.max_add_count,
                remark_prefix=req.remark_prefix,
                tags=req.tags,
                verify_message=req.verify_message,
                interval_range=interval,
                task_state=_task_state,
            )
        )

        logger.info(f"[群加好友] 批量加群好友任务结束: {result.get('message')}")

        # 检测是否由用户按下 ESC 键引起的中断挂起
        from src.utils.stop_signal import stop_signal
        is_esc_interrupt = (
            not result.get("success", False)
            and ("ESC" in result.get("message", "") or "中断" in result.get("message", ""))
        ) or stop_signal.is_stopped

        if is_esc_interrupt:
            logger.info("[群加好友] 检测到用户按下 ESC 键中断任务，正在将群加好友任务安全挂起...")
            _task_state["paused"] = True
            # 保持 _task_state["running"] = True，以供前端展现“安全挂起”状态
            from .task_engine import save_task_state_to_db
            save_task_state_to_db()
            try:
                stop_signal.reset()
            except Exception:
                pass
        else:
            _task_state["running"] = False
            from .task_engine import save_task_state_to_db
            save_task_state_to_db()

    except Exception as e:
        logger.error(f"[群加好友] 任务运行崩溃: {e}")
        try:
            from src.utils.alert_notifier import alert_notifier
            import platform
            _task_state["paused"] = True
            asyncio.create_task(alert_notifier.trigger_risk_alert(
                machine_code=platform.node(),
                account_id=_driver.wxid or _driver.nickname or "未知微信",
                reason=f"加群好友任务遇到未捕获异常崩溃: {str(e)}",
                is_fatal=False,
                hwnd=_driver.hwnd if _driver else 0
            ))
        except Exception as ae:
            logger.error(f"发送加群异常告警失败: {ae}")
        _task_state["running"] = False
        from .task_engine import save_task_state_to_db
        save_task_state_to_db()


EXPORTED_FILES = {}

@router.post("/group-members/sync-export")
async def sync_and_export_group_members(req: dict):
    group_name = req.get("group_name")
    if not group_name:
        return err(40000, "参数错误：群名不能为空")

    from src.utils.contacts_cache import contacts_cache
    from src.crm.account_data import get_active_account
    account_id = get_active_account()

    # 1. 尝试从 WCDB 微信解密数据库中直接同步读取 (优先)
    db_members = None
    hex_key = os.environ.get("WECHAT_4X_KEY_HEX") or os.environ.get("WCDB_HEX_KEY")
    if hex_key and len(hex_key) == 64:
        try:
            import asyncio
            from src.wechat_4x.contact_helper import extract_group_members_from_wcdb
            db_members = await asyncio.get_event_loop().run_in_executor(
                None, extract_group_members_from_wcdb, group_name, hex_key
            )
        except Exception as e_pre:
            logger.warning(f"[导出群成员] 微信数据库逻辑异常，降级为 RPA: {e_pre}")

    if db_members is not None:
        updated_members = db_members
    else:
        # 2. 没有数据库连接时的 RPA UIA 兜底逻辑
        # 延迟导入以防止循环引用
        from .task_service import _driver
        if not _driver:
            return err(40001, "微信未连接，请先登录微信")

        import asyncio
        # 运行 UIA 自动化同步
        from src.uia.group_sync_helper import sync_group_members_via_uia
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: sync_group_members_via_uia(_driver, group_name)
        )

        if not result.get("success"):
            return err(40002, result.get("message", "微信群成员同步失败"))

        members_list = result["members"]

        existing_members = contacts_cache.get_group_members(account_id, group_name) or []
        existing_dict = {m.get("display_name") or m.get("nickname"): m for m in existing_members if m}

        updated_members = []
        for name in members_list:
            if name in existing_dict:
                updated_members.append(existing_dict[name])
            else:
                updated_members.append({
                    "group_name": group_name,
                    "nickname": name,
                    "display_name": name,
                    "wxid": "",
                    "username": "",
                })

    # 合并其他群的成员并写回 contacts_cache
    all_members = contacts_cache.get_group_members(account_id) or []
    other_members = [m for m in all_members if m and m.get("group_name") != group_name]
    other_members.extend(updated_members)
    contacts_cache.set_group_members(account_id, other_members, sync_cloud=True)

    # 3. 生成 Excel (带头像)
    try:
        from src.utils.group_members_exporter import do_export_group_members
        file_path = do_export_group_members(group_name, updated_members)
        # 保存到全局导出字典
        EXPORTED_FILES[group_name] = file_path
        return ok({"message": "同步并生成 Excel 成功", "group_name": group_name})
    except Exception as e:
        logger.error(f"生成群成员 Excel 失败: {e}", exc_info=True)
        return err(50000, f"导出生成失败: {str(e)}")


@router.get("/group-members/export")
async def export_group_members(group_name: str):
    """获取指定群聊的群成员数据并导出为 Excel"""
    if not group_name:
        return err(40000, "参数错误：群名不能为空")

    file_path = EXPORTED_FILES.get(group_name)
    if not file_path:
        from src.utils.contacts_cache import contacts_cache
        from src.crm.account_data import get_active_account
        account_id = get_active_account()

        members = contacts_cache.get_group_members(account_id, group_name)
        if not members:
            return err(40400, "该群聊尚未同步群成员。请先执行通讯录同步。")

        try:
            from src.utils.group_members_exporter import do_export_group_members
            file_path = do_export_group_members(group_name, members)
            EXPORTED_FILES[group_name] = file_path
        except Exception as e:
            logger.error(f"导出群成员生成失败: {e}", exc_info=True)
            return err(50000, f"导出失败: {str(e)}")

    try:
        from fastapi.responses import FileResponse
        from urllib.parse import quote

        filename = f"群成员_{group_name}.xlsx"
        headers = {
            "Content-Disposition": f"attachment; filename*=utf-8''{quote(filename)}"
        }
        return FileResponse(
            file_path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers=headers
        )
    except Exception as e:
        logger.error(f"导出群成员下载失败: {e}", exc_info=True)
        return err(50000, f"下载失败: {str(e)}")



@router.post("/group-members/invite")
async def invite_members(req: dict):
    """支持多选微信好友批量拉入指定的群聊"""
    group_name = req.get("group_name")
    friend_names = req.get("friend_names", [])
    if not group_name or not friend_names:
        return err(40000, "参数错误：群名和好友名列表不能为空")

    from .task_service import _driver
    if not _driver or not _driver.is_connected():
        return err(40000, "微信未连接")

    from src.uia.group_invite_helper import invite_friends_to_group

    res = {}
    def run_invites():
        nonlocal res
        res = invite_friends_to_group(_driver, group_name, friend_names)

    await asyncio.get_event_loop().run_in_executor(None, run_invites)
    
    if res.get("success"):
        return ok({
            "success_count": res.get("success_count", 0),
            "failed_names": res.get("failed_names", []),
            "message": res.get("message") or f"成功邀请 {res.get('success_count', 0)} 位好友，失败 {len(res.get('failed_names', []))} 位"
        })
    else:
        return err(50000, res.get("message") or "群邀请操作失败")


@router.get("/group-history")
async def get_group_history(group_name: str):
    """获取指定群聊最新的添加历史记录"""
    if not group_name:
        return err(40000, "参数错误：群名不能为空")
    from src.friend.group_friend_history import get_group_history_list
    history = get_group_history_list(group_name)
    return ok(history)
