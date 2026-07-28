import uuid
import logging
from datetime import datetime
from fastapi import APIRouter, Request

from src.utils.db_manager import WeChatDBManager
from src.utils.response import ok, err
from src.utils.code_security_scanner import scan_script_security
from src.crm.account_data import get_active_account

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/fulfillment/market", tags=["plugin-market"])


@router.get("/plugins")
async def list_plugins(scope: str = "all"):
    """
    拉取插件市场列表。
    scope: 'all'（全部）, 'my_published'（我发布的）, 'my_purchased'（我已购买的）
    """
    db = WeChatDBManager()
    all_plugins = db.get_market_plugins()
    current_user = get_active_account() or "default_user"

    # 预设几个内置插件供体验
    if not all_plugins:
        all_plugins = _get_default_seed_plugins()
        for p in all_plugins:
            db.add_market_plugin(p)

    if scope == "my_published":
        res = [p for p in all_plugins if p.get("author_wxid") == current_user]
    elif scope == "my_purchased":
        purchased_ids = {p.get("plugin_id") for p in db.get_plugin_purchases() if p.get("buyer_wxid") == current_user}
        res = [p for p in all_plugins if p.get("id") in purchased_ids]
    else:
        res = all_plugins

    # 附带是否已购的标识
    purchased_ids = {p.get("plugin_id") for p in db.get_plugin_purchases() if p.get("buyer_wxid") == current_user}
    for item in res:
        item["purchased"] = item.get("id") in purchased_ids or item.get("price", 0) == 0 or item.get("author_wxid") == current_user

    return ok(res)


@router.post("/plugins")
async def publish_plugin(request: Request):
    """发布新插件（包含 AST 静态安全审查）"""
    data = await request.json()
    key = data.get("key", "").strip()
    name = data.get("name", "").strip()
    description = data.get("description", "").strip()
    price = float(data.get("price", 0))
    intent_keywords = data.get("intent_keywords", "").strip()
    cmd_template = data.get("cmd_template", "").strip()
    code_content = data.get("code_content", "").strip()

    if not key or not name or not cmd_template:
        return err(40000, "参数 Key、名称和命令行模板不能为空")

    # 核心安全屏障：AST 静态代码安全检测
    is_safe, error_desc = scan_script_security(code_content, cmd_template)
    if not is_safe:
        logger.warning(f"[插件安全校验拦截] 拒绝发布插件 '{name}': {error_desc}")
        return err(40001, f"安全校验拦截: {error_desc}")

    db = WeChatDBManager()
    current_user = get_active_account() or "default_user"

    plugin_item = {
        "id": f"plg_{uuid.uuid4().hex[:8]}",
        "key": key,
        "name": name,
        "description": description,
        "author_wxid": current_user,
        "author_name": f"开发者_{current_user[:6]}",
        "price": price,
        "intent_keywords": intent_keywords,
        "cmd_template": cmd_template,
        "code_content": code_content,
        "status": "approved",
        "created_at": datetime.now().isoformat()
    }

    db.add_market_plugin(plugin_item)
    return ok(plugin_item)


@router.post("/plugins/{plugin_id}/purchase")
async def purchase_plugin(plugin_id: str, request: Request):
    """购买插件"""
    db = WeChatDBManager()
    plugins = db.get_market_plugins()
    plugin = next((p for p in plugins if p.get("id") == plugin_id), None)
    if not plugin:
        return err(40400, "目标插件不存在")

    current_user = get_active_account() or "default_user"
    sku = f"sku_bot4_plg_{plugin_id}"

    purchase_record = {
        "id": f"pch_{uuid.uuid4().hex[:8]}",
        "plugin_id": plugin_id,
        "buyer_wxid": current_user,
        "sku": sku,
        "amount_paid": plugin.get("price", 0.0),
        "purchased_at": datetime.now().isoformat()
    }

    db.add_plugin_purchase(purchase_record)
    return ok(purchase_record)


@router.post("/withdraw")
async def request_withdrawal(request: Request):
    """申请提现"""
    data = await request.json()
    amount = float(data.get("amount", 0))
    if amount <= 0:
        return err(40000, "提现金额必须大于0")

    db = WeChatDBManager()
    current_user = get_active_account() or "default_user"

    # 计算提现手续费（15%）
    service_fee = round(amount * 0.15, 2)

    withdrawal = {
        "id": f"wth_{uuid.uuid4().hex[:8]}",
        "developer_wxid": current_user,
        "amount": amount,
        "service_fee": service_fee,
        "status": "pending",
        "created_at": datetime.now().isoformat()
    }

    db.add_withdrawal_record(withdrawal)
    return ok(withdrawal)


@router.get("/withdrawals")
async def list_withdrawals():
    """获取我的提现记录"""
    db = WeChatDBManager()
    records = db.get_withdrawal_records()
    current_user = get_active_account() or "default_user"
    res = [r for r in records if r.get("developer_wxid") == current_user]
    return ok(res)


def _get_default_seed_plugins():
    return [
        {
            "id": "plg_seed_live_record",
            "key": "send_live_record",
            "name": "实时电脑演示录屏",
            "description": "当被咨询系统演示时，自动在被控端录像，或兜底发送演示视频给客户",
            "author_wxid": "system_root",
            "author_name": "官方团队",
            "price": 0.0,
            "safety_level": 1,
            "intent_keywords": "演示视频,系统操作演示,演示效果",
            "cmd_template": "python {plugin_dir}/live_record.py --fallback={fallback_video}",
            "code_content": "import os\nprint('启动实时演示录屏逻辑...')",
            "status": "approved",
            "created_at": "2026-06-07T08:10:00",
            "config": {
                "fallback_video": "assets/default_demo.mp4"
            }
        },
        {
            "id": "plg_seed_materials",
            "key": "send_materials",
            "name": "沙箱物料宣传册",
            "description": "自动查找被控端物料存储目录，并通过微信给客户发送对应的产品白皮书与资料",
            "author_wxid": "system_root",
            "author_name": "官方团队",
            "price": 0.0,
            "safety_level": 2,
            "intent_keywords": "发送资料,产品手册,物料手册",
            "cmd_template": "python {plugin_dir}/send_materials.py --dir={sandbox_dir}",
            "code_content": "import os\nprint('启动自动发送宣传册逻辑...')",
            "status": "approved",
            "created_at": "2026-06-07T08:12:00",
            "config": {
                "sandbox_dir": "D:\\materials"
            }
        },
        {
            "id": "plg_seed_clean",
            "key": "sys_cleaner",
            "name": "Windows 系统垃圾清理插件",
            "description": "安全清理系统缓存文件夹，释放硬盘空间，定期静默运行",
            "author_wxid": "system_root",
            "author_name": "官方团队",
            "price": 0.0,
            "safety_level": 1,
            "intent_keywords": "清理系统,清理垃圾,清除缓存",
            "cmd_template": "cleanmgr /sagerun:1",
            "code_content": "# 这是一个内置的批处理脚本指令",
            "status": "approved",
            "created_at": "2026-06-07T08:00:00"
        },
        {
            "id": "plg_seed_backup",
            "key": "backup_docs",
            "name": "物理文档自动异地备份插件",
            "description": "自动对桌面或文档目录进行特定后缀过滤，并在后台进行压缩同步备份",
            "author_wxid": "system_root",
            "author_name": "官方团队",
            "price": 9.9,
            "safety_level": 3,
            "intent_keywords": "备份文档,资料归档,重要文件备份",
            "cmd_template": "python {plugin_dir}/backup.py --dest=D:\\Backup",
            "code_content": "import os\nimport shutil\nprint('开始备份物理文档，已确认路径安全')\n# shutil.copy2('src', 'dest')",
            "status": "approved",
            "created_at": "2026-06-07T08:05:00"
        }
    ]
