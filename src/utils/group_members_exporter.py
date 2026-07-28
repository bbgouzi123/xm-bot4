import os
import openpyxl
import tempfile
from src.crm.account_data import get_active_account, ACCOUNTS_DIR
from src.utils.contacts_exporter import beautify_sheet

def do_export_group_members(group_name: str, members_list: list) -> str:
    """复用已有的 Excel 导出与 beautify_sheet 功能，极速生成带头像的群成员导出 Excel，并返回临时文件路径"""
    active_id = get_active_account()
    filename = f"wechat_group_members_{group_name}.xlsx"
    temp_dir = tempfile.gettempdir()
    file_path = os.path.join(temp_dir, filename)
    
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "群成员列表"
    
    headers = ["头像", "序号", "昵称/备注", "微信内部ID (wxid)", "群聊名称", "经营范围", "所属行业"]
    ws.append(headers)
    
    for idx, m in enumerate(members_list, 1):
        wxid = m.get("wxid") or m.get("username") or ""
        nickname = m.get("display_name") or m.get("nickname") or ""
        ws.append([
            "",  # 头像插图位置
            idx,
            nickname,
            wxid,
            group_name,
            "",
            ""
        ])
    
    # 格式化 items_list，转换出 "wxid" 键，以便 beautify_sheet 从 accounts_dir 匹配 {wxid}.png 物理头像文件进行第一列插图
    formatted_items = []
    for m in members_list:
        formatted_items.append({
            "wxid": m.get("wxid") or m.get("username") or ""
        })
    
    beautify_sheet(ws, True, ACCOUNTS_DIR, formatted_items)
    wb.save(file_path)
    wb.close()
    return file_path
