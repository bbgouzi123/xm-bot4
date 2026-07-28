import ast
import re
from typing import Tuple, List

# 危险系统命令的粗粒度正则黑名单
DANGEROUS_COMMANDS_RE = [
    r"(?i)\bformat\s+[a-z]:",                     # 格式化磁盘
    r"(?i)\bshutil\.rmtree\s*\(\s*['\"]/?['\"]\)", # 删除根目录
    r"(?i)\bdel\s+/[sfq]+",                       # 危险的物理删除参数
    r"(?i)\brmdir\s+/[sfq]+",
    r"(?i)\brm\s+-rf\s+/",                        # Linux 根删除
    r"(?i)\breg\s+delete\b",                      # 删除注册表
    r"(?i)\bshutdown\s+/[sft\s]+",                # 强制关机
]

# 危险的 Python 导入库黑名单 (用于保护本地敏感资源)
BANNED_IMPORTS = {
    "ctypes",
    "win32api",
    "win32process",
    "win32con",
    "win32thread",
}

# 禁用混淆执行函数，防止后门绕过
BANNED_FUNCTIONS = {
    "eval",
    "exec",
    "__import__",
}


def scan_script_security(code_content: str, cmd_template: str) -> Tuple[bool, str]:
    """
    静态扫描上传的 Python 脚本和命令行模板，确保无恶意危险逻辑。
    返回: (是否安全, 错误信息描述)
    """
    # 1. 检测命令行模板中的 Shell 注入和危险系统命令
    for pattern in DANGEROUS_COMMANDS_RE:
        if re.search(pattern, cmd_template):
            return False, f"命令行模板中包含被拦截的高危系统指令，触发规则: {pattern}"
        if code_content and re.search(pattern, code_content):
            return False, f"脚本源码中包含被拦截的系统命令敏感关键词，触发规则: {pattern}"

    # 2. 如果是 Python 代码，启动 AST 树级深度静态安全检测
    if code_content:
        try:
            tree = ast.parse(code_content)
        except SyntaxError as e:
            return False, f"Python 脚本语法解析错误 (可能是非合规 Python): {e.msg} (第 {e.lineno} 行)"

        # 遍历 AST 节点
        for node in ast.walk(tree):
            # 2.1 检查导入语句
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name.split('.')[0]
                    if name in BANNED_IMPORTS:
                        return False, f"检测到导入高危系统底层库: '{name}'，已被安全规则拒绝"
            
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    name = node.module.split('.')[0]
                    if name in BANNED_IMPORTS:
                        return False, f"检测到从高危系统库导入: '{name}'，已被安全规则拒绝"

            # 2.2 检查高危调用
            elif isinstance(node, ast.Call):
                # 如果是直接函数调用
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                    if func_name in BANNED_FUNCTIONS:
                        return False, f"脚本包含被禁用的动态混淆函数调用: '{func_name}'"
                
                # 如果是属性调用如 getattr / setattr 动态执行
                elif isinstance(node.func, ast.Attribute):
                    # 拦截特殊的内建双下划线属性访问
                    if node.func.attr in ("__globals__", "__subclasses__", "__builtins__"):
                        return False, "检测到反射漏洞尝试访问敏感双下属性"

    return True, "扫描通过"
