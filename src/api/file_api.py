"""
文件 API（移植自 xm-bot4 api/file.py — 112行部分反编译）

原始文件: api/file.py (PARTIAL(5), 112 lines)
处理文件上传、下载和微信文件发送。
"""
import os
import uuid
import shutil
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Form, Request
from fastapi.responses import FileResponse
from typing import Optional
from src.utils.excel_parser import ExcelParser
from src.utils.db_manager import WeChatDBManager
from src.utils.response import ok, err, ok_msg




router = APIRouter(prefix='/api/file', tags=['file'])

# 文件存储目录
UPLOAD_DIR = Path.home() / '.xm-ai-bot' / 'uploads'
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@router.post('/upload')
async def upload_file(file: UploadFile = File(...)):
    """上传文件"""
    try:
        # 生成唯一文件名
        ext = os.path.splitext(file.filename)[1]
        unique_name = f'{uuid.uuid4().hex}{ext}'
        save_path = UPLOAD_DIR / unique_name

        # 保存文件
        with open(save_path, 'wb') as f:
            content = await file.read()
            f.write(content)

        return ok({
            'file_id': unique_name,
            'file_name': file.filename,
            'file_path': str(save_path),
            'size': len(content),
        })
    except Exception as e:
        return err(40000, str(e))


@router.post('/parse-leads')
async def parse_leads(file: UploadFile = File(...)):
    """(Phase 9) 接收拓客名单 Excel 并导入数据库"""
    try:
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in ['.xlsx', '.xls']:
            return err(40000, '仅支持 Excel 格式文件')
            
        unique_name = f'leads_{uuid.uuid4().hex}{ext}'
        save_path = UPLOAD_DIR / unique_name

        with open(save_path, 'wb') as f:
            content = await file.read()
            f.write(content)

        # 调用解析器
        leads = ExcelParser.parse_friend_list(str(save_path))
        if not leads:
            return err(40000, '文件为空或未能识别到正确的客户名单表头')

        # 写入数据库
        db = WeChatDBManager()
        success_count = db.bulk_import_leads(leads)

        return ok({
            'message': f'成功解析并洗入 {success_count} 条待激活拓客名单',
            'inserted_count': success_count,
            'total_parsed': len(leads)
        })
    except Exception as e:
        return err(40000, str(e))


@router.get('/download/{file_id}')
async def download_file(file_id: str, is_absolute: Optional[int] = None):
    """下载文件"""
    if is_absolute == 1:
        file_path = Path(file_id)
    else:
        file_path = UPLOAD_DIR / file_id
        
        # 兜底：如果默认上传目录没有该文件，搜索系统临时物料目录和用户物料目录
        if not file_path.exists():
            import tempfile
            temp_path = Path(tempfile.gettempdir()) / 'xm_bot4_materials' / file_id
            if temp_path.exists():
                file_path = temp_path
                
        if not file_path.exists():
            home_path = Path.home() / '.xm-ai-bot' / 'materials' / file_id
            if home_path.exists():
                file_path = home_path
                
        # 兜底二：如果在上传目录和临时目录均没找到该文件，说明可能已被物理删除（如60s垃圾清理机制）。
        # 我们在用户所有的行业配置物料中通过文件名检索，顺藤摸瓜找到原本的 OSS 相对路径或物理绝对路径并拉回
        if not file_path.exists():
            try:
                from src.crm.industry_config.manager import IndustryConfigManager
                icm = IndustryConfigManager(account_id="main")
                profiles = icm.get_all_profiles()
                
                matched_material_url = None
                for prof in profiles:
                    materials = getattr(prof, 'materials', []) or []
                    for mat in materials:
                        if not mat:
                            continue
                        import os
                        filename = os.path.basename(mat.replace('\\', '/'))
                        if filename == file_id:
                            matched_material_url = mat
                            break
                    if matched_material_url:
                        break
                
                if matched_material_url:
                    if os.path.exists(matched_material_url):
                        file_path = Path(matched_material_url)
                    else:
                        from src.utils.material_utils import resolve_and_download_material
                        downloaded_path = resolve_and_download_material(matched_material_url)
                        if downloaded_path and os.path.exists(downloaded_path):
                            file_path = Path(downloaded_path)
            except Exception:
                pass
                
    if not file_path.exists():
        return err(40400, '文件不存在')
    return FileResponse(str(file_path))


@router.post('/send')
async def send_file(request: Request):
    """发送文件给微信好友"""
    data = await request.json()
    target = data.get('target', '')
    file_path = data.get('filePath', '')

    if not target or not file_path:
        return err(40000, '目标和文件路径不能为空')

    if not os.path.exists(file_path):
        return err(40000, '文件不存在')

    # TODO: 实现通过 UIA 驱动发送文件
    return err(50000, '文件发送功能开发中')


@router.get('/list')
async def list_files():
    """列出已上传的文件"""
    try:
        files = []
        for f in UPLOAD_DIR.iterdir():
            if f.is_file():
                files.append({
                    'name': f.name,
                    'size': f.stat().st_size,
                    'modified': f.stat().st_mtime,
                })
        return ok(files)
    except Exception as e:
        return err(40000, str(e))


@router.delete('/delete/{file_id}')
async def delete_file(file_id: str):
    """删除文件"""
    file_path = UPLOAD_DIR / file_id
    if not file_path.exists():
        return err(40400, '文件不存在')
    try:
        file_path.unlink()
        return ok_msg('删除成功')
    except Exception as e:
        return err(40000, str(e))


@router.post('/save-export')
async def save_export(request: Request):
    """(Desktop Only) 将前端生成的导出数据保存到本地下载目录"""
    try:
        import base64
        from urllib.parse import unquote
        
        data = await request.json()
        filename = data.get('filename', 'export.csv')
        content = data.get('content', '')
        is_base64 = data.get('is_base64', False)
        is_binary = data.get('is_binary', False)
        
        if is_binary:
            binary_content = base64.b64decode(content)
            downloads_path = Path.home() / 'Downloads'
            downloads_path.mkdir(parents=True, exist_ok=True)
            save_path = downloads_path / filename
            counter = 1
            while save_path.exists():
                name_parts = os.path.splitext(filename)
                save_path = downloads_path / f"{name_parts[0]}_{counter}{name_parts[1]}"
                counter += 1
            with open(save_path, 'wb') as f:
                f.write(binary_content)
            return ok({
                'path': str(save_path),
                'filename': save_path.name
            })
            
        if is_base64:
            # 前端使用了 btoa(unescape(encodeURIComponent(s))) 编码
            filename = unquote(base64.b64decode(filename).decode('utf-8'))
            content = unquote(base64.b64decode(content).decode('utf-8'))
        
        # 默认保存到系统下载目录
        downloads_path = Path.home() / 'Downloads'
        downloads_path.mkdir(parents=True, exist_ok=True)
        
        save_path = downloads_path / filename
        # 处理重名
        counter = 1
        while save_path.exists():
            name_parts = os.path.splitext(filename)
            save_path = downloads_path / f"{name_parts[0]}_{counter}{name_parts[1]}"
            counter += 1
            
        with open(save_path, 'w', encoding='utf-8-sig') as f:
            f.write(content)
            
        return ok({
            'path': str(save_path),
            'filename': save_path.name
        })
    except Exception as e:
        return err(40000, str(e))


@router.post('/open-shell')
async def open_shell(request: Request):
    """(Desktop Only) 调用系统外壳打开文件或文件夹"""
    try:
        data = await request.json()
        path = data.get('path', '')
        action = data.get('action', 'open') # 'open' or 'folder'
        
        if not path or not os.path.exists(path):
            return err(40400, '路径不存在')
            
        target_path = path if action == 'open' else os.path.dirname(path)
        
        # Windows 环境下直接使用 os.startfile
        if os.name == 'nt':
            os.startfile(target_path)
        else:
            # macOS / Linux 兼容处理
            import subprocess
            import platform
            cmd = 'open' if platform.system() == 'Darwin' else 'xdg-open'
            subprocess.call([cmd, target_path])
            
        return ok_msg('已调用系统外壳')
    except Exception as e:
        return err(40000, str(e))


