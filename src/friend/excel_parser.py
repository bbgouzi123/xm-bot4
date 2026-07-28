"""
Excel 智能解析引擎 — 支持 xls/xlsx/csv

核心能力:
  - 智能识别表头（模糊匹配别名库）
  - 智能提取手机号/微信号（多号分隔、中文顿号）
  - 空号检测字段优先级
  - 企业画像信息自动提取
"""
import re
import os
import logging
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)


# ==================== 字段别名映射 ====================
# key = 系统字段名, value = 可能的 Excel 表头名称列表
FIELD_ALIASES = {
    "company_name": [
        "企业名称", "公司名称", "公司名", "公司", "企业", "company",
        "单位名称", "客户名称", "名称", "商家名称", "店铺名称",
        "客户全称", "全称", "门店名", "门店", "店名",
        "company_name", "companyname", "corp", "firm",
    ],
    "phone_verified": [
        "空号检测正常手机号", "有效手机号", "已验证手机号",
        "空号检测正常手机号（企业）", "有效号码",
    ],
    "phone_verified_alt": [
        "空号检测正常手机号（关联）",
    ],
    "phone": [
        "企业手机", "手机", "手机号", "手机号码", "mobile", "phone",
        "联系电话", "电话", "联系手机", "电话号码", "tel", "telephone",
        "联系方式", "手机1", "手机号1", "客户电话", "客户手机",
        "联络人手机", "联络电话", "联系人电话", "联系人手机",
        "phone_number", "mobile_phone", "cell", "cellphone",
    ],
    "phone_alt": [
        "关联手机", "备用手机", "其他手机", "手机2", "手机号2",
        "备用电话", "第二联系人电话",
    ],
    "wechat_id": [
        "企业微信", "微信", "微信号", "wechat", "wx", "微信id",
        "微信账号", "weixin", "wx号",
    ],
    "wechat_id_alt": [
        "关联微信",
    ],
    "landline": [
        "企业固话", "固话", "座机", "固定电话", "座机号",
    ],
    "legal_person": [
        "法定代表人", "法人", "负责人", "联系人", "contact",
        "法人代表", "代表人", "对接人", "姓名", "客户姓名",
        "contact_name", "name", "联络人",
    ],
    "industry": [
        "所属行业", "行业", "industry", "行业类别", "行业分类",
    ],
    "products": [
        "产品或服务", "主营产品", "产品", "经营项目", "服务",
    ],
    "scope": [
        "经营范围", "业务范围", "scope",
    ],
    "intro": [
        "企业简介", "简介", "公司简介", "描述", "备注",
    ],
    "registered_capital": [
        "注册资本", "注册资金", "资本",
    ],
    "employee_count": [
        "员工人数", "员工数", "人数", "规模",
    ],
    "address": [
        "注册地址", "地址", "地区", "通讯地址", "所在地",
        "详细地址", "地址信息", "省市区",
    ],
    "email": [
        "企业邮箱", "邮箱", "email", "邮件", "电子邮箱",
    ],
    "status": [
        "经营状态", "状态", "企业状态",
    ],
    "credit_code": [
        "统一社会信用代码", "信用代码", "社会信用代码",
    ],
    "follow_status": [
        "跟进状态",
    ],
    "follow_person": [
        "跟进人",
    ],
}

# 手机号正则（中国大陆 11 位）
PHONE_REGEX = re.compile(r"1[3-9]\d{9}")


def _normalize_phone_text(text: str) -> str:
    """清洗号码文本：处理 +86、横杠、空格、科学计数法等
    
    支持场景:
      - "+86-138-0013-8000" → "13800138000"
      - "1.38E+10" → "13800000000"
      - "138 0013 8000" → "13800138000"
      - "86-13800138000" → "13800138000"
    """
    if not text:
        return ""
    text = str(text).strip()
    
    # 科学计数法转换（Excel 常见问题：1.38001E+10）
    try:
        if 'e' in text.lower() or 'E' in text:
            num = float(text)
            if 1e10 <= num < 2e10:  # 11位手机号范围
                text = str(int(num))
    except (ValueError, OverflowError):
        pass
    
    # 去掉 +86、086 等国际区号前缀
    text = re.sub(r'^(?:\+?86|086)[-\s]?', '', text)
    # 去掉所有横杠、空格、括号、点
    text = re.sub(r'[\-\s().\u3000]', '', text)
    return text


def parse_excel(file_path: str) -> Dict[str, Any]:
    """解析 Excel/CSV 文件，返回预览数据

    Args:
        file_path: 文件路径（.xls / .xlsx / .csv）

    Returns:
        {
            "success": True,
            "total_rows": 4511,
            "headers": ["企业名称", "手机号", ...],
            "field_mapping": {"company_name": 0, "phone": 3, ...},
            "sample_data": [...前10条...],
            "stats": {
                "valid_phones": 3820,
                "valid_wechats": 156,
                "duplicates": 342,
                "invalid": 193,
            },
            "contacts": [...全量联系人列表...],
        }
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".csv":
        rows, headers = _read_csv(file_path)
    elif ext in (".xls", ".xlsx"):
        rows, headers = _read_excel(file_path)
    else:
        return {"success": False, "error": f"不支持的文件格式: {ext}"}

    if not rows:
        return {"success": False, "error": "文件为空或无法解析"}

    # 0. 智能定位真正的表头行（跳过 logo、标题、空行等非数据行）
    if not headers or not any(headers):
        # 没有表头，尝试从数据中定位
        headers, rows = _smart_detect_header(rows)
    else:
        # 有表头但可能不在第1行（先检查是否能映射到字段）
        test_mapping = _auto_map_fields(headers)
        if not test_mapping:
            # 第1行不是表头，尝试从数据中重新定位
            all_rows = [headers] + rows  # 把“表头”放回去
            headers, rows = _smart_detect_header(all_rows)

    if not headers:
        return {"success": False, "error": "无法识别表头，请检查文件格式"}

    # 1. 智能字段映射
    field_mapping = _auto_map_fields(headers)
    
    # 2. 如果映射不到手机号字段，启用内容嵅探（扫描列数据自动识别）
    if "phone" not in field_mapping and "phone_verified" not in field_mapping:
        phone_col = _detect_phone_column_by_content(rows, headers)
        if phone_col is not None:
            field_mapping["phone"] = phone_col
            logger.info(f"[智能解析] 通过内容嵅探发现手机号在第 {phone_col+1} 列: '{headers[phone_col]}'")
    
    logger.info(f"[Excel] 智能映射: {field_mapping}")

    # 3. 提取全量联系人 + 号码
    contacts, stats = _extract_contacts(rows, headers, field_mapping)

    # 4. 样本数据（前10条）
    sample = contacts[:10] if contacts else []

    return {
        "success": True,
        "total_rows": len(rows),
        "headers": headers,
        "field_mapping": field_mapping,
        "sample_data": sample,
        "raw_sample": rows[:5000],  # Return up to 5000 rows for frontend pagination
        "stats": stats,
        "contacts": contacts,
    }


# ==================== 文件读取 ====================

def _read_excel(file_path: str) -> Tuple[List[List], List[str]]:
    """读取 xls/xlsx 文件"""
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".xls":
        try:
            import xlrd
            wb = xlrd.open_workbook(file_path)
            sh = wb.sheet_by_index(0)
            headers = [str(sh.cell_value(0, c)).strip() for c in range(sh.ncols)]
            rows = []
            for r in range(1, sh.nrows):
                row = []
                for c in range(sh.ncols):
                    v = sh.cell_value(r, c)
                    if isinstance(v, float) and v == int(v):
                        v = str(int(v))
                    else:
                        v = str(v).strip()
                    row.append(v)
                rows.append(row)
            return rows, headers
        except ImportError:
            raise ImportError("需要安装 xlrd: pip install xlrd")
    else:
        try:
            import openpyxl
            wb = openpyxl.load_workbook(file_path, read_only=True)
            ws = wb.active
            all_rows = list(ws.iter_rows(values_only=True))
            if not all_rows:
                return [], []
            headers = [str(c or "").strip() for c in all_rows[0]]
            rows = []
            for row in all_rows[1:]:
                rows.append([str(c or "").strip() for c in row])
            wb.close()
            return rows, headers
        except ImportError:
            raise ImportError("需要安装 openpyxl: pip install openpyxl")


def _read_csv(file_path: str) -> Tuple[List[List], List[str]]:
    """读取 CSV 文件"""
    import csv
    rows = []
    headers = []
    # 尝试多种编码
    for encoding in ("utf-8", "gbk", "gb2312", "latin-1"):
        try:
            with open(file_path, "r", encoding=encoding, newline="") as f:
                reader = csv.reader(f)
                all_rows = list(reader)
                if all_rows:
                    headers = [str(c).strip() for c in all_rows[0]]
                    rows = [[str(c).strip() for c in row] for row in all_rows[1:]]
                break
        except (UnicodeDecodeError, UnicodeError):
            continue
    return rows, headers


# ==================== 智能表头检测 ====================

def _smart_detect_header(all_rows: List[List]) -> Tuple[List[str], List[List]]:
    """智能定位真正的表头行
    
    扫描前20行，通过别名匹配评分找到最可能是表头的行。
    场景：Excel 前几行是公司logo、标题、空行、说明文字等。
    
    Returns:
        (headers, data_rows) — 表头列表和去掉表头后的数据行
    """
    if not all_rows:
        return [], []
    
    best_row_idx = 0
    best_score = 0
    scan_limit = min(20, len(all_rows))  # 最多扫前20行
    
    # 构建所有别名的集合（用于快速匹配）
    all_aliases = set()
    for aliases in FIELD_ALIASES.values():
        for alias in aliases:
            all_aliases.add(alias.lower())
    
    for row_idx in range(scan_limit):
        row = all_rows[row_idx]
        if not row:
            continue
        
        score = 0
        non_empty = 0
        for cell in row:
            cell_str = str(cell or "").strip().lower()
            if not cell_str:
                continue
            non_empty += 1
            
            # 精确匹配别名
            if cell_str in all_aliases:
                score += 3
            # 包含匹配
            elif any(alias in cell_str for alias in all_aliases):
                score += 1
        
        # 表头行通常有多个非空单元格且匹配得分高
        if score > best_score and non_empty >= 2:
            best_score = score
            best_row_idx = row_idx
    
    if best_score == 0:
        # 完全没匹配到任何别名，用第一个有多个非空单元格的行作为表头
        for i in range(scan_limit):
            row = all_rows[i]
            non_empty = sum(1 for c in row if str(c or "").strip())
            if non_empty >= 2:
                best_row_idx = i
                break
    
    headers = [str(c or "").strip() for c in all_rows[best_row_idx]]
    data_rows = all_rows[best_row_idx + 1:]
    
    if best_row_idx > 0:
        logger.info(f"[智能解析] 表头不在第1行！从第 {best_row_idx + 1} 行检测到真正的表头（跳过了 {best_row_idx} 行非数据区）")
    
    return headers, data_rows


def _detect_phone_column_by_content(rows: List[List], headers: List[str]) -> Optional[int]:
    """内容嗅探：当表头匹配不到手机号字段时，扫描列数据识别手机号列
    
    策略：取前50行数据，逐列统计手机号正则命中率，命中率最高且超过30%的列视为手机号列。

    Returns:
        列索引，或 None（无法识别）
    """
    if not rows or not headers:
        return None
    
    sample_rows = rows[:50]
    col_count = len(headers)
    hit_counts = [0] * col_count
    
    for row in sample_rows:
        for col_idx in range(min(len(row), col_count)):
            cell = str(row[col_idx] or "").strip()
            if not cell:
                continue
            # 清洗后查找手机号
            cleaned = _normalize_phone_text(cell)
            if PHONE_REGEX.search(cleaned):
                hit_counts[col_idx] += 1
    
    # 找命中率最高的列
    if not sample_rows:
        return None
    
    best_col = max(range(col_count), key=lambda i: hit_counts[i])
    hit_rate = hit_counts[best_col] / len(sample_rows)
    
    if hit_rate >= 0.3:  # 至少30%的行该列包含手机号
        logger.info(f"[智能解析] 内容嗅探: 第 {best_col+1} 列 '{headers[best_col]}' 手机号命中率 {hit_rate:.0%}")
        return best_col
    
    return None


# ==================== 字段映射（自进化学习引擎）====================

# 学习规则存储路径
_LEARNED_RULES_PATH = os.path.join(
    os.path.expanduser("~"), ".xm-ai-bot", "learned_field_mappings.json"
)

def _load_learned_rules() -> Dict[str, Dict[str, str]]:
    """加载已学习的映射规则"""
    try:
        if os.path.exists(_LEARNED_RULES_PATH):
            import json
            with open(_LEARNED_RULES_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"[学习引擎] 加载规则失败: {e}")
    return {}

def _save_learned_rules(rules: Dict[str, Dict[str, str]]):
    """保存学习到的规则"""
    import json
    os.makedirs(os.path.dirname(_LEARNED_RULES_PATH), exist_ok=True)
    with open(_LEARNED_RULES_PATH, "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False, indent=2)
    logger.info(f"[学习引擎] 规则已保存（共 {len(rules)} 条）")

def _headers_fingerprint(headers: List[str]) -> str:
    """生成表头指纹（用于匹配学习规则）
    
    将表头排序+标准化后取哈希，这样即使列顺序略有不同也能匹配。
    """
    import hashlib
    normalized = sorted([h.strip().lower() for h in headers if h.strip()])
    key = "|".join(normalized)
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:12]


def _auto_map_fields(headers: List[str]) -> Dict[str, int]:
    """自动将 Excel 表头映射到系统字段
    
    优先级：
      1. 先查学习规则（用户之前标注过的）
      2. 再用别名库模糊匹配

    Returns:
        {"company_name": 0, "phone": 3, ...} 字段名→列索引
    """
    mapping = {}
    used_cols = set()

    # ===== 优先级 1: 查学习规则 =====
    learned = _load_learned_rules()
    fp = _headers_fingerprint(headers)
    if fp in learned:
        learned_map = learned[fp]  # {"表头名": "字段名", ...}
        for i, header in enumerate(headers):
            h_lower = header.strip().lower()
            for learned_header, field_name in learned_map.items():
                if learned_header.lower() == h_lower and field_name != "_ignore":
                    mapping[field_name] = i
                    used_cols.add(i)
                    break
        if mapping:
            logger.info(f"[学习引擎] 命中已学习规则（指纹={fp}），映射 {len(mapping)} 个字段")
            return mapping

    # ===== 优先级 2: 别名库匹配 =====
    for field_name, aliases in FIELD_ALIASES.items():
        for i, header in enumerate(headers):
            if i in used_cols:
                continue
            h = header.strip().lower()
            for alias in aliases:
                if alias.lower() == h or alias.lower() in h:
                    mapping[field_name] = i
                    used_cols.add(i)
                    break
            if field_name in mapping:
                break

    return mapping


def learn_mapping(headers: List[str], user_mapping: Dict[str, str]) -> bool:
    """学习用户标注的映射规则
    
    当用户手动标注了 Excel 列→字段的对应关系后，将其持久化。
    下次遇到相同（或相似）表头的 Excel 将自动命中。

    Args:
        headers: Excel 的表头列表
        user_mapping: 用户标注 {"表头名": "字段名", ...}
                      字段名可以是 FIELD_ALIASES 中的 key 或 "_ignore"

    Returns:
        True = 学习成功
    """
    try:
        rules = _load_learned_rules()
        fp = _headers_fingerprint(headers)
        rules[fp] = user_mapping
        _save_learned_rules(rules)
        
        # 同时把新的表头别名注入到 FIELD_ALIASES（当前进程立即生效）
        for header_name, field_name in user_mapping.items():
            if field_name == "_ignore" or not field_name:
                continue
            if field_name in FIELD_ALIASES:
                alias_lower = header_name.strip().lower()
                existing = [a.lower() for a in FIELD_ALIASES[field_name]]
                if alias_lower not in existing:
                    FIELD_ALIASES[field_name].append(header_name.strip())
                    logger.info(f"[学习引擎] 新别名注入: '{header_name}' → {field_name}")
        
        return True
    except Exception as e:
        logger.error(f"[学习引擎] 学习失败: {e}")
        return False


def reparse_with_mapping(file_path: str, user_mapping: Dict[str, str]) -> Dict[str, Any]:
    """根据用户手动映射重新解析 Excel
    
    Args:
        file_path: 文件路径
        user_mapping: 用户标注 {"表头名": "字段名", ...}

    Returns:
        与 parse_excel 相同的结果 dict
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".csv":
        rows, headers = _read_csv(file_path)
    elif ext in (".xls", ".xlsx"):
        rows, headers = _read_excel(file_path)
    else:
        return {"success": False, "error": f"不支持的文件格式: {ext}"}

    if not rows:
        return {"success": False, "error": "文件为空"}

    # 智能表头检测
    if not headers or not any(headers):
        headers, rows = _smart_detect_header(rows)
    
    # 根据用户标注构建 field_mapping
    field_mapping = {}
    for i, header in enumerate(headers):
        h_lower = header.strip().lower()
        for user_header, field_name in user_mapping.items():
            if user_header.lower() == h_lower and field_name != "_ignore":
                field_mapping[field_name] = i
                break
    
    logger.info(f"[手动映射] 用户指定: {field_mapping}")

    # 提取联系人
    contacts, stats = _extract_contacts(rows, headers, field_mapping)
    sample = contacts[:10] if contacts else []

    # 学习成功的映射（自进化）
    if contacts:
        learn_mapping(headers, user_mapping)

    return {
        "success": True,
        "total_rows": len(rows),
        "headers": headers,
        "field_mapping": field_mapping,
        "sample_data": sample,
        "stats": stats,
        "contacts": contacts,
    }


# ==================== 号码提取 ====================

def _extract_phones(text: str) -> List[str]:
    """从文本中智能提取手机号

    支持:
      - 中文顿号分隔: "13800000001、13800000002"
      - 英文逗号分隔: "13800000001,13800000002"
      - 混合分隔符
      - +86、横杠、空格、科学计数法
      - 纯数字 11 位
    """
    if not text:
        return []
    # 先清洗再提取
    cleaned = _normalize_phone_text(str(text))
    return PHONE_REGEX.findall(cleaned)


def _extract_contacts(
    rows: List[List],
    headers: List[str],
    mapping: Dict[str, int],
) -> Tuple[List[Dict], Dict[str, int]]:
    """从所有行提取联系人列表

    Returns:
        (contacts_list, stats_dict)
    """
    contacts = []
    seen_phones = set()
    stats = {
        "valid_phones": 0,
        "valid_wechats": 0,
        "duplicates": 0,
        "invalid": 0,
        "total_contacts": 0,
    }

    def _get(row: List, field: str) -> str:
        """安全获取映射字段值"""
        idx = mapping.get(field)
        if idx is not None and idx < len(row):
            return str(row[idx]).strip()
        return ""

    for row_idx, row in enumerate(rows):
        company = _get(row, "company_name")

        # 号码提取优先级:
        # 1. 空号检测正常手机号（最高优先级，已验证有效）
        # 2. 企业手机（原始号码列表）
        # 3. 关联手机
        all_phones = []

        # 优先级 1: 空号检测正常号
        verified = _get(row, "phone_verified")
        if verified:
            all_phones.extend(_extract_phones(verified))
        verified_alt = _get(row, "phone_verified_alt")
        if verified_alt:
            all_phones.extend(_extract_phones(verified_alt))

        # 优先级 2: 企业手机
        phone_raw = _get(row, "phone")
        if phone_raw:
            all_phones.extend(_extract_phones(phone_raw))

        # 优先级 3: 关联手机
        phone_alt = _get(row, "phone_alt")
        if phone_alt:
            all_phones.extend(_extract_phones(phone_alt))

        # 微信号提取
        wechat = _get(row, "wechat_id") or _get(row, "wechat_id_alt")

        # 去重
        unique_phones = []
        for p in all_phones:
            if p not in seen_phones:
                seen_phones.add(p)
                unique_phones.append(p)
            else:
                stats["duplicates"] += 1

        if not unique_phones and not wechat:
            stats["invalid"] += 1
            continue

        # === 通用字段 ===
        contact = {
            "company_name": company,
            "phones": unique_phones,
            "primary_phone": unique_phones[0] if unique_phones else "",
            "wechat_id": wechat,
            "legal_person": _get(row, "legal_person"),
            "row_index": row_idx + 1,  # Excel 行号（1-based，跳过表头）
        }

        # === 所有未映射列 → extra_fields（原样透传） ===
        mapped_cols = set(mapping.values())  # 已被映射的列索引
        extra = {}
        for col_idx, header in enumerate(headers):
            if col_idx in mapped_cols:
                continue  # 已映射为通用字段，跳过
            val = str(row[col_idx]).strip() if col_idx < len(row) else ""
            if val and val != "nan":  # 跳过空值
                extra[header] = val
        if extra:
            contact["extra_fields"] = extra

        stats["valid_phones"] += len(unique_phones)
        if wechat:
            stats["valid_wechats"] += 1

        contacts.append(contact)

    stats["total_contacts"] = len(contacts)
    return contacts, stats
