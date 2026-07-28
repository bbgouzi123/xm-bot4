"""
xm-bot4 AI Bot — 行业最强三层安全打包脚本
Cython 编译 + PyArmor 加密 + PyInstaller 打包
"""

import argparse
import sys
import os
import atexit
import traceback
from pathlib import Path

# 将 build_scripts 包路径加入模块搜索路径
sys.path.append(str(Path(__file__).resolve().parent))

from build_scripts.config import ROOT
from build_scripts.stage_clean import clean, finalize_workspace, _emergency_cleanup_build_tmp
from build_scripts.stage_staging import prepare_staging, promote_dist
from build_scripts.stage_cython import run_cython_compile
from build_scripts.stage_obfuscate import run_pyarmor_entry, inject_anti_debug
from build_scripts.stage_package import run_pyinstaller, run_inno_setup
from build_scripts.reporter import print_report

# Fix unicode error in python terminal
if sys.stdout is not None and getattr(sys.stdout, 'encoding', None) != 'utf-8' and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

def main():
    parser = argparse.ArgumentParser(description="XM AI Bot - Triple-Layer Security Build")
    parser.add_argument("--fast", action="store_true", help="Fast mode: PyInstaller only (dev/test)")
    parser.add_argument("--f12", action="store_true", help="Inject frontend anti-debug bypass token into packaged EXE (for debugging)")
    parser.add_argument("--inno", action="store_true", help="打包完成后自动调用 Inno Setup 编译 Setup.exe 安装包")
    args = parser.parse_args()

    atexit.register(_emergency_cleanup_build_tmp)

    mode = "fast" if args.fast else "default"
    exit_code = 0

    print(f"\n{'='*60}")
    print(f"  XM AI Bot - Security Build Pipeline")
    print(f"  Mode: {mode}")
    print(f"  Frontend F12 Bypass: {'[√] Enabled' if args.f12 else '[ ] Disabled'}")
    print(f"  Root: {ROOT}")
    print(f"{'='*60}")

    main_py_path = ROOT / "main.py"
    staging_main_text = (
        main_py_path.read_text("utf-8") if main_py_path.exists() else ""
    )

    success = False
    cython_ok = False
    pyarmor_ok = False
    installer_ok = False
    build_root = None
    try:
        try:
            clean()
            build_root = prepare_staging(staging_main_text, f12=bool(args.f12))
            print(f"  隔离构建目录: {build_root.resolve()}")

            if mode == "fast":
                success = run_pyinstaller(build_root)
            else:
                cython_ok = run_cython_compile(build_root)

                if not cython_ok:
                    print("\n  ❌ Cython 层未成功完成，跳过 PyArmor / PyInstaller。")
                    success = False
                else:
                    inject_anti_debug(build_root)
                    pyarmor_ok = run_pyarmor_entry(build_root)
                    require_pyarmor = os.environ.get("XM_REQUIRE_PYARMOR", "").strip().lower() in (
                        "1",
                        "true",
                        "yes",
                    )
                    if require_pyarmor and not pyarmor_ok:
                        print(
                            "\n  ❌ 已设置 XM_REQUIRE_PYARMOR，PyArmor 必须成功。"
                            " 请配置有效 PyArmor 许可证后重试。"
                        )
                        success = False
                    else:
                        success = run_pyinstaller(build_root)

            if success:
                promote_dist(build_root)
                try:
                    (ROOT / "dist" / ".xm_packaged_f12").write_text(
                        "1\n" if args.f12 else "0\n",
                        encoding="utf-8",
                    )
                except OSError:
                    pass

                if args.inno:
                    installer_ok = run_inno_setup()

            if not success:
                exit_code = 1
        except Exception as e:
            print(f"\n❌ [构建异常] 捕获到未处理的异常:")
            traceback.print_exc()
            exit_code = 1
    finally:
        finalize_workspace(build_root)

    print_report(mode, success, cython_ok, pyarmor_ok, installer_ok)
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
