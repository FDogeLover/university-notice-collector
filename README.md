# 大学信息收集 · university-notice-collector

把高校官网（研究生院 / 招生网 / 信息公开网）发布的通知自动采集进本地数据库，每条记录绑定**官方原文链接 + 正文快照**，可溯源、可检索、可提问。附 Web 操作界面与**可配置的 AI 助手**，支持基于库内通知作答。

> 详细使用手册见 [用法说明.md](用法说明.md)。

## 功能特性

- **采集层**：requests → Playwright 无头渲染 → 真实 Chrome，多级兜底过反爬；域名白名单过滤；URL 精确去重（标题指纹去重需发布时间非空，见"已知限制"）
  - **强制 IPv4** 解析（消除无 v6 出口时双栈站点的 `Errno 101` 误判，可用 `UNIV_FORCE_IPV4=0` 关闭）
  - **http↔https 协议回退**：原协议抓取失败时自动换另一种协议重试（仅当落点不被 301 跳回原协议时采纳）
  - **瑞数类挑战页自解等待**：首访挑战页原地等待 JS 自解并渲染完成，不再因急着取内容误记零产出
  - **出口不可达标记**：对长期拿不到的栏目设 `unreachable: true`，仍照常请求但失败记 `skipped`、不计入错误率（站点恢复即自动重新采到）
- **Web 界面**（FastAPI + 原生 JS，无前端框架）：
  - 统计概览、按学校 / 类型 / 关键词筛选（支持多词与学校简称，如"深大推免"；简称表目前只覆盖 5 所，其余学校按全名匹配）
  - 通知卡片 → 正文快照弹窗 → 一键跳官方原文
  - 顶部"触发采集"按钮，后台运行、实时进度
- **AI 助手**：
  - 兼容 OpenAI / DeepSeek / 通义 / Kimi / Claude（OpenAI 兼容协议 + Anthropic 原生协议）
  - 多轮流式对话（SSE）、停止生成、自定义 System Prompt、会话本地持久化
  - **库内检索作答**：提问时自动检索已采集通知作为依据，注明出处，库内没有不编造
- **结构化字段**：自动从正文提取**截止日期**、起止时间、招生对象、学院等字段入库（`notice_meta`），详情弹窗与 AI 问答直接引用；存量数据可用 `python run.py --backfill-meta` 补齐
- **静态展示站**：`python build_site.py` 一键把库内成果导出为纯展示静态网站（无 AI、无采集），双击 `site/index.html` 即可打开；`python deploy_site.py` 一键**发布到 GitHub Pages**（参考 AUV 站的同形态部署，因数据在本地库改为本地构建 + gh-pages 分支发布）
  - API Key 本地存储，绝不入库、不提交 Git
- **前端添加学校**：页面上直接添加学校与栏目，或输入校名由 **AI 智能填写** 生成域名与栏目入口

## 技术栈

Python 3.10+ · FastAPI · SQLite · httpx · requests / BeautifulSoup / Playwright · 原生 JavaScript

## 快速开始

```bash
# 1. 安装依赖（运行依赖全部锁在 requirements.txt：Web / AI / 浏览器渲染 / PDF 附件抽取）
pip install -r requirements.txt
python -m playwright install chromium   # 仅 browser / real_browser 栏目需要

# 2. 查看已配置的学校/栏目（首次运行自动建库）
python run.py --list

# 3. 采集（可指定学校 / 栏目）
python run.py --school 深圳大学 --source 研究生招生网

# 4. 启动 Web 界面
python web/app.py
# 打开 http://127.0.0.1:8000/
```

## 给 AI Agent 的启动指令

把下面这段提示词直接发给 AI 编程助手（TRAE / Claude / Cursor / Copilot 等），即可让它自动完成"下载 → 装依赖 → 初始化 → 启动 → 验证"全流程：

```text
你是资深 Python 后端工程师。请按以下步骤在本机运行并验证这个仓库项目：

0. 【先确认安装目录】动手前，先向用户确认项目克隆/安装的目标目录
   （例如 D:\projects\ 或 ~/projects/），以用户指定的路径为准；
   不要擅自选目录，也不要克隆到系统目录（如 C:\Windows\ 等）。
1. 克隆仓库：git clone https://github.com/FDogeLover/university-notice-collector
   到确认好的目录下，并进入项目根目录。
2. 安装依赖：pip install fastapi uvicorn httpx requests beautifulsoup4 pyyaml
3. 初始化并查看配置：python run.py --list
   （首次运行会自动创建数据库 data/university.db 并导入学校/栏目）
4. 可选——小规模采集验证：python run.py --school 深圳大学 --max-items 20 --no-detail
5. 启动 Web 界面：python web/app.py
   然后访问 http://127.0.0.1:8000/ 验证。
6. 验证标准：
   - 页面顶部统计卡显示学校/通知数量，通知列表有卡片数据；
   - curl http://127.0.0.1:8000/api/stats 返回 JSON；
   - 能通过关键词筛选搜索（如"推免"）。

注意事项：
- 需要 Python 3.10+，Windows 下使用 PowerShell 执行命令。
- 安装/运行目录以用户确认为准，开始执行任何命令前先报出即将使用的目录路径。
- AI 助手功能（页面右下角"AI"按钮）需要用户自行配置 API Key，
  不要向用户索要或读取 Key；配置入口在页面"设置"里，未配置时提示用户即可。
- data/ 目录是运行时数据（数据库、AI 配置），已被 .gitignore 忽略，
  不要提交也不要删除；数据库缺失时会自动重建。
- 添加学校请引导用户使用页面"添加学校"按钮（支持 AI 智能填写），
  或编辑 config/schools.yaml，不要擅自改代码。
- 不要为了"跑通"而修改 crawler 抓取逻辑；采集失败属正常网络现象，
  如实报告即可。
```

## AI 助手配置

1. 打开页面 → 右下角 **AI** 按钮 → **设置**
2. 选择供应商预设（OpenAI / DeepSeek / 通义 / Kimi / Claude），填写 **API Key**
3. 点 **测试连接** 验证后保存
4. 在聊天面板提问即可；开启 **"结合库内通知"** 时，回答会基于已采集数据并附原文链接

API Key 只写入 `data/ai_config.json`（已被 `.gitignore` 忽略），不会进入 Git 历史。

## 添加学校

- **前端**：点顶部 **"添加学校"** → 手动填写栏目，或输入校名用 **"AI 智能填写"** 自动生成 → 核对后保存
- **管理学校**：点顶部 **"管理学校"** → **停用**（数据保留，仅不再显示与采集，可随时恢复）或**删除**（连同通知与栏目一并移除，不可恢复）
- **批量覆盖 985/211**：`python add_985_211.py` 一键补齐全部 985/211 高校配置（AI 智能填写 + 域名校验，可重复执行续跑），学校卡片与筛选支持 985/211 标签
  - 注意：标签是**互斥**的——985 校只带 `985` 标签，筛"211"只会返回非 985 的 211 校（38 所 985 不在其中）
- **配置文件**：编辑 `config/schools.yaml` 追加学校与栏目入口（详见 [用法说明.md](用法说明.md) 第四节）；栏目可用 `browser: true`（无头渲染过 JS 反爬）、`real_browser: true`（真实 Chrome/有头 chromium 过瑞数）、`stype`（信息领域）、`unreachable: true`（出口不可达标记，见上方采集层说明）

## 目录结构

```
├── config/schools.yaml   # 学校 + 栏目入口清单（唯一权威副本）
├── crawler/              # 采集：fetch / parse / dedup / extract
├── db/                   # SQLite：建表 + 存储 + 查询
├── data/                 # 运行时数据（数据库、AI 配置，不入库）
├── web/                  # FastAPI 后端 + 静态前端（含 AI 助手）
├── scripts/              # 运维工具：巡检 / 实探 / 日期修复 / 备份 / 回滚 / 栏目换址
├── run.py                # 手动触发采集
└── query.py              # 命令行检索工具
```

## 开发与测试

```bash
pip install -r requirements-dev.txt     # pytest
python -m pytest tests/ -q              # 回归测试（完全离线，约 2s，77 项）

# 网站可能改版，可随时刷新 fixture 快照（自动保留抓取失败栏目的旧快照）
python tests/update_fixtures.py
```

测试基于 `tests/fixtures/` 中 10 份各校真实页面快照（7 列表页 + 3 详情页），
解析器行为被意外改坏时会在这里第一时间红灯，而不是等线上采集才发现。
生产部署前建议在目标机器上跑一遍 `python -m pytest -q`（服务器 venv 已装 pytest）。

## 运维

线上由服务器（`ssh study` 的 `/home/university-notice-collector`）承担：cron 每小时
抓一片（3 片覆盖全量 667 个栏目）、每周巡检、每日 04:00 数据库备份、每日 05:00
发布静态站快照 **（线上站是每日快照，不是实时数据）**。

```bash
python scripts/audit_probe.py --db-health     # 栏目健康清单（零产出/失败/停更，不联网）
python scripts/audit_probe.py --sweep          # 全量实探：每个栏目真的能不能采到
python scripts/fix_published_dates.py          # 发布时间体检修复（缺省只报告）
python scripts/backup_db.py --keep 7           # 在线备份（WAL 库不能用 cp）
python scripts/rollback_site.py                # 列出历史快照 / --to <hash> --apply 回滚
```

采集收尾自检会输出本轮 `抓取失败 / 出口不可达跳过计数 / 列表零产出 / 新增` 四项指标，
错误率超过 25% 即非零退出（供定时任务告警）；`unreachable: true` 栏目的失败只计入
"跳过计数"，不计入错误率。诊断/换址工具（`find_list_url.py`、`apply_source_fixes.py`）
对不可达主机先做 TCP 预检，避免浏览器兜底把单栏目诊断拖过数分钟。

发布护栏：`deploy_site.py` 会拒绝"构建库超过 3 天没新数据"或"条数比上次发布少 5%
以上"的发布（本机库比服务器库旧时最容易被拦），确需强制发布加 `--force`。

## 已知限制

审计（2026-09-20，全量实探 667 个栏目）确认、本轮尚未处理的项：

- **无分页**：每个栏目只取列表首页最新的 N 条（服务器 cron 用 `--max-items 50`；
  2026-09-25 前为 20，实测约 22% 栏目单轮截断、排在前 20 个命中链接之外的新条目
  长期漏采，故提到 50——去重先于详情抓取，上限提高只增加"新条目"的成本），
  历史存档无法回溯补齐
- **标题指纹去重依赖发布时间**：`--no-detail` 模式下不生效；库里同校同名但日期不同的
  条目按"不同通知"保留（年度重复的名单/安排属正常）
- **静态站与 Web 站口径不同**：静态站搜索只覆盖标题 + 摘要（Web 站搜全文）；
  "近 7/30 天"快筛以**构建日**为基准；两站的"学校数"分别指"有通知的学校"与"启用学校"
- **服务器出口限制**：服务器没有系统 Chrome，走 Xvfb + chromium 有头渲染（可过瑞数类
  WAF）；但个别站点同时校验出口 IP（如南京师范大学研招），在服务器仍拿不到，只有本机能采
- **无头通道无法救的栏目**：个别站点的列表页本身不产出可解析链接（登录墙 / JS 非 `<a>`
  渲染 / 标题过滤），改 `browser: true` 也采不到，需换 URL 或加 `real_browser: true`
- **图片型正文未 OCR**：`easyocr` 会拉入 torch（数 GB），未列入运行依赖；正文是图片的
  通知正文可能为空
- **约 60 条历史通知的发布时间无法自动确认**：标题年份与发布日期相差 ≥2 年，但栏目列表页
  也给不出真实日期，按原值保留（未置空、也未替换）
- **无 CI**：仓库没有 `.github/workflows`；服务器 venv 已装 pytest，部署前手动跑
  `python -m pytest -q`
- **健康度无界面**：Web 站/静态站都没有"哪些栏目是僵尸"的视图，用
  `python scripts/audit_probe.py --db-health` 在命令行看

## 安全声明

- 采集目标为各高校官网**公开公告**，数据仅供个人学习与信息聚合，请合理控制采集频率
- 采集路径只访问公网 http/https：请求前校验协议、主机名与 DNS 解析出的每个 IP，
  拒绝环回/私有/保留地址（含页面里的附件与 iframe 链接）
- 每条通知均附官方原文链接，可点击核对
