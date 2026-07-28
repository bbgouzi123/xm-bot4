"""
AI 返回 JSON 容错解析模块

AI（尤其是 Coze）偶尔会返回不合法的 JSON，常见情况：
  1. 字符串字段值中包含裸换行符（URL / image_prompt）
  2. 生成被截断，JSON 在字符串值中间结束，缺少闭合引号/括号
  3. 中文标点混入（，：""）

本模块提供五级容错解析，按优先级依次尝试：
  Level 1 - 标准解析
  Level 2 - 暴力清洗（去掉结构层换行、修复悬挂逗号）
  Level 3 - 截断修复（状态机找最后一个完整 {} 对象）
  Level 3b - 强制闭合（利用深度信息补全缺失的 ""}]）
  Level 4 - 逐条 regex 提取
"""
import re
import json


def fix_unescaped_quotes(s: str) -> str:
    """修复 JSON 中夹在字符串内部未转义的双引号"""
    n = len(s)
    res = []
    in_str = False
    esc = False
    
    i = 0
    while i < n:
        ch = s[i]
        if esc:
            res.append(ch)
            esc = False
            i += 1
            continue
        if ch == '\\':
            res.append(ch)
            esc = True
            i += 1
            continue
        if ch == '"':
            if not in_str:
                in_str = True
                res.append(ch)
            else:
                # 可能是结束双引号，也可能是内部未转义双引号
                is_structural = False
                j = i + 1
                while j < n:
                    next_ch = s[j]
                    if next_ch not in ' \t\n\r':
                        if next_ch in '}]:':
                            is_structural = True
                        elif next_ch == ',':
                            # 确认逗号后面是否跟着键值对双引号或对象/数组括号
                            k = j + 1
                            while k < n:
                                post_ch = s[k]
                                if post_ch not in ' \t\n\r':
                                    if post_ch in '"{}[':
                                        is_structural = True
                                    break
                                k += 1
                        break
                    j += 1
                else:
                    is_structural = True
                
                if is_structural:
                    in_str = False
                    res.append(ch)
                else:
                    res.append('\\"')
        else:
            res.append(ch)
        i += 1
    return "".join(res)


def robust_json_parse(raw_str: str):
    """五级容错解析 AI 返回的 JSON 数组。成功时返回 list，失败时返回 None。"""

    # Level 1 ─────────────────────────────────────────────────────────────
    try:
        data = json.loads(raw_str, strict=False)
        if isinstance(data, list):
            print(f"[日历引擎] ✅ 标准JSON解析成功，解析出 {len(data)} 条数据")
            return data
    except json.JSONDecodeError as e:
        print(f"[日历引擎] 标准JSON解析失败: {e}，尝试暴力修复...")

    # Level 2 ─────────────────────────────────────────────────────────────
    cleaned = raw_str.replace('\n', ' ').replace('\r', '')
    cleaned = fix_unescaped_quotes(cleaned)
    cleaned = re.sub(r',\s*,', ',', cleaned)
    cleaned = re.sub(r'\{\s*,', '{', cleaned)
    cleaned = re.sub(r'\[\s*,', '[', cleaned)
    cleaned = re.sub(r',\s*\]', ']', cleaned)
    cleaned = re.sub(r',\s*\}', '}', cleaned)
    try:
        data = json.loads(cleaned, strict=False)
        if isinstance(data, list):
            print(f"[日历引擎] ✅ 暴力清洗成功，解析出 {len(data)} 条数据")
            return data
    except json.JSONDecodeError:
        print(f"[日历引擎] 暴力清洗后仍然失败，尝试截断修复...")

    # Level 3 / 3b ────────────────────────────────────────────────────────
    try:
        arr_start = cleaned.find('[')
        work = cleaned[arr_start:] if arr_start >= 0 else '[' + cleaned

        last_ok = -1
        brace_d = bracket_d = 0
        in_str = esc = False

        for i, ch in enumerate(work):
            if esc:
                esc = False
                continue
            if ch == '\\' and in_str:
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == '{':
                brace_d += 1
            elif ch == '}':
                brace_d -= 1
                if brace_d == 0 and bracket_d == 1:
                    last_ok = i
            elif ch == '[':
                bracket_d += 1
            elif ch == ']':
                bracket_d -= 1

        # Level 3: 找到了完整对象
        if last_ok > 0:
            try:
                data = json.loads(work[:last_ok + 1] + ']', strict=False)
                if isinstance(data, list) and data:
                    print(f"[日历引擎] ✅ 截断修复成功！抢救出 {len(data)} 条完整排期")
                    return data
            except json.JSONDecodeError:
                pass

        # Level 3b: 强制闭合截断的 JSON
        if brace_d > 0:
            forced = work
            if in_str:
                forced += '"'
            for _ in range(max(0, bracket_d - 1)):
                forced += ']'
            for _ in range(brace_d):
                forced += '}'
            forced += ']'
            try:
                data = json.loads(forced, strict=False)
                if isinstance(data, list) and data:
                    valid = [x for x in data
                             if isinstance(x, dict) and 'day_offset' in x and 'text' in x]
                    if valid:
                        print(f"[日历引擎] ✅ 强制闭合修复成功！抢救出 {len(valid)} 条有效排期")
                        return valid
            except json.JSONDecodeError:
                pass

    except Exception as e:
        print(f"[日历引擎] 截断修复异常: {e}")

    # Level 4 ─────────────────────────────────────────────────────────────
    try:
        pat = re.compile(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}')
        data = []
        for m in pat.findall(cleaned):
            try:
                obj = json.loads(m, strict=False)
                if isinstance(obj, dict) and 'day_offset' in obj and 'text' in obj:
                    data.append(obj)
            except json.JSONDecodeError:
                continue
        if data:
            print(f"[日历引擎] ✅ 逐条提取成功！抢救出 {len(data)} 条有效排期")
            return data
    except Exception as e:
        print(f"[日历引擎] 逐条提取异常: {e}")

    print(f"[日历引擎] ❌ 五级容错全部失败，放弃解析")
    return None
