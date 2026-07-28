from __future__ import annotations
import sys
from pathlib import Path

class RecordMixin:
    @staticmethod
    def _get_record_file_path(industry_id: str = "") -> str:
        """获取测试录像保存的绝对物理路径"""
        if getattr(sys, 'frozen', False):
            base_dir = Path(sys.executable).parent
        else:
            base_dir = Path(__file__).resolve().parent.parent.parent
            
        record_dir = base_dir / "data" / "screen_records"
        record_dir.mkdir(parents=True, exist_ok=True)
        if industry_id and industry_id != "sys_001":
            return str(record_dir / f"xm_screen_record_test_{industry_id}.gif")
        return str(record_dir / "xm_screen_record_test.gif")

    def test_record_and_send(self, industry_id: str = "") -> dict:
        """测试录制 10 秒屏幕并自动发送至微信文件传输助手"""
        from app import state
        if not state.driver.is_connected():
            return {"success": False, "message": "微信驱动未连接，请先连接微信客户端！"}

        import threading
        def run_record():
            try:
                import comtypes
                comtypes.CoInitialize()
            except Exception:
                pass
            try:
                import time
                gif_path = self._get_record_file_path(industry_id)
                
                from xm_py_server import record_screen_to_gif
                success_path = record_screen_to_gif(
                    duration=10,
                    fps=4,
                    max_width=1280,
                    max_height=720,
                    colors=128,
                    output_path=gif_path
                )
                if not success_path:
                    return
                
                # 通过 UIBus 发送文件，确保 COM 线程安全和 uia_lock 串行
                from src.orchestrator.ui_bus import ui_bus, UICommand, UICommandKind, UICommandPriority
                cmd = UICommand(
                    wxid="",
                    kind=UICommandKind.SEND_FILE,
                    payload={"user": "文件传输助手", "file_path": gif_path},
                    priority=UICommandPriority.NORMAL,
                    timeout=60.0,
                )
                ui_bus.submit(cmd)
                ui_bus.await_result(cmd.id, 70.0)

                # 发送完毕后，通知前端刷新测试结果状态
                if self._window:
                    self._window.evaluate_js(
                        "if(window.__onRecordFinished) window.__onRecordFinished();"
                    )
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"录屏并发送测试失败: {e}", exc_info=True)

        threading.Thread(target=run_record, daemon=True).start()
        return {"success": True, "message": "已成功下发录屏任务，请开始操作桌面..."}

    def get_test_record_info(self, industry_id: str = "") -> dict:
        """获取当前测试录像文件的基本信息与 Base64 格式（用于前端无跨域展示）"""
        import os
        import base64
        file_path = self._get_record_file_path(industry_id)
        if not os.path.exists(file_path):
            return {"exists": False}
        
        try:
            with open(file_path, "rb") as f:
                content = f.read()
            b64_data = base64.b64encode(content).decode("utf-8")
            data_url = f"data:image/gif;base64,{b64_data}"
            return {
                "exists": True,
                "path": file_path,
                "dataUrl": data_url,
                "size": os.path.getsize(file_path)
            }
        except Exception as e:
            return {"exists": False, "error": str(e)}

    def open_record_directory(self, industry_id: str = "") -> bool:
        """在文件管理器中打开当前录像所在文件夹并默认定位到该文件（调用全局公共方法）"""
        from xm_py_server import show_in_file_manager
        file_path = self._get_record_file_path(industry_id)
        return show_in_file_manager(file_path)

    def delete_record_file(self, industry_id: str = "") -> bool:
        """物理删除已录制的测试视频文件"""
        import os
        file_path = self._get_record_file_path(industry_id)
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                return True
            except Exception:
                return False
        return False
