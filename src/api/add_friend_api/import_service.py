import os
import uuid
import logging
from pathlib import Path
from typing import List, Any
from fastapi import APIRouter, UploadFile, File, Request

from src.friend import excel_parser
from src.friend import friend_queue
from src.utils.response import ok, err
from .models import ImportRequest, RemapRequest, ManualImportRequest

logger = logging.getLogger(__name__)
router = APIRouter()

# 上传临时目录
UPLOAD_DIR = Path.home() / ".xm-ai-bot" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

@router.post("/upload")
async def upload_excel(file: UploadFile = File(...)):
    """上传 Excel/CSV 文件并返回解析预览"""
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".xls", ".xlsx", ".csv"):
        return err(40000, f"不支持的文件格式 {ext}，请上传 .xls / .xlsx / .csv")

    file_id = uuid.uuid4().hex[:12]
    temp_path = UPLOAD_DIR / f"{file_id}{ext}"

    try:
        content = await file.read()
        temp_path.write_bytes(content)
    except Exception as e:
        return err(40000, f"文件保存失败: {e}")

    try:
        result = excel_parser.parse_excel(str(temp_path))
    except Exception as e:
        return err(40000, f"文件解析失败: {e}")

    if not result.get("success"):
        return result

    has_phone = "phone" in result["field_mapping"] or "phone_verified" in result["field_mapping"]
    has_company = "company_name" in result["field_mapping"]
    mapped_count = len(result["field_mapping"])
    confidence = "high" if (has_phone and mapped_count >= 3) else "medium" if has_phone else "low"
    
    return ok({
        "file_id": file_id,
        "filename": file.filename,
        "total_rows": result["total_rows"],
        "headers": result["headers"],
        "field_mapping": result["field_mapping"],
        "sample_data": result["sample_data"],
        "stats": result["stats"],
        "mapping_confidence": confidence,
    })

@router.get("/field-options")
async def get_field_options():
    """返回所有可映射的字段列表"""
    options = [
        {"value": "_ignore", "label": "忽略此列", "group": "操作"},
        {"value": "company_name", "label": "企业/客户名称", "group": "基础"},
        {"value": "phone", "label": "手机号码", "group": "联系方式"},
        {"value": "phone_alt", "label": "备用手机", "group": "联系方式"},
        {"value": "phone_verified", "label": "已验证手机号", "group": "联系方式"},
        {"value": "wechat_id", "label": "微信号", "group": "联系方式"},
        {"value": "landline", "label": "固定电话", "group": "联系方式"},
        {"value": "email", "label": "邮箱", "group": "联系方式"},
        {"value": "legal_person", "label": "联系人/法人", "group": "基础"},
    ]
    return ok({"options": options})

@router.post("/remap")
async def remap_with_user_mapping(req: RemapRequest):
    """用户手动标注列映射后重新解析"""
    file_id = req.file_id
    mapping = req.mapping
    temp_files = list(UPLOAD_DIR.glob(f"{file_id}.*"))
    if not temp_files:
        return err(40000, "临时文件已过期，请重新上传")

    temp_path = temp_files[0]
    try:
        result = excel_parser.reparse_with_mapping(str(temp_path), mapping)
    except Exception as e:
        return err(40000, f"重新解析失败: {e}")

    if not result.get("success"):
        return result

    return ok({
        "file_id": file_id,
        "total_rows": result["total_rows"],
        "headers": result["headers"],
        "field_mapping": result["field_mapping"],
        "sample_data": result["sample_data"],
        "stats": result["stats"],
        "mapping_confidence": "high",
        "learned": True,
    })

@router.post("/import-excel")
async def standard_import_excel(request: Request, files: List[UploadFile] = File(...)):
    """符合 @xm/import 规范的标准导入接口"""
    dry_run = request.query_params.get("dry_run") == "true"
    total_rows = 0
    all_contacts = []
    preview_headers = []
    preview_rows = []
    files_processed = []
    errors = []

    # 安全兜底：某些 FastAPI 版本/客户端上传单文件时会传入 UploadFile 对象而非列表
    if isinstance(files, UploadFile):
        files = [files]

    for file in files:
        ext = os.path.splitext(file.filename or "")[1].lower()
        if ext not in (".xls", ".xlsx", ".csv", ".zip"):
            errors.append(f"{file.filename}: 不支持的文件格式")
            continue

        file_id = uuid.uuid4().hex[:12]
        temp_path = UPLOAD_DIR / f"{file_id}{ext}"
        try:
            content = await file.read()
            temp_path.write_bytes(content)
            result = excel_parser.parse_excel(str(temp_path))
            if not result.get("success"):
                errors.append(f"{file.filename}: 解析失败 {result.get('error', '')}")
                continue
            
            total_rows += result["total_rows"]
            all_contacts.extend(result.get("contacts", []))
            files_processed.append(file.filename)
            
            if dry_run:
                # 💡 如果是多文件预览：表头统一以第一个文件的表头为准，并在最前面添加“数据来源文件”列
                if not preview_headers:
                    preview_headers = ["数据来源文件"] + result["headers"]
                
                # 💡 将此文件的每一行前面都挂上对应的文件名，然后合并到全量预览中！
                fn = file.filename or "未知文件"
                for row in result.get("raw_sample", []):
                    preview_rows.append([fn] + list(row))
        except Exception as e:
            errors.append(f"{file.filename}: {str(e)}")

    if dry_run:
        return ok({
            "rows_read": total_rows,
            "preview_headers": preview_headers,
            "preview_rows": preview_rows,
            "files_processed": files_processed,
            "errors": errors
        })

    if not all_contacts:
        return err(40000, "没有提取到可导入的有效数据")

    import_batch_id = uuid.uuid4().hex[:12]
    industry_id, industry_name = _get_active_industry()
    tags = list(set([os.path.splitext(f)[0] for f in files_processed]))

    import_result = friend_queue.import_contacts(
        all_contacts,
        source_file="批量导入",
        original_filename="批量导入.zip" if len(files_processed) > 1 else files_processed[0],
        tags=tags,
        import_batch_id=import_batch_id,
        industry_profile_id=industry_id,
        industry_profile_name=industry_name,
    )
    return ok({
        "rows_read": total_rows,
        "files_processed": files_processed,
        "errors": errors,
        "imported": import_result["imported"],
        "skipped": import_result["skipped"],
        "batch_id": import_batch_id,
        "records": import_result.get("records", []),
        "stats": import_result
    })

@router.post("/import")
async def confirm_import(req: ImportRequest):
    """确认导入到加好友队列"""
    temp_files = list(UPLOAD_DIR.glob(f"{req.file_id}.*"))
    if not temp_files:
        return err(40000, "临时文件已过期，请重新上传")

    temp_path = temp_files[0]
    try:
        result = excel_parser.parse_excel(str(temp_path))
        if not result.get("success"):
            return result
        contacts = result.get("contacts", [])
        if not contacts:
            return err(40000, "没有可导入的有效联系人")

        tags = list(req.tags)
        if req.auto_tag_filename and req.original_filename:
            auto_tag = Path(req.original_filename).stem
            if auto_tag and auto_tag not in tags:
                tags.insert(0, auto_tag)

        industry_id, industry_name = _get_active_industry()
        import_batch_id = uuid.uuid4().hex[:12]
        import_result = friend_queue.import_contacts(
            contacts,
            source_file=temp_path.name,
            original_filename=req.original_filename,
            tags=tags,
            import_batch_id=import_batch_id,
            industry_profile_id=industry_id,
            industry_profile_name=industry_name,
        )
        temp_path.unlink(missing_ok=True)
        return ok({
            "imported": import_result.get("imported", 0),
            "skipped": import_result.get("skipped", 0),
            "batch_id": import_batch_id,
            "records": import_result.get("records", []),
            "industry": industry_name,
            "tags": tags,
            "total_in_queue": friend_queue.get_queue_stats().get("pending", 0),
        })
    except Exception as e:
        return err(40000, f"导入失败: {e}")

@router.post("/import-manual")
async def import_manual(req: ManualImportRequest):
    """手动录入并确认导入到加好友队列"""
    try:
        contacts = req.contacts
        if not contacts:
            return err(40000, "没有可导入的有效联系人")

        # 整理联系人格式
        formatted_contacts = []
        for i, c in enumerate(contacts):
            phone = str(c.get("phone") or "").strip()
            wechat_id = str(c.get("wechat_id") or "").strip()
            if not phone and not wechat_id:
                continue

            cleaned_phones = []
            if phone:
                from src.friend.excel_parser import _extract_phones
                cleaned_phones = _extract_phones(phone)

            formatted_contacts.append({
                "company_name": str(c.get("company_name") or "").strip(),
                "phones": cleaned_phones,
                "primary_phone": cleaned_phones[0] if cleaned_phones else "",
                "wechat_id": wechat_id,
                "legal_person": str(c.get("legal_person") or "").strip(),
                "row_index": i + 1,
            })

        if not formatted_contacts:
            return err(40000, "没有提取到可导入的有效号码")

        tags = list(req.tags)
        if not tags:
            tags = ["手动录入"]

        industry_id, industry_name = _get_active_industry()
        import_batch_id = uuid.uuid4().hex[:12]
        import_result = friend_queue.import_contacts(
            formatted_contacts,
            source_file="手动录入",
            original_filename="手动录入",
            tags=tags,
            import_batch_id=import_batch_id,
            industry_profile_id=industry_id,
            industry_profile_name=industry_name,
        )
        return ok({
            "imported": import_result.get("imported", 0),
            "skipped": import_result.get("skipped", 0),
            "batch_id": import_batch_id,
            "records": import_result.get("records", []),
            "industry": industry_name,
            "tags": tags,
            "total_in_queue": friend_queue.get_queue_stats().get("pending", 0),
        })
    except Exception as e:
        return err(40000, f"手动录入失败: {e}")

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
