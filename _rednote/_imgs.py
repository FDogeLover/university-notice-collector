# -*- coding: utf-8 -*-
"""更新笔记图片列表：用真实界面截图替换 AI 卡片图。"""
from pathlib import Path

html_file = Path(r"d:\项目开发\大学信息收集\_rednote\university-notice-tool-share\post\rednote-post.html")
html = html_file.read_text(encoding="utf-8")

old = '["../images/cover.jpg", "../images/card1.jpg", "../images/card2.jpg", "../images/card3.jpg"]'
new = '["../images/cover.jpg", "../images/shot1.jpg", "../images/shot2.jpg", "../images/shot3.jpg"]'

if old not in html:
    raise SystemExit("未找到图片列表")
html = html.replace(old, new, 1)
html_file.write_text(encoding="utf-8", data=html)
print("图片列表已更新为真实截图")
