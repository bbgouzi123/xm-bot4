"""Ports, origins, and cross-service proxy map (single source of truth)."""

from xm_py_server.runtime_urls import LOOPBACK_HOST, http_origin, prod_gateway_url

# xm-bot4 Python 后端与本地 Vite 端口（与 docs/registry/ports.md 一致）
BOT4_PORT = 42041
BOT4_VITE_PORT = 40041
BOT4_LOCAL_ORIGIN = http_origin(LOOPBACK_HOST, BOT4_PORT)
VITE_DEV_ORIGIN = http_origin(LOOPBACK_HOST, BOT4_VITE_PORT)
# 前端构建 base 路径前缀（与 vite.config.ts 中的 base: '/xm-bot4/' 保持一致）
# Python 跳转必须使用此完整路径，否则 SolidJS Router base='/xm-bot4' 无法匹配根路由导致空白渲染
BOT4_FRONTEND_ENTRY = BOT4_LOCAL_ORIGIN + "/xm-bot4/"

# 与 packages/frontend/crypto/src/anti-debug.ts 中 _SECRET 保持一致（用于 ?tk= 解锁前端 F12/快捷键拦截）
ANTI_DEBUG_BYPASS_TK = "YyM1jXxmD6fWTERCccPhCk0"
# 由 build_protected.py 在「deploy … --f12」打包前置为 True，使生产 EXE 双击即可开 DevTools（无需命令行）
XM_PACKAGED_WITH_F12 = False

# 跨服务路由前缀注册表（与 Vite BACKEND_SERVICES 保持一致）
CROSS_SERVICE_MAP: dict[str, dict[str, str]] = {
    "/api/xm-user": {"local": http_origin(LOOPBACK_HOST, 42001), "prod": prod_gateway_url("/api/xm-user")},
    "/api/xm-store": {"local": http_origin(LOOPBACK_HOST, 42003), "prod": prod_gateway_url("/api/xm-store")},
    "/api/xm-bot4-cloud": {"local": http_origin(LOOPBACK_HOST, 42040), "prod": prod_gateway_url("/api/xm-bot4-cloud")},
    "/api/xm-dragonscale": {"local": http_origin(LOOPBACK_HOST, 42046), "prod": prod_gateway_url("/api/xm-dragonscale")},
    "/api/xm-oss": {"local": http_origin(LOOPBACK_HOST, 42042), "prod": prod_gateway_url("/api/xm-oss")},
    "/api/xm-sentinel": {"local": http_origin(LOOPBACK_HOST, 42063), "prod": prod_gateway_url("/api/xm-sentinel")},
    "/api/xm-mashangchaqi": {"local": http_origin(LOOPBACK_HOST, 42032), "prod": prod_gateway_url("/api/xm-mashangchaqi")},
    "/api/xm-regionhub": {"local": http_origin(LOOPBACK_HOST, 42067), "prod": prod_gateway_url("/api/xm-regionhub")},
}

CROSS_SERVICE_PREFIXES: list[str] = list(CROSS_SERVICE_MAP.keys())
