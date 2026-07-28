# -*- mode: python ; coding: utf-8 -*-
"""
xm-bot4 AI Bot — PyInstaller 打包配置
生成单目录 EXE（onedir），方便后续做安装包
"""

import os
import sys
from pathlib import Path

# 含 main.py / src 的目录（正常为 backend-python；隔离构建时为 backend-python/_build_tmp）
_SPEC_DIR = Path(SPEC).resolve().parent
# 真实的 backend-python 目录（含 .venv），与 build_protected 的暂存约定一致
if _SPEC_DIR.parent.name == "_build_tmp":
    BACKEND_PYTHON_ROOT = _SPEC_DIR.parent.parent
elif _SPEC_DIR.name == "_build_tmp":
    BACKEND_PYTHON_ROOT = _SPEC_DIR.parent
else:
    BACKEND_PYTHON_ROOT = _SPEC_DIR
PROJECT_ROOT = str(_SPEC_DIR)

# 前端静态资源（xm-bot4/frontend/dist，相对 backend-python 为 ../frontend/dist）
frontend_dist = os.path.normpath(
    os.path.join(str(BACKEND_PYTHON_ROOT), '..', 'frontend', 'dist')
)

def collect_safe_data_files():
    safe_datas = []
    data_dir = os.path.join(PROJECT_ROOT, 'data')
    if os.path.exists(data_dir):
        # 敏感运行时数据及缓存目录黑名单，打包发布时绝对不包含，防止开发者本地测试数据泄漏
        exclude_dirs = {'accounts', 'avatars', 'chat_history', 'screen_records', 'backups'}
        exclude_extensions = {'.db', '.db-shm', '.db-wal', '.log'}
        
        for root, dirs, files in os.walk(data_dir):
            dirs[:] = [d for d in dirs if d not in exclude_dirs]
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in exclude_extensions:
                    continue
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, PROJECT_ROOT)
                safe_datas.append((file_path, os.path.dirname(rel_path)))
    return safe_datas

datas = [
    # Logo 资源
    (os.path.join(PROJECT_ROOT, 'assets', 'logo.ico'), 'assets'),
    (os.path.join(PROJECT_ROOT, 'assets', 'logo.png'), 'assets'),
    # 微信数据库解密 DLL (实现高效率 WCDB 数据读取，无损自动抓取加载)
    (os.path.join(PROJECT_ROOT, 'assets', 'sqlite3_secure.dll'), 'assets'),
    # 诊断脚本（用户双击 exe 无反应时可运行此脚本自助排查）
    (os.path.join(str(BACKEND_PYTHON_ROOT), 'scripts', 'diagnose.bat'), '.'),
    # 启动屏 HTML 模板
    (os.path.join(PROJECT_ROOT, 'app', 'bootstrap', 'loading.html'), os.path.join('app', 'bootstrap')),
]
datas += collect_safe_data_files()

# ============== 收集新依赖的资源文件 (OCR 与验证码识别) ==============
try:
    from PyInstaller.utils.hooks import collect_data_files
    datas += collect_data_files('rapidocr_onnxruntime')
    print(f"[spec] 成功收集 rapidocr_onnxruntime 数据文件")
except Exception as _e:
    print(f"[spec] 收集 rapidocr_onnxruntime 数据文件失败: {_e}")

# 前端产物
if os.path.exists(frontend_dist):
    index_html = os.path.join(frontend_dist, 'index.html')
    if not os.path.exists(index_html):
        raise RuntimeError(
            f"❌ 前端构建产物不完整！目录存在但未找到 index.html: {frontend_dist}\\n"
            f"   这会造成启动后提示“界面资源未就绪”。请先运行 pnpm build 确保前端构建完全就绪再进行打包。"
        )
    datas.append((frontend_dist, os.path.join('frontend', 'dist')))


# 启动屏读取应用版本（app.paths.xm_bot4_splash_app_version，与前端 package.json 一致）
_frontend_package_json = os.path.normpath(
    os.path.join(str(BACKEND_PYTHON_ROOT), '..', 'frontend', 'package.json')
)
if os.path.isfile(_frontend_package_json):
    datas.append((_frontend_package_json, 'assets'))

# ============== 隐藏导入 ==============
# PyInstaller 静态分析无法发现的动态导入
hiddenimports = [
    # FastAPI / Uvicorn 核心
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    'uvicorn.lifespan.off',
    'uvicorn',
    'uvloop',
    'httptools',
    'websockets',
    'fastapi',
    'starlette',
    'starlette.routing',
    'starlette.responses',
    'starlette.middleware',
    'starlette.middleware.cors',
    'anyio._backends._asyncio',

    # UIA / pywinauto 自动化
    'uiautomation',
    'src.uia.modules.edit_helper_verify',
    'pywinauto',
    'pywinauto.controls',
    'pywinauto.controls.uiawrapper',
    'comtypes',
    'comtypes.stream',

    # AI 服务
    'httpx',
    'httpx._transports',
    'httpx._transports.default',
    'httpcore',
    'httpcore._async',
    'httpcore._sync',
    'h11',
    'h2',
    'hpack',
    'certifi',
    'sniffio',
    'anyio',

    # 调度器
    'apscheduler',
    'apscheduler.triggers.interval',
    'apscheduler.triggers.cron',
    'apscheduler.schedulers.asyncio',
    'apscheduler.schedulers.background',
    'apscheduler.jobstores.memory',
    'apscheduler.executors.pool',
    'apscheduler.jobstores.sqlalchemy',

    # 系统托盘
    'pystray',
    'pystray._win32',

    # pywebview
    'webview',
    'webview.platforms.edgechromium',
    'webview.platforms.winforms',
    'clr_loader',
    'pythonnet',

    # 图像处理 / 截图
    'PIL',
    'PIL.Image',
    'PIL.ImageGrab',
    'mss',
    'mss.windows',
    'numpy',
    'rapidocr_onnxruntime',
    'onnxruntime',

    # 剪贴板
    'pyperclip',

    # Database (Supabase)
    'supabase',

    # Excel Parsing
    'openpyxl',
    'xlrd',

    # HTTP / Network
    'requests',
    'jwt',  # PyJWT
    'multipart',  # python-multipart
    'keyboard',
    'psutil',
    'psutil._psutil_windows',

    # 标准库补全
    'sqlite3',
    'json',
    'uuid',
    'email',
    'email.mime',
    'email.mime.multipart',
    'email.mime.text',
    # html 标准库子模块（PyInstaller 不自动收集，knowledge_file_api 用到）
    'html',
    'html.parser',
    'html.entities',

    # ── 文件解析库（knowledge_file_api.py 动态 import）──────────────
    # python-docx (.docx 解析)
    'docx',
    'docx.oxml',
    'docx.oxml.ns',
    'docx.opc',
    'docx.opc.constants',
    'docx.parts',
    'docx.parts.document',
    'docx.shared',
    'docx.text',
    'docx.text.paragraph',
    'docx.table',
    # pdfplumber + 其依赖 pdfminer.six (.pdf 解析首选)
    # 模块列表以 .venv 中实际存在的子模块为准（pdfminer.pdfresource 已在新版移除）
    'pdfplumber',
    'pdfminer',
    'pdfminer.high_level',
    'pdfminer.layout',
    'pdfminer.converter',
    'pdfminer.pdfinterp',
    'pdfminer.pdfdevice',
    'pdfminer.pdfparser',
    'pdfminer.pdfdocument',
    'pdfminer.pdfpage',
    'pdfminer.pdftypes',
    'pdfminer.pdfcolor',
    'pdfminer.psparser',
    'pdfminer.utils',
    'pdfminer.encodingdb',
    'pdfminer.fontmetrics',
    'pdfminer.cmapdb',
    'pdfminer.image',
    'pdfminer.jbig2',
    'pdfminer.lzw',
    'pdfminer.runlength',
    'pdfminer.arcfour',
    'pdfminer.ascii85',
    'pdfminer.ccitt',
    'pdfminer.glyphlist',
    'pdfminer.latin_enc',
    'pdfminer.settings',
    'pdfminer.data_structures',
    'pdfminer._saslprep',
    # python-pptx (.pptx 解析)
    'pptx',
    'pptx.util',
    'pptx.oxml',
    'pptx.oxml.ns',
    'pptx.parts',
    'pptx.shapes',
    'pptx.shapes.base',
    'pptx.shapes.placeholder',
    'pptx.shapes.shapetree',
    'pptx.text',
    'pptx.text.text',
    'pptx.dml.color',
    'pptx.enum.text',

    # ── 安全 / 认证 ─────────────────────────────────────────────────
    '_cffi_backend',
    'cryptography',
    'cryptography.hazmat',
    'cryptography.hazmat.primitives',
    'cryptography.hazmat.primitives.ciphers',
    'cryptography.hazmat.backends',
    'cryptography.fernet',

    # ── 环境变量 ─────────────────────────────────────────────────────
    'dotenv',

    # ── HTTP / 异步 ──────────────────────────────────────────────────
    'aiohttp',
    'aiohttp.connector',
    'aiohttp.client',
    'multipart',

    # ── 数据库 ORM ───────────────────────────────────────────────────
    'sqlalchemy',
    'sqlalchemy.orm',
    'sqlalchemy.ext',
    'sqlalchemy.ext.declarative',
    'sqlalchemy.dialects',
    'sqlalchemy.dialects.sqlite',
    'greenlet',

    # win32 API
    'win32api',
    'win32con',
    'win32gui',
    'win32process',
    'ctypes',
    'ctypes.wintypes',

    # 语音合成 (pyttsx3)
    'pyttsx3',
    'pyttsx3.drivers',
    'pyttsx3.drivers.sapi5',

    # App 组装层与项目 src 层的模块通过自动扫描加载，防止被 Cython 编译后 PyInstaller 无法静态分析
    'qrcode',
    'silent_narrator',
]

def discover_monorepo_python_packages():
    discovered = []
    packages_python_dir = os.path.normpath(
        os.path.join(str(BACKEND_PYTHON_ROOT), '..', '..', '..', 'packages', 'python')
    )
    if os.path.isdir(packages_python_dir):
        for pkg_name in ['xm_py_server', 'xm_py_updater']:
            pkg_path = os.path.join(packages_python_dir, pkg_name)
            if os.path.isdir(pkg_path):
                discovered.append(pkg_name)
                for root, dirs, files in os.walk(pkg_path):
                    dirs[:] = [d for d in dirs if d != '__pycache__' and not d.startswith('.')]
                    rel_dir = os.path.relpath(root, packages_python_dir)
                    prefix = rel_dir.replace(os.sep, '.')
                    for file in files:
                        if file.endswith('.py'):
                            name = os.path.splitext(file)[0]
                            if name == '__init__':
                                continue
                            discovered.append(f"{prefix}.{name}")
    return sorted(list(set(discovered)))

hiddenimports += discover_monorepo_python_packages()

def auto_discover_local_modules():
    discovered = []
    for folder in ['src', 'app']:
        folder_path = os.path.join(PROJECT_ROOT, folder)
        if not os.path.exists(folder_path):
            continue
        discovered.append(folder)
        for root, dirs, files in os.walk(folder_path):
            # 排除 __pycache__、build 临时目录等非模块目录
            dirs[:] = [d for d in dirs if d != '__pycache__' and not d.startswith('.')]
            rel_dir = os.path.relpath(root, PROJECT_ROOT)
            if rel_dir != '.':
                discovered.append(rel_dir.replace(os.sep, '.'))
            for file in files:
                # 兼容未编译的 .py 和已编译为 machine code 的 .pyd
                if file.endswith('.py'):
                    if file == '__init__.py':
                        continue
                    mod_name = os.path.splitext(file)[0]
                elif file.endswith('.pyd'):
                    # 剥离可能存在的平台后缀，如 filename.cp310-win_amd64.pyd -> filename
                    mod_name = file.split('.')[0]
                else:
                    continue
                
                # 构造模块绝对引入名
                rel_dir_mod = rel_dir.replace(os.sep, '.')
                if rel_dir_mod == '.':
                    full_mod = mod_name
                else:
                    full_mod = f"{rel_dir_mod}.{mod_name}"
                discovered.append(full_mod)
    return sorted(list(set(discovered)))

hiddenimports += auto_discover_local_modules()

# ============== Cython .pyd 原生扩展自动收集 ==============
# build_protected.py 会将 src/ 中的 .py 编译为 .pyd 原生机器码
# PyInstaller 需要将这些 .pyd 作为 binaries 收集进包
import glob as _glob

cython_binaries = []
src_dir = os.path.join(PROJECT_ROOT, 'src')
for pyd_file in _glob.glob(os.path.join(src_dir, '**', '*.pyd'), recursive=True):
    # 计算 .pyd 在包内的相对目录路径
    rel_dir = os.path.relpath(os.path.dirname(pyd_file), PROJECT_ROOT)
    cython_binaries.append((pyd_file, rel_dir))
    # print(f"[spec] Cython .pyd: {os.path.relpath(pyd_file, PROJECT_ROOT)}")

if cython_binaries:
    print(f"[spec] 已收集 {len(cython_binaries)} 个 Cython 原生扩展 (.pyd)")

# uiautomation 通过 ctypes 加载的系统 DLL，由官方 hook 补充动态库列表，减少 Analysis 阶段「未找到」告警
try:
    from PyInstaller.utils.hooks import collect_dynamic_libs

    _uia_dyn_binaries = collect_dynamic_libs("uiautomation")
    if _uia_dyn_binaries:
        print(f"[spec] uiautomation 动态库: {len(_uia_dyn_binaries)} 项")
except Exception as _e:
    _uia_dyn_binaries = []
    print(f"[spec] uiautomation collect_dynamic_libs 跳过: {_e}")

# ============== PyArmor 运行时库收集 ==============
# PyArmor 加密后会生成 pyarmor_runtime_000000 运行时库
for runtime_dir_name in ['pyarmor_runtime_000000']:
    for search_root in [PROJECT_ROOT, src_dir]:
        runtime_path = os.path.join(search_root, runtime_dir_name)
        if os.path.exists(runtime_path):
            datas.append((runtime_path, runtime_dir_name))
            print(f"[spec] PyArmor runtime: {runtime_path}")
            break

main_entry = os.path.join(PROJECT_ROOT, 'main.py')
packages_python_dir = os.path.normpath(
    os.path.join(str(BACKEND_PYTHON_ROOT), '..', '..', '..', 'packages', 'python')
)
analysis_pathex = [PROJECT_ROOT, packages_python_dir]

# 强制静绑定 uiautomation 的底层 C++ UIAutomation 库（.venv 始终在 backend-python 下）
_venv_sp = BACKEND_PYTHON_ROOT / '.venv' / 'Lib' / 'site-packages'
if not _venv_sp.is_dir():
    _cands = list((BACKEND_PYTHON_ROOT / '.venv').glob('lib/python*/site-packages'))
    _venv_sp = _cands[0] if _cands else _venv_sp
venv_site_packages = str(_venv_sp)
uia_bin_dir = os.path.join(venv_site_packages, 'uiautomation', 'bin')

uia_binaries = []
if os.path.exists(uia_bin_dir):
    # Add uia_bin_dir to PATH so PyInstaller's ctypes analyzer can find the DLLs and suppress the warnings
    os.environ['PATH'] = uia_bin_dir + os.pathsep + os.environ.get('PATH', '')
    for dll_name in ['UIAutomationClient_VC140_X64.dll', 'UIAutomationClient_VC140_X86.dll']:
        dll_path = os.path.join(uia_bin_dir, dll_name)
        if os.path.exists(dll_path):
            uia_binaries.append((dll_path, os.path.join('uiautomation', 'bin')))

# ============== 收集 VC++ 运行时基础 DLL（彻底解决 chicken-and-egg 问题，防止在没有 VC++ 的干净机器上 LoadLibrary 失败） ==============
vc_binaries = []
sys32_dir = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
for dll_name in ["vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll"]:
    dll_path = os.path.join(sys32_dir, dll_name)
    if os.path.exists(dll_path):
        vc_binaries.append((dll_path, '.'))

# ============== 收集 WCDB 双引擎 DLL ==============
# wx_key.dll   : 密钥提取器（注入微信进程读取内存中的 AES key）
# wcdb_api.dll : WCDB 数据库读取 API 封装层
# WCDB.dll     : 腾讯官方 WCDB SQLite 加密引擎（wcdb_api 依赖）
# SDL2.dll     : 渲染依赖（部分版本 WCDB 需要）
# vc 运行时    : msvcp140 等，防止干净机器缺少 VC++ 运行时导致 DLL 加载失败
_wcdb_resources = os.path.normpath(
    os.path.join(str(BACKEND_PYTHON_ROOT), 'assets')
)
wcdb_binaries = []
if os.path.isdir(_wcdb_resources):
    _wcdb_dll_names = [
        'wcdb_api.dll',
        'sqlite3_secure.dll',
        'WCDB.dll',
        'SDL2.dll',
        'msvcp140.dll',
        'msvcp140_1.dll',
        'vcruntime140.dll',
        'vcruntime140_1.dll',
    ]
    for _dll in _wcdb_dll_names:
        _dll_path = os.path.join(_wcdb_resources, _dll)
        if os.path.exists(_dll_path):
            # 打包到 EXE 同级根目录，ctypes.CDLL('xxx.dll') 即可直接找到
            wcdb_binaries.append((_dll_path, '.'))
    if wcdb_binaries:
        print(f"[spec] WCDB 双引擎 DLL: {len(wcdb_binaries)} 个 → {_wcdb_resources}")
    else:
        print(f"[spec] ⚠️  未找到 WCDB DLL，双引擎在打包产物中将不可用。路径: {_wcdb_resources}")
else:
    print(f"[spec] ⚠️  assets 目录不存在，跳过 WCDB DLL 收集: {_wcdb_resources}")

a = Analysis(
    [main_entry],
    pathex=analysis_pathex,
    binaries=cython_binaries + _uia_dyn_binaries + uia_binaries + vc_binaries + wcdb_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'ddddocr',
        'tkinter',
        'unittest',
        'pydoc',
        'pydoc_data',
        'idlelib',
        'test',
        'lib2to3',
        'matplotlib',
        'scipy',
        'pandas',
        'notebook',
        'IPython',
        'pycparser.lextab',
        'pycparser.yacctab',
        'pysqlite2',
        'MySQLdb',
        'psycopg2',
        'brotli',
        'brotlicffi',
        'brotlipy',
    ],
    noarchive=False,
)

# 过滤掉不需要的视频编解码组件，减少约 25MB 体积
a.binaries = [x for x in a.binaries if "opencv_videoio_ffmpeg" not in x[0]]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='xm-bot4',
    icon=os.path.join(PROJECT_ROOT, 'assets', 'logo.ico'),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # 关闭启动时的 CMD 窗口，使用纯桌面 GUI 窗口模式
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=None,
    uac_admin=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='xm-bot4',
)
