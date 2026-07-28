import logging
import io
import base64
import asyncio
from typing import Optional, Set

from src.utils.websocket_manager import ws_manager
from src.utils.feishu_notifier import feishu_notifier

# 动态按需引入 Pillow 和 win32gui
try:
    from PIL import ImageGrab
    import win32gui
except ImportError:
    ImageGrab = None
    win32gui = None

logger = logging.getLogger(__name__)

# 全局后台任务集合，防止正在运行的协程被垃圾回收从而触发 Task was destroyed but it is pending 警告
background_tasks: Set[asyncio.Task] = set()

def _send_email_blocking(smtp_host, smtp_port, smtp_user, smtp_pass, receiver, html_body, account_id, is_test=False):
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from email.header import Header

    msg = MIMEMultipart()
    if is_test:
        msg['From'] = Header(f"xm-bot4 机器人 <{smtp_user}>", 'utf-8')
        msg['To'] = Header(receiver, 'utf-8')
        msg['Subject'] = Header("【xm-bot4】风控告警邮件通道连通性测试", 'utf-8')
    else:
        msg['From'] = Header(f"xm-bot4 机器人告警 <{smtp_user}>", 'utf-8')
        msg['To'] = Header(receiver, 'utf-8')
        msg['Subject'] = Header(f"【紧急风控告警】微信号 {account_id} 触发异常阻断", 'utf-8')
        
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))
    
    if smtp_port == 465:
        server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15)
    else:
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=15)
        server.starttls()
    try:
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, [receiver], msg.as_string())
    finally:
        server.quit()

class AlertNotifier:
    """全局统一风控告警神经网 (Phase 7 整合层)
    
    能够同时穿透前台(WebSocket) 与 后台(企业微信/飞书/Webhook)，
    遇到严重风控 (封号、限制登录、外挂限制) 时强制停止流水线并唤醒人工。
    """
    
    @staticmethod
    def capture_window_base64(hwnd: int) -> Optional[str]:
        """抓取指定窗口句柄的截图并转换为 Base64 字符串"""
        if not hwnd or ImageGrab is None or win32gui is None:
            return None
        try:
            # 获取目标微信窗口的边框范围
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            if right - left <= 0 or bottom - top <= 0:
                logger.warning(f"[截图失败] 窗口句柄 {hwnd} 获取到的尺寸无效: ({left}, {top}, {right}, {bottom})")
                return None
            
            # 使用 PIL 进行像素截取
            img = ImageGrab.grab(bbox=(left, top, right, bottom))
            
            # 缩放图片以防 Base64 字符串过大导致 WebSocket 帧阻塞 (最大宽度 800px)
            max_width = 800
            if img.width > max_width:
                ratio = max_width / float(img.width)
                new_height = int(float(img.height) * ratio)
                # 使用 Pillow 兼容的 Resampling 选项 (PIL 9+ 使用 Resampling.LANCZOS, 否则用 ANTIALIAS)
                try:
                    from PIL import Image
                    img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
                except AttributeError:
                    img = img.resize((max_width, new_height), 1) # 兼容老版本 ANTIALIAS 的值
            
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            byte_data = buf.getvalue()
            return base64.b64encode(byte_data).decode('utf-8')
        except Exception as e:
            logger.error(f"[截图失败] 无法截取句柄 {hwnd} 的屏幕: {e}")
            return None

    @classmethod
    async def send_alert_email(cls, machine_code: str, account_id: str, title: str, reason: str, screenshot_b64: Optional[str] = None):
        """异步在线程池中发送风控告警邮件"""
        current_task = None
        try:
            current_task = asyncio.current_task()
            if current_task:
                background_tasks.add(current_task)
        except Exception:
            pass

        try:
            try:
                from src.api.config_api.base_config import _load_configs
                configs = _load_configs()
            except Exception as ce:
                logger.error(f"[邮件告警] 加载配置失败: {ce}")
                return

            email_settings = configs.get("alert_email_settings", {})
            if not email_settings.get("enabled", False):
                return
            
            receiver = email_settings.get("receiver_email", "").strip()
            if not receiver:
                logger.warning("[邮件告警] 已启用但未配置接收邮箱")
                return
                
            smtp_host = email_settings.get("smtp_host", "").strip()
            smtp_port = int(email_settings.get("smtp_port", 465))
            smtp_user = email_settings.get("smtp_user", "").strip()
            smtp_pass = email_settings.get("smtp_pass", "").strip()
            
            if not smtp_host or not smtp_user or not smtp_pass:
                logger.warning("[邮件告警] 缺少发信邮箱 SMTP 配置，无法发送邮件")
                return

            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            from email.header import Header
            
            img_tag = ""
            if screenshot_b64:
                img_tag = f'<div style="margin-top:20px;"><p style="color:#64748B;font-size:14px;margin-bottom:8px;font-weight:600;">微信故障现场截图：</p><div style="border:1px solid #E2E8F0;border-radius:12px;padding:4px;background:#F8FAFC;display:inline-block;max-width:100%;"><img src="data:image/png;base64,{screenshot_b64}" style="max-width:100%;height:auto;border-radius:8px;display:block;" alt="WeChat Screenshot" /></div></div>'
            
            html_body = f"<!DOCTYPE html><html><head><meta charset='utf-8'></head><body style='margin:0;padding:0;background:#f4f6f8;font-family:sans-serif;'><div style='max-width:600px;margin:40px auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 8px 30px rgba(0,0,0,0.05);border:1px solid #E2E8F0;'><div style='background:#EF4444;padding:24px;text-align:center;color:#ffffff;'><h2 style='margin:0;font-size:20px;font-weight:800;'>⚠️ xm-bot4 风控异常告警 ⚠️</h2></div><div style='padding:32px 24px;'><p style='color:#0F172A;font-size:16px;margin:0 0 16px;font-weight:bold;border-left:4px solid #EF4444;padding-left:8px;'>系统检测到微信运行环境异常：</p><div style='background:#F8FAFC;border:1px solid #E2E8F0;border-radius:12px;padding:20px;margin-bottom:20px;'><table style='width:100%;border-collapse:collapse;font-size:14px;color:#334155;'><tr style='border-bottom:1px solid #F1F5F9;'><td style='padding:10px 0;font-weight:600;width:100px;color:#64748B;'>设备码</td><td style='padding:10px 0;'>{machine_code}</td></tr><tr style='border-bottom:1px solid #F1F5F9;'><td style='padding:10px 0;font-weight:600;color:#64748B;'>受波及账号</td><td style='padding:10px 0;font-weight:bold;color:#0F172A;'>{account_id}</td></tr><tr style='border-bottom:1px solid #F1F5F9;'><td style='padding:10px 0;font-weight:600;color:#64748B;'>异常类型</td><td style='padding:10px 0;color:#EF4444;font-weight:600;'>{title}</td></tr><tr><td style='padding:10px 0;font-weight:600;vertical-align:top;color:#64748B;'>原生详情</td><td style='padding:10px 0;white-space:pre-wrap;line-height:1.6;'>{reason}</td></tr></table></div><p style='color:#0F172A;font-size:14px;font-weight:600;margin:24px 0 8px;'>🔧 处置建议：</p><p style='color:#475569;font-size:14px;margin:0 0 20px;line-height:1.6;background:#FEF3C7;border-left:4px solid #F59E0B;padding:12px;border-radius:6px;'>系统已为您<strong>自动暂停了当前加粉任务流水线</strong>，以规避违规封号风险。请立即通过前台控制台查看截图，或人工接管微信客户端处理弹窗！</p>{img_tag}</div><div style='background:#F8FAFC;padding:16px;text-align:center;border-top:1px solid #E2E8F0;font-size:12px;color:#94A3B8;'>© xm-bot4 智能机器人客服系统 · 请勿直接回复此邮件</div></div></body></html>"
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    _send_email_blocking,
                    smtp_host,
                    smtp_port,
                    smtp_user,
                    smtp_pass,
                    receiver,
                    html_body,
                    account_id,
                    False
                )
                logger.info(f"[邮件告警] 成功向 {receiver} 发送了风控异常告警邮件")
            except Exception as ex:
                logger.error(f"[邮件告警] 线程投递发信失败: {ex}")
        finally:
            if current_task:
                background_tasks.discard(current_task)

    @classmethod
    async def send_test_email(cls, email_settings: dict) -> None:
        """发送测试邮件以验证 SMTP 参数"""
        receiver = email_settings.get("receiver_email", "").strip()
        smtp_host = email_settings.get("smtp_host", "").strip()
        smtp_port = int(email_settings.get("smtp_port", 465))
        smtp_user = email_settings.get("smtp_user", "").strip()
        smtp_pass = email_settings.get("smtp_pass", "").strip()
        
        if not receiver:
            raise ValueError("接收人邮箱不能为空")
        if not smtp_host or not smtp_user or not smtp_pass:
            raise ValueError("发信服务器(SMTP)配置不完整")

        import smtplib
        import time
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        from email.header import Header

        html_body = f"<!DOCTYPE html><html><head><meta charset='utf-8'></head><body style='margin:0;padding:0;background:#f4f6f8;font-family:sans-serif;'><div style='max-width:600px;margin:40px auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 8px 30px rgba(0,0,0,0.05);border:1px solid #E2E8F0;'><div style='background:#10B981;padding:24px;text-align:center;color:#ffffff;'><h2 style='margin:0;font-size:20px;font-weight:800;'>✅ 邮件告警测试成功 ✅</h2></div><div style='padding:32px 24px;text-align:center;'><p style='color:#0F172A;font-size:16px;font-weight:bold;margin-bottom:12px;'>这是一封测试邮件</p><p style='color:#475569;font-size:14px;line-height:1.6;margin-bottom:24px;'>您的 SMTP 邮箱配置工作状态正常。当微信号运行发生风控异常、被踢下线或流水线报错崩溃时，系统将自动向您发送此类告警通知。</p><div style='display:inline-block;padding:8px 16px;background:#ECFDF5;color:#047857;border-radius:20px;font-size:12px;font-weight:600;'>测试时间: {time.strftime('%Y-%m-%d %H:%M:%S')}</div></div><div style='background:#F8FAFC;padding:16px;text-align:center;border-top:1px solid #E2E8F0;font-size:12px;color:#94A3B8;'>© xm-bot4 智能机器人客服系统</div></div></body></html>"
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            _send_email_blocking,
            smtp_host,
            smtp_port,
            smtp_user,
            smtp_pass,
            receiver,
            html_body,
            "",
            True
        )

    @classmethod
    async def trigger_risk_alert(cls, machine_code: str, account_id: str, reason: str, is_fatal: bool = False, hwnd: int = 0):
        """触发系统级风控告警"""
        screenshot_b64 = cls.capture_window_base64(hwnd) if hwnd > 0 else None
        screenshot_notice = "\n**实时截图**: [系统前台控制台已同步捕获故障微信客户端截图]" if screenshot_b64 else ""

        title = "⚠️ 触发风控频率警报" if not is_fatal else "🚨 致命风控！账号下线阻断"
        content = (
            f"**终端所在机器**: {machine_code}\n"
            f"**波及的微信号**: {account_id}\n"
            f"**原生阻断详情**: {reason}\n"
            f"**处置建议**: 请立即停止自动化任务循环，人工接管微信客户端处理弹窗或解绑！{screenshot_notice}"
        )
        
        level = "fatal" if is_fatal else "error"
        logger.error(f"[RISK DETECTED] {title} - Account: {account_id} - Reason: {reason} - Has Screenshot: {screenshot_b64 is not None}")
        
        # 1. 穿透至屏幕前台，对坐在电脑前的销售下发 WebSocket 强制确认弹窗 (附带 Base64 实时截图)
        await ws_manager.broadcast_alert(level=level, title=title, content=content, screenshot=screenshot_b64)
        
        # 2. 穿透至同步后端飞书或钉钉群，防止该销售下班挂机离开工位，通知运维接管
        await feishu_notifier.send_alert_card(title=title, content=content, level=level)

        # 3. 穿透至用户配置的紧急接收邮箱
        email_task = asyncio.create_task(
            cls.send_alert_email(
                machine_code=machine_code,
                account_id=account_id,
                title=title,
                reason=reason,
                screenshot_b64=screenshot_b64
            )
        )
        background_tasks.add(email_task)
        email_task.add_done_callback(background_tasks.discard)

        # 4. 穿透至用户消息通知中心
        try:
            noti_task = asyncio.create_task(
                cls.send_user_notification(
                    title=title,
                    body=f"微信号 {account_id} {reason}",
                    category="alert"
                )
            )
            background_tasks.add(noti_task)
            noti_task.add_done_callback(background_tasks.discard)
        except Exception:
            pass

    @classmethod
    async def send_user_notification(cls, title: str, body: str, category: str = "alert", action_url: Optional[str] = None, action_label: Optional[str] = None):
        """发送实时通知到 xm-user 消息中心"""
        current_task = None
        try:
            current_task = asyncio.current_task()
            if current_task:
                background_tasks.add(current_task)
        except Exception:
            pass

        try:
            try:
                from src.utils.license_validator.subscription import SubscriptionMixin
                user_id = SubscriptionMixin._get_sso_user_id()
                if not user_id:
                    return
                from src.utils.license_validator.env import license_client
                try:
                    from src.sso_bridge import read_sso_session
                    session = read_sso_session()
                    if session and session.get("access_token"):
                        license_client.set_token(session["access_token"])
                except Exception as token_err:
                    logger.debug(f"[通知中心] 同步 SSO Token 失败: {token_err}")

                payload = {
                    "app_key": "xm-bot4-python",
                    "app_secret": "xms_official_bot4_python_secret",
                    "user_id": user_id,
                    "source": "xm-bot4",
                    "category": category,
                    "title": title,
                    "body": body,
                    "channels": ["inbox"]
                }
                if action_url:
                    payload["action_url"] = action_url
                if action_label:
                    payload["action_label"] = action_label
                loop = asyncio.get_event_loop()
                res = await loop.run_in_executor(None, lambda: license_client.post("/api/v1/messages/send", payload))
                if res and res.get("success") is True:
                    logger.info(f"[通知中心] 发送消息成功: {title}")
                else:
                    err = res.get('error') if res else None
                    err_str = str(err) if err else ""
                    # 良性情况：
                    #   1. res=None —— 网络不通或接口未部署，静默跳过（debug 级别）
                    #   2. 404/不存在 —— 用户尚未注册通知账号，属于正常情况
                    #   3. err 包含 "不存在" / "not found" / "404" 等描述
                    is_benign = (
                        res is None
                        or any(kw in err_str.lower() for kw in ("不存在", "not found", "404", "no user", "用户不存在"))
                    )
                    if is_benign:
                        logger.debug(f"[通知中心] 消息通知接口暂不可用或用户未注册，静默跳过 (err={err_str or 'None'})")
                    else:
                        logger.warning(f"[通知中心] 发送消息失败: {err_str}")
            except Exception as e:
                logger.error(f"[通知中心] 发送消息异常: {e}")
        finally:
            if current_task:
                background_tasks.discard(current_task)

# 静态导出供 UIA 层或外层直接调用
alert_notifier = AlertNotifier()
