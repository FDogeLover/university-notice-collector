# 大学信息收集 · university-notice-collector

把高校官网（研究生院 / 招生网 / 信息公开网）发布的通知自动采集进本地数据库，每条记录绑定**官方原文链接 + 正文快照**，可溯源、可检索、可提问。附 Web 操作界面与**可配置的 AI 助手**，支持基于库内通知作答。

> 详细使用手册见 [用法说明.md](用法说明.md)。

## 功能特性

- **采集层**：requests → Playwright 无头渲染 → 真实 Chrome，多级兜底过反爬；域名白名单过滤；URL + 标题指纹去重
- **Web 界面**（FastAPI + 原生 JS，无前端框架）：
  - 统计概览、按学校 / 类型 / 关键词筛选（支持多词与学校简称，如"深大推免"）
  - 通知卡片 → 正文快照弹窗 → 一键跳官方原文
  - 顶部"触发采集"按钮，后台运行、实时进度
- **AI 助手**：
  - 兼容 OpenAI / DeepSeek / 通义 / Kimi / Claude（OpenAI 兼容协议 + Anthropic 原生协议）
  - 多轮流式对话（SSE）、停止生成、自定义 System Prompt、会话本地持久化
  - **库内检索作答**：提问时自动检索已采集通知作为依据，注明出处，库内没有不编造
  - API Key 本地存储，绝不入库、不提交 Git
- **前端添加学校**：页面上直接添加学校与栏目，或输入校名由 **AI 智能填写** 生成域名与栏目入口

## 技术栈

Python 3.10+ · FastAPI · SQLite · httpx · requests / BeautifulSoup / Playwright · 原生 JavaScript

## 快速开始

```bash
# 1. 安装依赖
pip install fastapi uvicorn httpx requests beautifulsoup4 pyyaml

# 2. 查看已配置的学校/栏目（首次运行自动建库）
python run.py --list

# 3. 采集（可指定学校 / 栏目）
python run.py --school 深圳大学 --source 研究生招生网

# 4. 启动 Web 界面
python web/app.py
# 打开 http://127.0.0.1:8000/
```

## AI 助手配置

1. 打开页面 → 右下角 **AI** 按钮 → **设置**
2. 选择供应商预设（OpenAI / DeepSeek / 通义 / Kimi / Claude），填写 **API Key**
3. 点 **测试连接** 验证后保存
4. 在聊天面板提问即可；开启 **"结合库内通知"** 时，回答会基于已采集数据并附原文链接

API Key 只写入 `data/ai_config.json`（已被 `.gitignore` 忽略），不会进入 Git 历史。

## 添加学校

- **前端**：点顶部 **"添加学校"** → 手动填写栏目，或输入校名用 **"AI 智能填写"** 自动生成 → 核对后保存
- **配置文件**：编辑 `config/schools.yaml` 追加学校与栏目入口（详见 [用法说明.md](用法说明.md) 第四节）

## 目录结构

```
├── config/schools.yaml   # 学校 + 栏目入口清单
├── crawler/              # 采集：fetch / parse / dedup / extract
├── db/                   # SQLite：建表 + 存储 + 查询
├── data/                 # 运行时数据（数据库、AI 配置，不入库）
├── web/                  # FastAPI 后端 + 静态前端（含 AI 助手）
├── run.py                # 手动触发采集
└── query.py              # 命令行检索工具
```

## 安全声明

- 采集目标为各高校官网**公开公告**，数据仅供个人学习与信息聚合，请合理控制采集频率
- 每条通知均附官方原文链接，可点击核对
