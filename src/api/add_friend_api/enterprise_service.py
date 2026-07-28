from pathlib import Path
from typing import List, Dict, Any
from fastapi import APIRouter, Request

from src.friend import enterprise_import
from src.friend import friend_queue
from src.utils.response import ok, err
from .models import EnterpriseSearchRequest, EnterprisePrepareRequest, EnterpriseImportPaidRequest

router = APIRouter()
UPLOAD_DIR = Path.home() / ".xm-ai-bot" / "uploads"

@router.get("/enterprise/industries")
async def enterprise_industries(http: Request):
    """获取码上查企行业列表"""
    auth = http.headers.get("authorization")
    data = await enterprise_import.get_industries(auth_header=auth)
    return ok(data)

@router.post("/enterprise/search")
async def enterprise_search(http: Request, body: EnterpriseSearchRequest):
    """代理码上查企企业检索"""
    auth = http.headers.get("authorization")
    
    # 🩹 自愈装甲：在每次查询时，后台异步触发一次本地队列向同步后端的全量安全补偿报送
    # 利用同步后端 ON CONFLICT DO NOTHING 的幂等性，彻底治愈由于历史断网或500引发的僵尸漏网数据
    async def heal_zombie_records():
        try:
            from src.friend.friend_queue import query as fq_query
            # 取本地最新的一批数据进行补偿报送
            local_list = fq_query.get_queue_list(page_size=5000).get("items", [])
            contacts = []
            for item in local_list:
                ph = item.get("phone") or item.get("primary_phone")
                cn = item.get("company_name") or item.get("name")
                if ph and cn:
                    extra = item.get("extra_fields") or {}
                    if isinstance(extra, str):
                        try:
                            import json as _json
                            extra = _json.loads(extra)
                        except Exception:
                            extra = {}
                    ent_id = extra.get("id") if isinstance(extra, dict) else ""
                    if not ent_id:
                        ent_id = item.get("id") or ""
                    contacts.append({
                        "phone": ph,
                        "company_name": cn,
                        "enterprise_row_key": str(ent_id),
                    })
            if contacts:
                await enterprise_import.report_purchased_contacts(
                    auth_header=auth,
                    batch_id=999999,  # 使用特殊批次号代表补偿数据
                    contacts=contacts,
                )
        except Exception:
            pass
            
    import asyncio
    asyncio.create_task(heal_zombie_records())

    data = await enterprise_import.search_enterprises(
        auth_header=auth,
        q=body.q,
        region=body.region,
        industry=body.industry,
        reg_status=body.reg_status,
        page=body.page,
        page_size=body.page_size,
        hide_purchased=body.hide_purchased,
    )
    raw_list = data.get("list") or []
    lst = [enterprise_import.ui_row(x) for x in raw_list]
    result = {
        "list": lst,
        "total": data.get("total") if data.get("total") is not None else len(lst),
        "page": data.get("page", body.page),
        "page_size": data.get("page_size", body.page_size),
    }
    # 透传查企服务的业务错误信息（如认证失败、配额不足等），供前端 toast 提示
    if data.get("error"):
        result["error"] = data["error"]
    return ok(result)

@router.post("/enterprise/prepare")
async def enterprise_prepare(http: Request, body: EnterprisePrepareRequest):
    """勾选行 ID 换取完整数据并写入本地会话"""
    if not body.ids:
        return err(40000, "请先勾选要导入的企业")
    
    auth = http.headers.get("authorization")
    full_rows = await enterprise_import.get_enterprises_by_ids(ids=body.ids, auth_header=auth)
    if not full_rows:
        return err(40000, "获取企业详情失败或记录已失效")

    contacts: List[Dict[str, Any]] = []
    for i, row in enumerate(full_rows):
        c = enterprise_import.enterprise_row_to_contact(row, row_index=i)
        if c:
            contacts.append(c)
    if not contacts:
        return err(40000, "勾选的记录中没有含有效联系方式的数据")
    
    sid = enterprise_import.save_session(UPLOAD_DIR, contacts, user_id=body.user_id.strip())
    n = len(contacts)
    unit = 10
    return ok({
        "session_id": sid,
        "quantity": n,
        "unit_price_fen": unit,
        "total_fen": n * unit,
    })

@router.post("/enterprise/import-paid")
async def enterprise_import_paid(request: Request, body: EnterpriseImportPaidRequest):
    """校验订单并写入加好友队列"""
    if not body.session_id or not body.user_id:
        return err(40000, "缺少 session_id 或 user_id")
    sess = enterprise_import.load_session(UPLOAD_DIR, body.session_id)
    if not sess:
        return err(40000, "会话已过期，请重新筛选")
    contacts = sess.get("contacts") or []
    if not contacts:
        return err(40000, "会话数据为空")
    ok_pay = await enterprise_import.verify_consumable_order(
        user_id=body.user_id.strip(),
        order_id=body.consumable_order_id,
        expect_ref=body.session_id.strip(),
    )
    if not ok_pay:
        return err(40000, "支付未完成或订单与本次筛选会话不匹配")

    tags = list(body.tags)
    stem = Path(body.original_filename).stem
    if stem and stem not in tags:
        tags.insert(0, stem)

    industry_id, industry_name = _get_active_industry()
    # 【修复】直接使用前端 session_id 作为 import_batch_id，确保前端任务筛选时
    # 用来过滤的 ID（session_id）与后端入队记录的 import_batch_id 字段完全一致。
    # 原先使用 uuid.uuid4().hex[:12] 会生成全新随机 ID，导致任务启动后
    # get_pending 按 session_id 筛选时找不到任何记录，任务立即停止。
    import_batch_id = body.session_id.strip()
    import_result = friend_queue.import_contacts(
        contacts,
        source_file=f"enterprise:{body.session_id}",
        original_filename=body.original_filename,
        tags=tags,
        import_batch_id=import_batch_id,
        industry_profile_id=industry_id,
        industry_profile_name=industry_name,
    )
    _sync_cloud()
    
    # 异步报送购买凭证给码上查企中央数据库（解决后端无法过滤的关键一步）
    purchased_items = []
    for c in contacts:
        purchased_items.append({
            "phone": c.get("phone") or "",
            "company_name": c.get("company_name") or "",
            "enterprise_row_key": str(c.get("id") or ""),
        })
    await enterprise_import.report_purchased_contacts(
        auth_header=request.headers.get("authorization"),
        batch_id=body.consumable_order_id,
        contacts=purchased_items,
    )
    
    enterprise_import.delete_session(UPLOAD_DIR, body.session_id)

    return ok({
        "imported": import_result.get("imported", 0),
        "skipped": import_result.get("skipped", 0),
        "batch_id": import_batch_id,    # 与 session_id 相同，供前端 setLastEnterpriseInfo 使用
        "records": import_result.get("records", []),
        "industry": industry_name,
        "tags": tags,
        "total_in_queue": friend_queue.get_queue_stats().get("pending", 0),
    })

def _get_active_industry():
    industry_id, industry_name = "", ""
    try:
        # 1. 优先读取当前活跃微信实例绑定的专属行业配置
        active_wxid = None
        try:
            from src.utils.instance_manager import InstanceManagerV2
            manager = InstanceManagerV2.get_instance()
            active_inst_id = manager.get_active_instance_id()
            if active_inst_id and active_inst_id in manager.get_all_instances():
                active_wxid = active_inst_id
        except Exception:
            pass

        from src.crm.industry_config import IndustryConfigManager
        mgr = IndustryConfigManager(account_id="global")

        if active_wxid:
            from src.api.instance_settings_api import load_instance_settings
            try:
                cfg = load_instance_settings(active_wxid)
                profile_id = cfg.get("industry_profile_id")
                if profile_id:
                    profile = mgr.get_profile_by_id(profile_id)
                    if profile:
                        return profile.id, profile.name
            except Exception:
                pass

        # 2. 兜底获取全局默认激活行业
        profile = mgr.get_active_profile()
        if profile:
            industry_id, industry_name = profile.id, profile.name
    except Exception:
        pass
    return industry_id, industry_name

def _sync_cloud():
    try:
        import threading
        from src.utils.cloud_sync import get_cloud_client
        threading.Thread(
            target=get_cloud_client().sync_friend_queue,
            args=(),
            daemon=True
        ).start()
    except Exception:
        pass
