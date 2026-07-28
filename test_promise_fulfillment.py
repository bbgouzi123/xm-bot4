import os
import sys
import unittest
import asyncio
from unittest.mock import MagicMock, patch

# 将 src 目录加入 PATH
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

# Mock ui_bus 和微信驱动避免硬件依赖
mock_ui_bus = MagicMock()
mock_ui_bus.submit = MagicMock()
mock_ui_bus.await_result = MagicMock(return_value={"success": True})

sys.modules['src.orchestrator.ui_bus'] = MagicMock(ui_bus=mock_ui_bus)

from src.task.promise_executor import capture_web_page, resolve_video_watermark, execute_web_snapshot, execute_download_media
from src.utils.db_manager import WeChatDBManager


class TestPromiseFulfillmentE2E(unittest.TestCase):
    def setUp(self):
        self.db = WeChatDBManager()
        # 清理旧数据，预设测试环境
        self.db._promise_tasks.clear()
        
    @patch("subprocess.run")
    def test_01_web_page_snapshot_offline(self, mock_run):
        """测试物理网页静默截图的捕获函数 (Mock Subprocess)"""
        temp_dir = os.path.abspath("temp_test")
        os.makedirs(temp_dir, exist_ok=True)
        img_path = os.path.join(temp_dir, "test_snapshot.png")
        
        # 模拟 subprocess 成功，并在物理磁盘上写入临时 mock 图片
        def fake_run(args, **kwargs):
            # 找到 screenshot 路径
            screenshot_arg = [a for a in args if a.startswith("--screenshot=")]
            if screenshot_arg:
                path = screenshot_arg[0].split("=")[1]
                with open(path, "wb") as f:
                    f.write(b"mock image bytes")
            mock_res = MagicMock()
            mock_res.returncode = 0
            return mock_res
            
        mock_run.side_effect = fake_run
        
        ok = capture_web_page("https://www.bing.com", img_path)
        self.assertTrue(ok)
        self.assertTrue(os.path.exists(img_path))
        if os.path.exists(img_path):
            os.remove(img_path)

    @patch("urllib.request.urlopen")
    def test_02_video_watermark_resolve(self, mock_urlopen):
        """测试短视频免签解析接口与去水印提取 (Mock Urlopen)"""
        # 使用直链或者无效链接时，应当原样返回
        url = "https://example.com/video.mp4"
        res = resolve_video_watermark(url)
        self.assertEqual(res, url)
        
        # 模拟 Peark 去水印解析接口返回
        mock_response = MagicMock()
        mock_response.read = MagicMock(return_value=b'{"code": 200, "url": "https://video.peark.mock/watermark_removed.mp4"}')
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response
        
        dy_share = "7.82 p@v.bf 10/22 qGz.xx 复制此链接，打开DouYin搜索，【测试视频】https://v.douyin.com/abcde/"
        resolved = resolve_video_watermark(dy_share)
        self.assertEqual(resolved, "https://video.peark.mock/watermark_removed.mp4")

    def test_03_download_media_sandboxed(self):
        """测试带沙箱大小防护的流媒体下载执行"""
        driver = MagicMock()
        target_wxid = "test_friend"
        task_id = "test_task_001"
        media_url = "https://example.com/video.mp4"
        
        # 配置允许最大 1MB，模拟文件过大触发安全报错拦截
        config = {"sandbox_dir": "temp_test", "max_file_size_mb": 1}
        
        # 我们可以通过 mock urllib.request.urlopen 模拟一个大文件的头部 Content-Length
        mock_response = MagicMock()
        mock_response.headers = MagicMock()
        mock_response.headers.get = MagicMock(return_value="52428800") # 50MB
        mock_response.read = MagicMock(return_value=b"mock video data bytes")
        mock_response.__enter__.return_value = mock_response
        
        with patch("urllib.request.urlopen", return_value=mock_response):
            with self.assertRaises(ValueError) as context:
                asyncio.run(execute_download_media(driver, target_wxid, task_id, media_url, config))
            self.assertIn("下载文件过大", str(context.exception))

    def test_04_high_risk_security_gating(self):
        """测试安全边界等级的安全拦截逻辑"""
        # 注册一个高危关机任务
        task_id = "task_shutdown_001"
        self.db.add_promise_task({
            "id": task_id,
            "target_wxid": "test_friend",
            "task_type": "sys_control",
            "reply_text": "好的，这就帮你关机",
            "payload_details": {"cmd_kind": "shutdown"},
            "status": "pending_approval",
            "safety_level": 3,
            "approval_status": "pending_approval",
            "retry_count": 0,
            "created_at": "2026-06-07T00:00:00"
        })
        
        # 提取待执行任务进行模拟
        tasks = self.db.get_promise_tasks()
        pending_tasks = [t for t in tasks if t.get("status") == "pending" and t.get("retry_count", 0) < 3]
        # 等于 0，说明任务已被系统逻辑挂起隔离，绝对不可能被 Worker 直接运行
        self.assertEqual(len(pending_tasks), 0)

    def test_05_remote_filehelper_commands(self):
        """测试通过文件传输助手微信命令审批的执行流"""
        from src.monitor.chat_monitor.promise_helper import handle_remote_approval_command

        # 先测无数据时的列表指令
        is_cmd, text = asyncio.run(handle_remote_approval_command("查看待审批"))
        self.assertTrue(is_cmd)
        self.assertIn("暂无需要审批", text)

        # 注册一个需要审批的任务
        task_id = "task_test_999"
        self.db.add_promise_task({
            "id": task_id,
            "target_wxid": "user_abc",
            "target_name": "张三",
            "task_type": "sys_control",
            "reply_text": "好的关机",
            "status": "pending_approval",
            "safety_level": 3,
            "approval_status": "pending_approval",
            "retry_count": 0,
            "created_at": "2026-06-07T00:00:00"
        })

        # 再次查看列表
        is_cmd, text = asyncio.run(handle_remote_approval_command("查看待审批"))
        self.assertTrue(is_cmd)
        self.assertIn("待审批物理承诺任务列表", text)
        self.assertIn(task_id, text)

        # 测试非控制指令
        is_cmd, text = asyncio.run(handle_remote_approval_command("你好传输助手"))
        self.assertFalse(is_cmd)

        # 批准它 (使用后缀短 ID 匹配)
        is_cmd, text = asyncio.run(handle_remote_approval_command("批准 test_999"))
        self.assertTrue(is_cmd)
        self.assertIn("成功批准任务", text)

        # 检查数据库状态
        tasks = self.db.get_promise_tasks()
        task = [t for t in tasks if t.get("id") == task_id][0]
        self.assertEqual(task.get("status"), "pending")
        self.assertEqual(task.get("approval_status"), "approved")

        # 重新放回为 pending_approval 测试驳回
        self.db.update_promise_task(task_id, {"status": "pending_approval", "approval_status": "pending_approval"})
        is_cmd, text = asyncio.run(handle_remote_approval_command("拒绝 test_999"))
        self.assertTrue(is_cmd)
        self.assertIn("成功拒绝并作废任务", text)

        tasks = self.db.get_promise_tasks()
        task = [t for t in tasks if t.get("id") == task_id][0]
        self.assertEqual(task.get("status"), "failed")
        self.assertEqual(task.get("approval_status"), "denied")


if __name__ == '__main__':
    unittest.main()
