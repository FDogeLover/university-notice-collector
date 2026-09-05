# -*- coding: utf-8 -*-
"""高校官网信息收集 - Web 操作界面后端（FastAPI）。

启动方式（在项目根目录）：
    python web/app.py
或：
    python -m uvicorn web.app:app --host 127.0.0.1 --port 8000

默认地址：http://127.0.0.1:8000
"""
import json
import os
import re
import subprocess
import sys
import threading
from collections import deque
from datetime import datetime
from pathlib import Path

import yaml

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db import store  # noqa: E402
from crawler.extract import clean_summary, extract_highlights, standardize_highlights  # noqa: E402
from web.ai_client import (  # noqa: E402
    AIError,
    PROVIDERS,
    chat_once,
    chat_stream,
    load_config,
    public_config,
    save_config,
    validate_config,
)
from web.ai_context import SCHOOL_ABBRS, build_context  # noqa: E402

app = FastAPI(title="大学信息收集", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC = Path(__file__).resolve().parent / "static"

# 采集任务状态（内存态），log 存最近若干行，供前端实时展示进度
_crawl_state = {
    "running": False,
    "last": None,
    "started_at": None,
    "log": deque(maxlen=400),
}


# ---------- 工具 ----------
def _rows(sql, params=()):
    conn = store.connect()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _row(sql, params=()):
    rows = _rows(sql, params)
    return rows[0] if rows else None


def _all_school_names():
    return [r["name"] for r in _rows("SELECT name FROM schools")]


def _clean(v):
    return "" if v is None else v


# ---------- 页面 ----------
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


# ---------- 统计 ----------
@app.get("/api/stats")
def stats():
    totals = _row(
        "SELECT (SELECT COUNT(*) FROM schools) AS schools,"
        "       (SELECT COUNT(*) FROM sources) AS sources,"
        "       (SELECT COUNT(*) FROM notices) AS notices,"
        "       (SELECT MAX(fetched_at) FROM notices) AS last_fetch"
    )
    by_type = _rows(
        "SELECT COALESCE(type_tag,'未分类') AS name, COUNT(*) AS count "
        "FROM notices GROUP BY type_tag ORDER BY count DESC"
    )
    by_school = _rows(
        "SELECT sc.name AS name, COUNT(n.id) AS count "
        "FROM schools sc LEFT JOIN notices n ON n.school_id=sc.id "
        "GROUP BY sc.id ORDER BY count DESC"
    )
    return {
        "schools": totals["schools"],
        "sources": totals["sources"],
        "notices": totals["notices"],
        "last_fetch": _clean(totals["last_fetch"]),
        "by_type": by_type,
        "by_school": by_school,
    }


# ---------- 学校 / 类型 ----------
@app.get("/api/schools")
def schools():
    return _rows(
        "SELECT sc.id, sc.name, sc.domain,"
        "       (SELECT COUNT(*) FROM sources s WHERE s.school_id=sc.id) AS source_count,"
        "       (SELECT COUNT(*) FROM notices n WHERE n.school_id=sc.id) AS notice_count "
        "FROM schools sc ORDER BY sc.id"
    )


@app.post("/api/schools")
def add_school(payload: dict):
    """前端添加学校：校验 → 写入 config/schools.yaml → 入库。"""
    name = (payload.get("name") or "").strip()
    domain = (payload.get("domain") or "").strip().lower()
    sources = payload.get("sources") or []

    errors = []
    if not name:
        errors.append("学校名称不能为空")
    if not domain:
        errors.append("官方域名不能为空")
    else:
        domain = domain.removeprefix("http://").removeprefix("https://").split("/")[0]
        if "." not in domain:
            errors.append("域名格式不正确（如 szu.edu.cn）")
    if not sources:
        errors.append("至少添加一个栏目")
    clean_sources = []
    for i, s in enumerate(sources, 1):
        sname = (s.get("name") or "").strip()
        surl = (s.get("url") or "").strip()
        scat = (s.get("category") or "").strip() or "通知公告"
        if not sname or not surl:
            errors.append(f"第 {i} 个栏目：名称和 URL 不能为空")
            continue
        if not surl.startswith(("http://", "https://")):
            errors.append(f"栏目「{sname}」的 URL 需以 http(s):// 开头")
            continue
        clean_sources.append({"name": sname, "url": surl, "category": scat})
    if errors:
        return JSONResponse({"errors": errors}, status_code=400)

    cfg_path = ROOT / "config" / "schools.yaml"
    try:
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"errors": [f"读取配置文件失败：{e}"]}, status_code=500)
    school_list = data.get("schools") or []
    if any(s.get("name") == name for s in school_list):
        return JSONResponse({"errors": [f"学校「{name}」已存在，如需修改请直接编辑 schools.yaml"]},
                            status_code=400)
    school_list.append({"name": name, "domain": domain, "sources": clean_sources})
    data["schools"] = school_list

    # 保留文件头注释（# 开头的连续行），再序列化数据
    raw = cfg_path.read_text(encoding="utf-8").splitlines()
    header = []
    for line in raw:
        if line.startswith("#"):
            header.append(line)
        else:
            break
    new_text = "\n".join(header) + "\n" + yaml.safe_dump(
        data, allow_unicode=True, sort_keys=False, default_flow_style=False
    )
    cfg_path.write_text(new_text, encoding="utf-8")

    conn = store.connect()
    try:
        store.import_schools(conn, [{"name": name, "domain": domain,
                                     "sources": clean_sources}])
    finally:
        conn.close()
    return {"ok": True, "name": name, "message": f"学校「{name}」已添加，可点击「触发采集」获取通知"}


# ---------- AI 智能填写学校信息 ----------
AI_SCHOOL_PROMPT = (
    "你是高校官网信息采集助手。根据用户给出的大学名称（可能含官网链接线索），"
    "输出该校官方招生相关网站的栏目入口信息。\n"
    "只输出一个 JSON 对象，不要任何其他文字或 markdown 标记，格式：\n"
    '{{"name": "学校全称", "domain": "官方主域名（不含 http 与路径，如 szu.edu.cn）", '
    '"sources": [{{"name": "栏目名称", "url": "列表页 URL（https:// 开头）", '
    '"category": "招生 或 通知公告 或 信息公开"}}]}}\n'
    "要求：\n"
    "1. name 用学校官方全称，domain 用该校真实主域名；\n"
    "2. sources 尽量给出 3 个常见栏目：研究生院 / 研究生招生网 / 信息公开网，"
    "栏目名要与该校实际称呼接近；\n"
    "3. url 必须是以 https:// 开头、属于该校官方域名的列表页地址（如 gs.xxx.edu.cn、"
    "yz.xxx.edu.cn 等子域），不确定真实路径的栏目直接省略，宁可少不要编造；\n"
    "4. category 只能取三者之一。"
)


def _extract_json_obj(text):
    """从 AI 输出中提取第一个 { ... } 对象（容忍 ```json 包裹）。"""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None


def _clean_ai_school(obj):
    """清洗并校验 AI 生成的学校信息，返回 (clean_dict, errors)。"""
    errors = []
    if not isinstance(obj, dict):
        return None, ["AI 返回格式不正确，请重试"]
    name = (obj.get("name") or "").strip()
    domain = (obj.get("domain") or "").strip().lower()
    if not name:
        errors.append("AI 未识别出学校名称，请补充学校名重试")
    if not domain:
        errors.append("AI 未识别出学校域名")
    elif "." not in domain:
        errors.append("AI 给出的域名格式不正确")
    if errors:
        return None, errors

    clean_sources = []
    for s in obj.get("sources") or []:
        if not isinstance(s, dict):
            continue
        sname = (s.get("name") or "").strip()
        surl = (s.get("url") or "").strip()
        scat = (s.get("category") or "").strip()
        if not sname or not surl or not surl.startswith(("http://", "https://")):
            continue
        # 域名一致性：URL 的 host 必须属于该校主域名（含子域）
        host = surl.split("/")[2].lower()
        if not (host == domain or host.endswith("." + domain)):
            continue
        if scat not in ("招生", "通知公告", "信息公开"):
            scat = "通知公告"
        clean_sources.append({"name": sname, "url": surl, "category": scat})
    if not clean_sources:
        errors.append("AI 未能生成该校可靠的栏目入口（URL 可能都不在官方域名下），请补充官网链接重试")
        return None, errors
    return {"name": name, "domain": domain, "sources": clean_sources}, []


@app.post("/api/schools/ai-hint")
async def ai_school_hint(payload: dict):
    """AI 智能填写：根据学校名生成域名与栏目入口（只返回，不入库）。"""
    hint = (payload.get("hint") or "").strip()
    if not hint:
        return JSONResponse({"errors": ["请先输入学校名称（如：四川大学）"]}, status_code=400)
    cfg = load_config()
    if not (cfg.get("api_key") or "").strip():
        return JSONResponse(
            {"errors": ["尚未配置 AI API Key，请先打开 AI 助手右上角「设置」完成配置"]},
            status_code=400,
        )
    try:
        text = await chat_once(
            cfg,
            [{"role": "user", "content": f"大学名称/线索：{hint}"}],
            system=AI_SCHOOL_PROMPT,
        )
    except AIError as e:
        return {"ok": False, "errors": [e.message]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "errors": [f"AI 调用失败：{e}"]}

    obj = _extract_json_obj(text)
    school, errors = _clean_ai_school(obj)
    if errors:
        return {"ok": False, "errors": errors}
    return {"ok": True, "school": school}


@app.get("/api/types")
def types():
    return _rows(
        "SELECT COALESCE(type_tag,'未分类') AS name, COUNT(*) AS count "
        "FROM notices GROUP BY type_tag ORDER BY count DESC"
    )


# ---------- 通知列表 ----------
@app.get("/api/notices")
def notices(
    school: str = Query(""),
    type: str = Query(""),
    keyword: str = Query(""),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    conds, params = [], []
    words = []  # 供排序使用：有关键词时标题命中优先
    if school:
        conds.append("sc.name=?")
        params.append(school)
    if type:
        conds.append("COALESCE(n.type_tag,'未分类')=?")
        params.append(type)
    if keyword:
        # 多词搜索：拆成"组"，组间 AND、组内 OR。
        # 同校简称与全名归为一组（"深大"∪"深圳大学"→OR），其余词各自成组。
        kw = keyword.strip()
        words = []  # 展平词表，供排序
        groups = []  # 每个元素是一组词，组内 OR
        rest = kw
        for abbr, full in SCHOOL_ABBRS.items():
            if abbr in rest:
                rest = rest.replace(abbr, " ")
                groups.append([abbr, full])
                words += [abbr, full]
        for name in _all_school_names():
            if name in rest:
                rest = rest.replace(name, " ")
                groups.append([name])
                words.append(name)
        other = [w for w in re.split(r"[\s,，、;；]+", rest)
                 if len(w) >= 2 and w not in words]
        if other:
            groups.append(other)
            words += other
        if groups:
            for g in groups:
                parts = [
                    "(sc.name LIKE ? OR n.title LIKE ? "
                    "OR COALESCE(n.content_md,'') LIKE ?)"
                    for _ in g
                ]
                conds.append("(" + " OR ".join(parts) + ")")
                for w in g:
                    params += [f"%{w}%", f"%{w}%", f"%{w}%"]
        elif kw:
            conds.append("(n.title LIKE ? OR COALESCE(n.content_md,'') LIKE ?)")
            params += [f"%{kw}%", f"%{kw}%"]
    where = (" WHERE " + " AND ".join(conds)) if conds else ""

    base = ("SELECT n.*, sc.name AS school_name, s.name AS source_name "
            "FROM notices n JOIN schools sc ON sc.id=n.school_id "
            "LEFT JOIN sources s ON s.id=n.source_id" + where)
    total = _row(f"SELECT COUNT(*) AS c FROM ({base})", params)["c"]
    # 排序：有关键词时"标题命中词数"优先，再按发布时间倒序
    if words:
        rank = " + ".join(["(n.title LIKE ?)"] * len(words))
        order_sql = (
            base + f" ORDER BY ({rank}) DESC, "
            "(n.published_at IS NULL OR n.published_at='') ASC, "
            "n.published_at DESC, n.fetched_at DESC, n.id DESC LIMIT ? OFFSET ?"
        )
        order_params = params + [f"%{w}%" for w in words] + [limit, offset]
    else:
        order_sql = (
            base + " ORDER BY (n.published_at IS NULL OR n.published_at='') ASC, "
            "n.published_at DESC, n.fetched_at DESC, n.id DESC LIMIT ? OFFSET ?"
        )
        order_params = params + [limit, offset]
    items = _rows(order_sql, order_params)
    for it in items:
        it["excerpt"] = clean_summary(it["content_md"], it["title"], max_len=120)
        it["highlights"] = standardize_highlights(
            extract_highlights(it["content_md"], it["title"]),
            it["published_at"],
        )
        it["content_md"] = ""  # 列表不返回正文，节省流量
    return {"total": total, "items": items}


# ---------- 通知详情 ----------
@app.get("/api/notices/{notice_id}")
def notice_detail(notice_id: int):
    row = _row(
        "SELECT n.*, sc.name AS school_name, sc.domain, s.name AS source_name "
        "FROM notices n JOIN schools sc ON sc.id=n.school_id "
        "LEFT JOIN sources s ON s.id=n.source_id WHERE n.id=?",
        (notice_id,),
    )
    if not row:
        return {"error": "not found"}
    return row


# ---------- 触发采集（手动，带实时进度） ----------
def _run_crawl_worker(max_items=60):
    _crawl_state["running"] = True
    _crawl_state["log"].clear()
    _crawl_state["started_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        proc = subprocess.Popen(
            [sys.executable, "-u", str(ROOT / "run.py"),
             "--max-items", str(max_items), "--sleep", "0.3"],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=env,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            _crawl_state["log"].append(line.rstrip())
        proc.wait(timeout=1800)
    except Exception as e:  # noqa: BLE001
        _crawl_state["log"].append(f"采集异常: {e}")
    _crawl_state["running"] = False
    _crawl_state["last"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@app.post("/api/crawl")
def trigger_crawl(max_items: int = Query(60, ge=5, le=200)):
    if _crawl_state["running"]:
        return {"started": False, "message": "已有采集任务运行中"}
    threading.Thread(target=_run_crawl_worker, args=(max_items,), daemon=True).start()
    return {"started": True, "message": "采集已启动"}


@app.get("/api/crawl/status")
def crawl_status():
    tail = list(_crawl_state["log"])[-14:]
    return {
        "running": _crawl_state["running"],
        "last": _crawl_state["last"],
        "started_at": _crawl_state["started_at"],
        "log": tail,
    }


# ---------- AI 助手：配置 ----------
@app.get("/api/ai/config")
def ai_get_config():
    cfg = load_config()
    return {"providers": PROVIDERS, "config": public_config(cfg)}


@app.post("/api/ai/config")
def ai_save_config(payload: dict):
    old = load_config()
    api_key = (payload.get("api_key") or "").strip()
    if not api_key:
        # 前端回显的是打码 Key，未修改时保留旧 Key
        api_key = old.get("api_key", "")
    cfg = {
        "provider": (payload.get("provider") or "custom").strip(),
        "base_url": (payload.get("base_url") or "").strip().rstrip("/"),
        "api_key": api_key,
        "model": (payload.get("model") or "").strip(),
        "protocol": payload.get("protocol") or "openai",
    }
    errors = validate_config(cfg)
    if errors:
        return JSONResponse({"errors": errors}, status_code=400)
    save_config(cfg)
    return {"ok": True, "config": public_config(cfg)}


@app.post("/api/ai/test")
async def ai_test(payload: dict):
    """连通性测试：发最小请求验证 Key。优先用请求体里的配置（保存前可先测）。"""
    old = load_config()
    cfg = {
        "provider": payload.get("provider") or old.get("provider", "custom"),
        "base_url": (payload.get("base_url") or old.get("base_url", "")).strip().rstrip("/"),
        "api_key": (payload.get("api_key") or old.get("api_key", "")).strip(),
        "model": (payload.get("model") or old.get("model", "")).strip(),
        "protocol": payload.get("protocol") or old.get("protocol", "openai"),
    }
    errors = validate_config(cfg)
    if errors:
        return JSONResponse({"errors": errors}, status_code=400)
    try:
        await chat_once(cfg, [{"role": "user", "content": "你好，请只回复两个字：正常"}])
        return {"ok": True, "message": f"连接成功，模型 {cfg['model']} 可用"}
    except AIError as e:
        return {"ok": False, "message": e.message}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"未知错误：{e}"}


# ---------- AI 助手：对话（SSE 流式，可选结合库内通知） ----------
RAG_SYSTEM_TMPL = (
    "{base}\n\n"
    "回答要求：\n"
    "1. 优先依据下方检索到的通知内容回答；通知里没有的信息不要编造，"
    "明确说明\"库内通知未涉及\"并建议用户点击原文链接核实。\n"
    "2. 引用信息时注明来自哪条通知（如\"据[2]\"），涉及时间、名额、"
    "条件等关键数字尽量原样引用。\n"
    "3. 回答末尾列出用到的通知编号与原文链接。\n\n{ctx}"
)


@app.post("/api/ai/chat")
async def ai_chat(payload: dict):
    cfg = load_config()
    if not (cfg.get("api_key") or "").strip():
        return JSONResponse(
            {"error": "尚未配置 API Key，请先打开 AI 助手右上角「设置」完成配置"},
            status_code=400,
        )
    messages = payload.get("messages") or []
    if not messages or not isinstance(messages, list):
        return JSONResponse({"error": "消息内容不能为空"}, status_code=400)
    system = (payload.get("system") or "").strip()
    use_db = bool(payload.get("use_db"))

    question = next(
        (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
    )

    async def gen():
        final_system = system
        if use_db and question:
            ctx, hits = build_context(question)
            if hits:
                # meta 帧：告知前端本次结合了库内通知
                yield ("data: " + json.dumps(
                    {"meta": f"已结合库内 {hits} 条相关通知作答"},
                    ensure_ascii=False,
                ) + "\n\n")
                final_system = RAG_SYSTEM_TMPL.format(base=system or "你是一个乐于助人的助手。", ctx=ctx)
        try:
            async for chunk in chat_stream(cfg, messages, final_system):
                yield f"data: {json.dumps({'delta': chunk}, ensure_ascii=False)}\n\n"
        except AIError as e:
            yield (f"event: error\ndata: "
                   f"{json.dumps({'message': e.message}, ensure_ascii=False)}\n\n")
        except Exception as e:  # noqa: BLE001
            yield (f"event: error\ndata: "
                   f"{json.dumps({'message': f'未知错误：{e}'}, ensure_ascii=False)}\n\n")

    return StreamingResponse(gen(), media_type="text/event-stream")


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
