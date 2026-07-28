"""
SSO 跨产品单点登录 API
"""
import json
from fastapi import APIRouter, Request
from src.utils.response import ok, err, ok_msg
from src.sso_bridge import read_sso_session, write_sso_session, clear_sso_session, _sso_file_path

router = APIRouter()

@router.get("/api/v1/sso/session")
async def get_sso_session():
    """读取本地 SSO 共享文件（供前端 auth.ts 检测跨产品登录状态）"""
    session = read_sso_session()
    return ok(session)


@router.post("/api/v1/sso/session")
async def save_sso_session(request: Request):
    """写入本地 SSO 共享文件（前端登录成功后调用）"""
    try:
        raw = await request.body()
        body = json.loads(raw.decode("utf-8"))
    except Exception:
        return err(40000, "请求体解析失败")

    user_info = body.get("user", {})
    user_id = user_info.get("id") or user_info.get("userId")
    if user_id:
        _failed_sync_user_ids.discard(user_id)

    write_sso_session(
        access_token=body.get("access_token", ""),
        refresh_token=body.get("refresh_token", ""),
        user_info=user_info,
        source_app=body.get("source_app", "xm-bot4"),
        device_fingerprint=body.get("device_fingerprint", ""),
    )

    # [用户配置隔离] 登录成功后，立即将真实 token 注入同步服务客户端
    try:
        from src.utils.cloud_sync import get_cloud_client
        get_cloud_client().sync_token_from_sso()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"[SSO] Token 热切换异常: {e}")

    # 🌟 [强力同步拉取云配置] 登录成功且 Token 准备好后，立即强制从同步后端拉取最新配置
    try:
        from src.utils.config_cache import config_cache
        # load_from_cloud 内部已实现比对逻辑，加载成功后会自动同步保存到本地文件并强制热加载 AI 服务
        config_cache.load_from_cloud(clear_before_load=True)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"[SSO] 登录后强制同步云端配置异常: {e}")

    # [AI 服务热切换] 确保本地已保存最新配置后，再次执行强制 AI 重建，避免防抖延迟
    try:
        from src.api.config_api import _reload_ai_service
        _reload_ai_service(force=True)
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug(f"[SSO] 登录后 AI 服务重载异常（非严重）: {e}")

    # [WCDB 延迟启动] 登录成功后，如果微信已经连接，立即补触发 WCDB 引擎
    # 原因：在用户未登录时，discover_and_connect 会跳过 WCDB 引擎启动（设计铁律：未登录不连接微信数据库）。
    # 用户完成 xm-bot4 平台登录后，此处检查已有的微信连接实例，逐一补触发 WCDB 引擎。
    try:
        import asyncio as _asyncio
        from app import state as app_state
        from app.state import account_manager as _am

        def _trigger_wcdb_for_all_instances():
            try:
                if not _am:
                    return
                for inst in list(_am._instances.values()):
                    if not inst.wxid:
                        continue
                    mon = inst.monitor
                    if mon is None:
                        continue
                    # 仅对尚未启动 WCDB 引擎的实例补触发
                    if getattr(mon, "_wcdb_session_monitor", None) is not None:
                        continue
                    if getattr(mon, "_wcdb_key_failed_permanently", False):
                        continue
                    if getattr(mon, "_wcdb_starting", False):
                        continue
                    print(f"[SSO] 登录后补触发 WCDB 引擎 (wxid={inst.wxid})")
                    try:
                        if hasattr(app_state, "main_loop") and app_state.main_loop and app_state.main_loop.is_running():
                            _asyncio.run_coroutine_threadsafe(mon._start_wcdb_engine(), app_state.main_loop)
                        else:
                            loop = _asyncio.get_event_loop()
                            if loop.is_running():
                                loop.create_task(mon._start_wcdb_engine())
                    except Exception as _we:
                        print(f"[SSO] 补触发 WCDB 引擎异常: {_we}")
            except Exception as _e:
                import logging
                logging.getLogger(__name__).debug(f"[SSO] WCDB 延迟启动异常（非严重）: {_e}")

        import threading as _threading
        _threading.Thread(target=_trigger_wcdb_for_all_instances, daemon=True, name="sso-wcdb-trigger").start()
    except Exception:
        pass

    return ok_msg("操作成功")




@router.delete("/api/v1/sso/session")
async def delete_sso_session():
    """清除本地 SSO 共享文件（前端登出时调用）"""
    clear_sso_session()

    # 重置平台登录内存标志为 False，阻止任何微信数据库后台行为
    try:
        from src.utils.auth_session import set_platform_logged_in
        set_platform_logged_in(False)
    except Exception:
        pass

    # [用户配置隔离] 登出时清空内存配置缓存，防止残留给下一个用户
    try:
        from src.utils.config_cache import config_cache
        config_cache._cache.clear()
        import logging
        logging.getLogger(__name__).info("[SSO] 用户登出，内存配置缓存已清空")
    except Exception:
        pass

    return ok_msg("操作成功")

_failed_sync_user_ids = set()

@router.post("/api/v1/sso/detect")
async def detect_sso_session():
    """全局沙盒穿穿透探测端点（响应任意浏览器的本地探测指令）"""
    path = _sso_file_path()
    if not path.exists():
        return ok({"accounts": []})
        
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        accounts = []
        
        if data.get('version') == 2:
            raw_accounts = data.get('accounts', [])
            
            # 探测阶段异步补充缺失的用户画像（昵称/头像等）
            has_missing_info = False
            for acc in raw_accounts:
                u_id = acc.get("user_id")
                if u_id in _failed_sync_user_ids:
                    continue
                if (not acc.get("nickname") or not acc.get("avatar_url")) and acc.get("access_token"):
                    has_missing_info = True
                    break
            
            if has_missing_info:
                import threading
                def bg_sync():
                    import urllib.request as urllib_request
                    import os
                    license_api_url = os.environ.get("XM_LICENSE_API_URL") or "http://127.0.0.1:42001"
                    for acc in raw_accounts:
                        u_id = acc.get("user_id")
                        if not u_id or u_id in _failed_sync_user_ids:
                            continue
                        if (not acc.get("nickname") or not acc.get("avatar_url")) and acc.get("access_token"):
                            try:
                                url = f"{license_api_url.rstrip('/')}/api/v1/user/profile/info"
                                req = urllib_request.Request(
                                    url, 
                                    headers={
                                        "Authorization": f"Bearer {acc['access_token']}",
                                        "Content-Type": "application/json"
                                    }, 
                                    method="GET"
                                )
                                with urllib_request.urlopen(req, timeout=3) as resp:
                                    res_data = json.loads(resp.read().decode("utf-8"))
                                    profile = res_data.get("data") if isinstance(res_data, dict) and "data" in res_data else res_data
                                    if isinstance(profile, dict):
                                        write_sso_session(
                                            access_token=acc["access_token"],
                                            refresh_token=acc.get("refresh_token") or "",
                                            user_info={
                                                "id": u_id or profile.get("id") or "",
                                                "nickname": profile.get("nickname"),
                                                "phone": profile.get("phone_masked") or profile.get("phone"),
                                                "phone_full": profile.get("phone"),
                                                "email": profile.get("email"),
                                                "avatar_url": profile.get("avatar_url"),
                                                "role": profile.get("role"),
                                                "roles": profile.get("roles"),
                                                "deletion_scheduled_at": profile.get("deletion_scheduled_at"),
                                            },
                                            source_app=acc.get("source_app") or "xm-bot4",
                                            device_fingerprint=data.get("device_fingerprint", "")
                                        )
                            except Exception as e:
                                _failed_sync_user_ids.add(u_id)
                                import logging
                                logging.getLogger(__name__).debug(f"[SSO Detect Sync] Failed to fetch profile for {u_id}: {e}")
                threading.Thread(target=bg_sync, daemon=True).start()

            for acc in raw_accounts:
                accounts.append({
                    "userId": acc.get("user_id"),
                    "nickname": acc.get("nickname"),
                    "phoneMasked": acc.get("phone_masked"),
                    "phone": acc.get("phone"),
                    "email": acc.get("email"),
                    "avatarUrl": acc.get("avatar_url"),
                    "accessToken": acc.get("access_token"),
                    "refreshToken": acc.get("refresh_token"),
                    "sessionId": acc.get("session_id"),
                    "updatedAt": acc.get("updated_at"),
                    "sourceApp": acc.get("source_app", "xm-bot4"),
                    "role": acc.get("role"),
                    "roles": acc.get("roles"),
                    "deletionScheduledAt": acc.get("deletion_scheduled_at"),
                })
        elif data.get('access_token'):
            # V1
            user = data.get('user', {})
            accounts.append({
                "userId": user.get('id'),
                "nickname": user.get('nickname'),
                "phoneMasked": user.get('phone'),
                "phone": user.get('phone'),
                "email": user.get('email'),
                "avatarUrl": user.get('avatar_url'),
                "accessToken": data.get('access_token'),
                "refreshToken": data.get('refresh_token'),
                "sessionId": data.get('session_id'),
                "updatedAt": data.get('updated_at'),
                "sourceApp": data.get('source_app', 'xm-bot4'),
                "role": data.get('role'),
                "roles": data.get('roles'),
                "deletionScheduledAt": data.get('deletion_scheduled_at'),
            })
            
        return ok({
            "version": data.get("version", 2),
            "active_account_id": data.get("active_account_id"),
            "device_fingerprint": data.get("device_fingerprint", ""),
            "accounts": accounts
        })
    except Exception:
        return ok({"accounts": []})


@router.post("/api/v1/sso/save")
async def save_sso_from_web(request: Request):
    """反向 SSO 回写端点（网页端登录 → 写入本地 SSO 文件 → 桌面端免登）"""
    try:
        raw = await request.body()
        body = json.loads(raw.decode("utf-8"))
    except Exception:
        return err(40000, "请求体解析失败")

    user_id = body.get("userId", "")
    if not user_id:
        return err(40000, "userId 不能为空")

    _failed_sync_user_ids.discard(user_id)

    write_sso_session(
        access_token=body.get("accessToken", ""),
        refresh_token=body.get("refreshToken", ""),
        user_info={
            "id": user_id,
            "nickname": body.get("nickname"),
            "phone": body.get("phoneMasked"),
            "avatar_url": body.get("avatarUrl"),
        },
        source_app=body.get("sourceApp", "web"),
        device_fingerprint=body.get("deviceFingerprint") or body.get("device_fingerprint", ""),
    )

    # [用户配置隔离] 回写成功后，切换同步服务客户端到该用户的 token
    try:
        from src.utils.cloud_sync import get_cloud_client
        get_cloud_client().sync_token_from_sso()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"[SSO Web] Token 热切换异常: {e}")

    # 🌟 [强力同步拉取云配置] 网页端登录成功且 Token 准备好后，立即强制从同步后端拉取最新配置
    try:
        from src.utils.config_cache import config_cache
        config_cache.load_from_cloud(clear_before_load=True)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"[SSO Web] 登录后强制同步云端配置异常: {e}")

    # [AI 服务热切换] 与 save_sso_session 保持一致，登录后重建 AI 服务
    try:
        from src.api.config_api import _reload_ai_service
        _reload_ai_service(force=True)
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug(f"[SSO Web] 登录后 AI 服务重载异常: {e}")

    return ok_msg("SSO 回写成功")


@router.post("/api/v1/sso/remove")
async def remove_sso_from_web(request: Request):
    """网页端登出后清理 SSO 文件中指定账号"""
    try:
        raw = await request.body()
        body = json.loads(raw.decode("utf-8")) if raw.strip() else {}
    except Exception:
        body = {}

    user_id = body.get("userId", "") or body.get("user_id", "")

    if user_id:
        # 按 userId 移除特定账号
        path = _sso_file_path()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                if data.get('version') == 2:
                    old_count = len(data.get('accounts', []))
                    data['accounts'] = [
                        acc for acc in data.get('accounts', [])
                        if acc.get('user_id') != user_id
                    ]
                    new_count = len(data['accounts'])
                    if new_count == 0:
                        path.unlink()
                        print(f'[xm-core/sso] 网页端请求移除账号 {user_id}，文件已清空删除')
                    else:
                        if data.get('active_account_id') == user_id:
                            data['active_account_id'] = data['accounts'][0].get('user_id')
                        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
                        print(f'[xm-core/sso] 网页端请求移除账号 {user_id}，{old_count} → {new_count}')
                else:
                    # V1 格式，直接清除整个文件
                    path.unlink()
                    print(f'[xm-core/sso] V1 格式 SSO 文件已清除')
            except Exception:
                pass
    else:
        # 未指定 userId，清除全部
        clear_sso_session()

    return ok_msg("SSO 清理成功")
