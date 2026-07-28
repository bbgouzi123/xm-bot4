"""
配置连通性测试与服务重载子模块 (config_test_api.py)
从 base_config.py 中拆分以遵守 300 行代码质量规范。
"""
from fastapi import Request
import time
from src.utils.response import ok, err, ok_msg
from .state import router, CONFIG_DIR
from . import state

def _reload_friend_request_monitor(configs):
    auto_accept = configs.get("friend_request_settings", {}).get("auto_accept", False)
    try:
        from app.state import account_manager as am
        if am:
            for inst in am._instances.values():
                if inst.friend_request_monitor:
                    if auto_accept:
                        if not inst.friend_request_monitor.is_running():
                            inst.friend_request_monitor.start()
                    else:
                        if inst.friend_request_monitor.is_running():
                            inst.friend_request_monitor.stop()

        if auto_accept:
            if state._friend_request_monitor is None:
                from src.monitor.friend_request_monitor import FriendRequestMonitor
                state._friend_request_monitor = FriendRequestMonitor(state._driver, state._ai_service)
            if not state._friend_request_monitor.is_running():
                state._friend_request_monitor.start()
        else:
            if state._friend_request_monitor is not None and state._friend_request_monitor.is_running():
                state._friend_request_monitor.stop()
    except Exception as e:
        print(f"[配置更新] 好友请求监控器加载异常: {e}")

_reload_ai_service_last_run = 0.0
_RELOAD_AI_DEBOUNCE_SEC = 3.0
_RELOAD_AI_HARD_COOLDOWN = 1.0  # 1 秒硬防抖冷区
_last_loaded_configs = None

def _reload_ai_service(force: bool = False):
    global _reload_ai_service_last_run, _last_loaded_configs
    now = time.monotonic()
    
    # 1. 即使 force=True，如果距上一次执行小于 1 秒，也直接阻断，防止冷启动并发惊群
    if now - _reload_ai_service_last_run < _RELOAD_AI_HARD_COOLDOWN:
        return
        
    # 2. 如果是非 force 的日常热加载，遵循 3 秒普通防抖
    if not force and (now - _reload_ai_service_last_run < _RELOAD_AI_DEBOUNCE_SEC):
        return

    try:
        # 延迟局部导入，避免循环引用
        from .base_config import _load_configs
        configs = _load_configs()
        
        # 3. 提取 AI 核心配置，仅当核心配置改变且服务已就绪时才重载，过滤 updated_at 等无关字段干扰
        def _extract_ai_fields(cfg: dict) -> dict:
            if not cfg:
                return {}
            keys = ("coze_settings", "external_api_settings", "ai_platform", "agents",
                    "text_settings", "image_settings", "video_settings", "_platform_managed")
            return {k: cfg.get(k) for k in keys}

        current_ai = _extract_ai_fields(configs)
        last_ai = _extract_ai_fields(_last_loaded_configs)

        if _last_loaded_configs is not None and current_ai == last_ai and state._ai_service is not None:
            return
            
        from src.ai.factory import AIServiceFactory
        from src.api import chat as chat_module
        
        new_service = AIServiceFactory.create_from_full_config(configs)
        if new_service and new_service.is_configured():
            state._ai_service = new_service
            _last_loaded_configs = configs
            _reload_ai_service_last_run = now  # 更新最后一次成功执行的时间戳
            
            if chat_module._monitor:
                chat_module._monitor.ai_service = new_service

            if state._friend_request_monitor:
                state._friend_request_monitor.ai_service = new_service

            import app.state as app_state
            app_state.ai_service = new_service
            try:
                from app.state import account_manager
                if account_manager:
                    # 🌟 核心同步：更新多开管理器上的 ai_service 引用，以确保后续动态发现并绑定新微信实例时能获取到最新的 AI 服务
                    account_manager.ai_service = new_service
                    for inst in account_manager._instances.values():
                        if inst.monitor:
                            inst.monitor.ai_service = new_service
                        if inst.friend_request_monitor:
                            inst.friend_request_monitor.ai_service = new_service
            except Exception as sync_err:
                print(f"[配置] 多开实例 AI 服务同步异常: {sync_err}")
            if getattr(app_state, 'moment_interaction_manager', None):
                app_state.moment_interaction_manager.ai_service = new_service

            print(f"[配置] AI 服务已切换: {new_service.platform}")
        else:
            print("[配置] AI 服务未配置或无效")
    except Exception as e:
        print(f"[配置] AI 重新加载失败: {e}")


def _reload_customer_adapters(configs: dict):
    try:
        from src.api.customer_api.adapter_factory import CustomerAdapterFactory as CAF, start_queue_worker as SQW
        CAF.load_config(configs.get("customer_api_settings", {}))
        SQW()
    except Exception as e: 
        print(f"[配置更新] 客户 API 适配器热加载异常: {e}")

@router.post("/api/test/ai")
async def test_ai_connection(request: Request, capability: str = "text"):
    data = await request.json()
    logs = []
    def _add(entry: str, status: str = "success"): logs.append({"log": entry, "status": status})
    start = time.time()
    _add(f"🚀 初始化诊断引擎 ({capability})...", "running")
    try:
        from src.ai.factory import AIServiceFactory
        test_service = AIServiceFactory.create_from_full_config(data)
        active_service = None
        if hasattr(test_service, 'text_service'):
            if capability == "image": active_service = test_service.image_service
            elif capability == "video": active_service = test_service.video_service
            else: active_service = test_service.text_service
        else: active_service = test_service
        if not active_service or not active_service.is_configured():
            _add("❌ AI 配置缺失关键参数", "error")
            return ok(logs)
        provider = active_service.platform.lower()
        _add(f"✅ 挂载成功 | 平台: [ {provider.upper()} ]")
        if capability == "image":
            _add("🎨 绘制图像...")
            img_url = await active_service.generate_image("a small cute orange cat")
            if img_url: _add(f"⚡ [成功] 耗时: {int((time.time() - start)*1000)}ms | URL: {img_url}", "success")
            else: _add("❌ 图像生成失败", "error")
        elif capability == "video":
            _add("🎬 生成视频...")
            video_url = await active_service.generate_video("a small cute cat playing")
            if video_url: _add(f"⚡ [成功] 耗时: {int((time.time() - start)*1000)}ms | URL: {video_url}", "success")
            else: _add("❌ 视频生成失败", "error")
        elif provider == "coze":
            _add("✉️ 发送破冰暗号...")
            response = await active_service.start_chat(message="你好！请回复“连接成功”", cache_session=False)
            if response.get("success"):
                _add(f"⚡ [通畅] Coze 延迟: {response.get('elapsed', int((time.time() - start)*1000))}ms | « {response.get('content', '').strip()} »", "success")
            else: _add(f"❌ 通道阻断: {response.get('error', '未知异常')}", "error")
        else:
            _add("✉️ 发送破冰暗号...")
            response = await active_service.start_chat(message="你好！请回复“连接成功”", cache_session=False)
            if response.get("success"):
                _add(f"⚡ [通畅] 延迟: {response.get('elapsed', int((time.time() - start)*1000))}ms | « {response.get('content', '').strip()} »", "success")
            else: _add(f"❌ 通道阻断: {response.get('error', '未知异常')}", "error")
    except Exception as e: _add(f"🔥 致命错误: {str(e)}", "error")
    return ok(logs)

@router.post("/api/config/coze-check")
async def test_coze_cookie(request: Request):
    from src.utils.coze_auth_helper import auto_activate_coze
    data = await request.json()
    res = await auto_activate_coze(data.get("coze_cookie", ""))
    return ok(res) if res.get("success") else err(40000, res.get("message", "测试失败"), res)

@router.post("/api/test/email-alert")
async def test_email_alert(request: Request):
    try:
        from src.utils.alert_notifier import alert_notifier
        await alert_notifier.send_test_email((await request.json()).get("alert_email_settings", {}))
        return ok_msg("测试邮件发送成功")
    except Exception as e: return err(40000, f"邮件测试发送失败: {str(e)}")

@router.post("/api/test/feishu-alert")
async def test_feishu_alert(request: Request):
    try:
        url = (await request.json()).get("alert_feishu_settings", {}).get("webhook_url", "").strip()
        if not url: return err(40000, "测试失败：Webhook URL 不能为空")
        from src.utils.feishu_notifier import feishu_notifier
        orig = feishu_notifier._env_webhook_url
        feishu_notifier._env_webhook_url = url
        try:
            success = await feishu_notifier.send_alert_card(
                title="✅ 飞书告警通道连通性测试 ✅",
                content="如果您收到了这条卡片消息，代表您的 xm-bot4 飞书机器人 Webhook 告警通道已成功打通！",
                level="info"
            )
            return ok_msg("测试消息已成功投递到飞书") if success else err(40000, "发送测试消息到飞书失败，请检查 Webhook")
        finally: feishu_notifier._env_webhook_url = orig
    except Exception as e: return err(40000, f"测试发送异常: {str(e)}")
