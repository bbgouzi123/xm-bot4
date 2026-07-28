import re

with open('d:/code/xm-products/xm-bot4/新建文件夹/webot/dist/js/app.8302dde8.js', 'r', encoding='utf-8') as f:
    text = f.read()

paths = set(re.findall(r'path:"([^"]+)"', text))
paths.update(re.findall(r"path:'([^']+)'", text))
print("Paths found:", sorted(list(paths)))

menus = re.findall(r'title:"([^"]+)"', text)
print("Titles found:", sorted(list(set(menus))))
