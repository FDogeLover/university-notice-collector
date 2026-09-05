-- 高校官网信息采集 - 数据库结构
-- 数据库文件：data/university.db
-- 说明：所有时间字段默认存本地时间字符串 yyyy-MM-dd HH:mm:ss

-- 学校
CREATE TABLE IF NOT EXISTS schools (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,          -- 学校名
    domain      TEXT,                          -- 官方域名（用于链接域名过滤）
    note        TEXT
);

-- 采集入口（栏目）
CREATE TABLE IF NOT EXISTS sources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    school_id   INTEGER NOT NULL REFERENCES schools(id),
    name        TEXT NOT NULL,                 -- 栏目名，如 研究生招生网
    url         TEXT NOT NULL UNIQUE,          -- 栏目首页/列表页地址
    category    TEXT,                          -- 招生 / 通知公告 / 信息公开
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 通知/信息本体（每条绑定官方原文链接与快照，保证可溯源）
CREATE TABLE IF NOT EXISTS notices (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    school_id    INTEGER NOT NULL REFERENCES schools(id),
    source_id    INTEGER REFERENCES sources(id),
    title        TEXT NOT NULL,                -- 标题
    url          TEXT NOT NULL UNIQUE,         -- 官方原文链接（溯源核心）
    content_md   TEXT,                         -- 正文快照（转 Markdown，防改版删帖）
    published_at TEXT,                         -- 官方发布时间
    fetched_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    type_tag     TEXT,                         -- 类型标签：推免/预推免/夏令营/招生/通知/公示...
    is_active    INTEGER NOT NULL DEFAULT 1,   -- 原文链接是否仍有效
    UNIQUE(url)
);

-- 结构化字段（由 LLM 或规则抽取：截止日期/学院/专业/条件等）
CREATE TABLE IF NOT EXISTS notice_meta (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_id   INTEGER NOT NULL REFERENCES notices(id),
    field_name  TEXT NOT NULL,                 -- 字段名，如 deadline / college
    field_value TEXT,
    UNIQUE(notice_id, field_name)
);

-- 抓取日志
CREATE TABLE IF NOT EXISTS fetch_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id   INTEGER,
    school_id   INTEGER,
    status      TEXT,                          -- ok / error
    new_count   INTEGER NOT NULL DEFAULT 0,
    message     TEXT,
    run_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 全文索引（标题 + 正文，供关键词检索）
CREATE VIRTUAL TABLE IF NOT EXISTS notices_fts USING fts5(
    title,
    content_md,
    content='notices',
    content_rowid='id'
);

-- FTS 同步触发器
CREATE TRIGGER IF NOT EXISTS notices_ai AFTER INSERT ON notices BEGIN
    INSERT INTO notices_fts(rowid, title, content_md)
    VALUES (new.id, new.title, new.content_md);
END;

CREATE TRIGGER IF NOT EXISTS notices_ad AFTER DELETE ON notices BEGIN
    INSERT INTO notices_fts(notices_fts, rowid, title, content_md)
    VALUES ('delete', old.id, old.title, old.content_md);
END;

CREATE TRIGGER IF NOT EXISTS notices_au AFTER UPDATE ON notices BEGIN
    INSERT INTO notices_fts(notices_fts, rowid, title, content_md)
    VALUES ('delete', old.id, old.title, old.content_md);
    INSERT INTO notices_fts(rowid, title, content_md)
    VALUES (new.id, new.title, new.content_md);
END;
