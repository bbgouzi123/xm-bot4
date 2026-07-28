from __future__ import annotations

class UtilsMixin:
    def clipboard_read_text(self) -> str:
        try:
            import pyperclip
            return str(pyperclip.paste() or '')
        except Exception:
            return ''

    def clipboard_write_text(self, text: str) -> bool:
        try:
            import pyperclip
            pyperclip.copy(text if text is not None else '')
            return True
        except Exception:
            return False

    def capture_screenshot(self) -> dict:
        """原生屏幕截图API：隐藏当前窗口，使用 Pillow 截取全屏，返回 Base64 格式，并恢复窗口"""
        import time
        import base64
        import logging
        from io import BytesIO
        from PIL import ImageGrab
        
        logger = logging.getLogger(__name__)
        is_hidden = False
        try:
            if self._window:
                self.hide_window()
                is_hidden = True
                # 等待窗口隐藏动画/操作完成，避免把自己截进去
                time.sleep(0.35)
        except Exception as e:
            logger.warning(f"隐藏主窗口失败: {e}")
            
        try:
            # 截取全屏
            img = ImageGrab.grab()
            
            # 转换为 base64
            buffered = BytesIO()
            img.save(buffered, format="PNG")
            img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
            data_url = f"data:image/png;base64,{img_str}"
            
            return {
                "success": True,
                "dataUrl": data_url
            }
        except Exception as e:
            logger.error(f"原生截图失败: {e}")
            return {
                "success": False,
                "message": f"截图失败: {str(e)}"
            }
        finally:
            if is_hidden and self._window:
                try:
                    self.raise_main_window()
                except Exception as e:
                    logger.warning(f"恢复主窗口失败: {e}")

    def select_files(self) -> dict:
        """打开原生选择文件对话框，返回选中的文件物理路径列表"""
        import webview
        import logging
        logger = logging.getLogger(__name__)
        if not self._window:
            return {"success": False, "message": "窗口未初始化"}
        try:
            file_paths = self._window.create_file_dialog(
                dialog_type=webview.OPEN_DIALOG,
                allow_multiple=True
            )
            if not file_paths:
                return {"success": True, "filePaths": []}
            if isinstance(file_paths, str):
                file_paths = [file_paths]
            elif isinstance(file_paths, (tuple, list)):
                file_paths = list(file_paths)
            else:
                file_paths = []
            return {
                "success": True,
                "filePaths": file_paths
            }
        except Exception as e:
            logger.error(f"打开原生文件选择框失败: {e}")
            return {
                "success": False,
                "message": f"选择文件失败: {str(e)}"
            }

    def save_file(self, filename: str, content: str) -> dict:
        """打开原生保存文件对话框，并将内容保存到该路径下"""
        import webview
        import logging
        logger = logging.getLogger(__name__)
        if not self._window:
            return {"success": False, "message": "窗口未初始化"}
        try:
            save_path = self._window.create_file_dialog(
                dialog_type=webview.SAVE_DIALOG,
                save_filename=filename
            )
            if not save_path:
                return {"success": False, "message": "已取消"}
                
            # 写入文件内容，使用 utf-8-sig 写入 BOM 头以防 Excel 乱码
            with open(save_path, "w", encoding="utf-8-sig") as f:
                f.write(content)
                
            return {
                "success": True,
                "savePath": save_path
            }
        except Exception as e:
            logger.error(f"保存文件失败: {e}")
            return {
                "success": False,
                "message": f"保存文件失败: {str(e)}"
            }

    def select_directory(self) -> dict:
        """打开原生选择文件夹对话框，返回选中的文件夹物理路径"""
        import webview
        import logging
        logger = logging.getLogger(__name__)
        if not self._window:
            return {"success": False, "message": "窗口未初始化"}
        try:
            folder_paths = self._window.create_file_dialog(
                dialog_type=webview.FOLDER_DIALOG
            )
            if not folder_paths:
                return {"success": True, "folderPath": ""}
            if isinstance(folder_paths, str):
                path = folder_paths
            elif isinstance(folder_paths, (tuple, list)) and len(folder_paths) > 0:
                path = folder_paths[0]
            else:
                path = ""
            return {
                "success": True,
                "folderPath": path
            }
        except Exception as e:
            logger.error(f"打开原生文件夹选择框失败: {e}")
            return {
                "success": False,
                "message": f"选择文件夹失败: {str(e)}"
            }

    def select_file_single(self) -> dict:
        """打开原生选择单个文件对话框，返回选中的文件物理路径"""
        import webview
        import logging
        logger = logging.getLogger(__name__)
        if not self._window:
            return {"success": False, "message": "窗口未初始化"}
        try:
            file_paths = self._window.create_file_dialog(
                dialog_type=webview.OPEN_DIALOG,
                allow_multiple=False
            )
            if not file_paths:
                return {"success": True, "filePath": ""}
            if isinstance(file_paths, str):
                path = file_paths
            elif isinstance(file_paths, (tuple, list)) and len(file_paths) > 0:
                path = file_paths[0]
            else:
                path = ""
            return {
                "success": True,
                "filePath": path
            }
        except Exception as e:
            logger.error(f"打开原生文件选择框失败: {e}")
            return {
                "success": False,
                "message": f"选择文件失败: {str(e)}"
            }

