import os
import sys
import argparse
import httpx
import asyncio
import logging

# Force UTF-8 encoding for stdout and stderr on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Set up logging to stderr only, to avoid polluting stdout
logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
logger = logging.getLogger("parse_cli")

# Add backend-python to sys.path so we can import from src
script_dir = os.path.dirname(os.path.abspath(__file__))
backend_python_dir = os.path.dirname(script_dir)
sys.path.insert(0, backend_python_dir)

# Add packages/python to sys.path so we can import packages/python modules like xm_py_server
packages_python_dir = os.path.abspath(os.path.join(backend_python_dir, "..", "..", "..", "packages", "python"))
if os.path.isdir(packages_python_dir) and packages_python_dir not in sys.path:
    sys.path.insert(0, packages_python_dir)

from src.api.knowledge_file_parsers import _PARSERS, _detect_file_type

async def download_file(url: str, base_url: str) -> bytes:
    # Resolve relative URL
    if url.startswith("/"):
        # 优先在本地开发环境的文件系统中查找该文件，避免开发环境下因网络或域名限制下载失败
        workspace_root = os.path.abspath(os.path.join(backend_python_dir, "..", "..", ".."))
        local_paths = [
            os.path.join(workspace_root, "products", "xm-user", "backend", url.lstrip("/")),
            os.path.join(workspace_root, url.lstrip("/")),
            os.path.join(os.getcwd(), url.lstrip("/")),
            os.path.join(os.getcwd(), "products", "xm-user", "backend", url.lstrip("/")),
        ]
        for path in local_paths:
            if os.path.exists(path):
                try:
                    with open(path, "rb") as f:
                        return f.read()
                except Exception as ex:
                    logger.warning(f"Found local file at {path} but failed to read: {ex}")

        if base_url:
            url = base_url.rstrip("/") + url
        else:
            url = "https://xmcore.top" + url
            
    headers = {}
    
    # Try to load token for authorization if downloading from local backend
    try:
        token_file = os.path.join(
            os.environ.get("APPDATA", os.path.expanduser("~")),
            "xm-bot4", "sso_token.txt"
        )
        if os.path.exists(token_file):
            with open(token_file, "r", encoding="utf-8") as f:
                token = f.read().strip()
                if token:
                    headers["Authorization"] = f"Bearer {token}"
    except Exception as e:
        logger.warning(f"Could not load sso_token: {e}")

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=headers, timeout=30.0, follow_redirects=True)
        resp.raise_for_status()
        return resp.content

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--mime-type", required=True)
    parser.add_argument("--base-url", default="")
    args = parser.parse_args()

    file_type = _detect_file_type(args.name, args.mime_type)
    if file_type not in _PARSERS:
        print(f"ERROR: Unsupported format: {args.name}", file=sys.stderr)
        sys.exit(1)

    try:
        raw_bytes = await download_file(args.url, args.base_url)
    except Exception as e:
        print(f"ERROR: Download failed: {e}", file=sys.stderr)
        sys.exit(2)

    try:
        import inspect
        parser_func = _PARSERS[file_type]
        if inspect.iscoroutinefunction(parser_func):
            text = await parser_func(raw_bytes)
        else:
            text = parser_func(raw_bytes)
        
        cleaned = text.strip()
        print(cleaned)
    except Exception as e:
        print(f"ERROR: Parse failed: {e}", file=sys.stderr)
        sys.exit(3)

if __name__ == "__main__":
    asyncio.run(main())
