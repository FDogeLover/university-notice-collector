# -*- coding: utf-8 -*-
"""AI 助手：配置存储 + 统一请求层。

支持两种协议：
- OpenAI 兼容（/v1/chat/completions）：OpenAI / DeepSeek / 通义 / Kimi
- Anthropic 原生（/v1/messages）：Claude

配置（含 API Key）只写入 data/ai_config.json，已被 .gitignore 忽略，
绝不硬编码进源码，也不在日志中输出 Key。
"""
import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "data" / "ai_config.json"

# 供应商预设：选预设自动填充 base_url / model / protocol
PROVIDERS = {
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "protocol": "openai",
    },
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "protocol": "openai",
    },
    "qwen": {
        "name": "通义千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "protocol": "openai",
    },
    "kimi": {
        "name": "Kimi（月之暗面）",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "protocol": "openai",
    },
    "claude": {
        "name": "Claude（Anthropic）",
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-sonnet-4-20250514",
        "protocol": "anthropic",
    },
}


class AIError(Exception):
    """带中文提示的 AI 请求错误。"""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.message = message
        self.status = status


# ---------- 配置存储 ----------
def load_config():
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 配置损坏时按空配置处理
        return {}


def save_config(cfg):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def validate_config(cfg):
    errors = []
    if not (cfg.get("base_url") or "").strip():
        errors.append("Base URL 不能为空")
    if not (cfg.get("api_key") or "").strip():
        errors.append("API Key 不能为空")
    if not (cfg.get("model") or "").strip():
        errors.append("模型名不能为空")
    if cfg.get("protocol") not in ("openai", "anthropic"):
        errors.append("协议必须为 openai 或 anthropic")
    return errors


def mask_key(key):
    """Key 打码：仅保留前 3 位与后 4 位，供前端回显。"""
    key = (key or "").strip()
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return key[:3] + "*" * (len(key) - 7) + key[-4:]


def public_config(cfg):
    """返回不含明文 Key 的配置，供前端回显。"""
    return {
        "provider": cfg.get("provider", ""),
        "base_url": cfg.get("base_url", ""),
        "model": cfg.get("model", ""),
        "protocol": cfg.get("protocol", "openai"),
        "api_key_masked": mask_key(cfg.get("api_key", "")),
        "has_key": bool((cfg.get("api_key") or "").strip()),
    }


# ---------- 请求构造 ----------
def _build_request(cfg, messages, system, stream):
    """构造 (payload, headers, url)，按协议分派。"""
    protocol = cfg.get("protocol", "openai")
    base = cfg["base_url"].rstrip("/")
    api_key = cfg["api_key"].strip()

    if protocol == "anthropic":
        msgs = [
            {"role": m["role"], "content": m["content"]}
            for m in messages if m["role"] in ("user", "assistant")
        ]
        return {
            "model": cfg["model"],
            "messages": msgs,
            "system": system,
            "max_tokens": 4096,
            "stream": stream,
        }, {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }, base + "/messages"

    msgs = ([{"role": "system", "content": system}] if system else []) + messages
    return {
        "model": cfg["model"],
        "messages": msgs,
        "stream": stream,
    }, {
        "Authorization": f"Bearer {api_key}",
        "content-type": "application/json",
    }, base + "/chat/completions"


# ---------- 响应解析 ----------
def _extract_openai_text(data):
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise AIError("供应商返回了无法识别的数据格式")


def _extract_anthropic_text(data):
    try:
        return "".join(
            b.get("text", "") for b in data["content"] if b.get("type") == "text"
        )
    except (KeyError, TypeError):
        raise AIError("供应商返回了无法识别的数据格式")


def _extract_text(data, protocol):
    if protocol == "anthropic":
        return _extract_anthropic_text(data)
    return _extract_openai_text(data)


def _sse_error(obj):
    """识别供应商塞在流里的错误对象（部分网关返回 HTTP 200 + 流内 error）。"""
    err = obj.get("error")
    if not isinstance(err, dict):
        return None
    msg = err.get("message") or ""
    code = err.get("code")
    if code == "invalid_api_key" or "api key" in msg.lower() or "apikey" in msg.lower():
        return "API Key 无效或没有权限，请检查配置中的 Key"
    if code == "insufficient_quota" or "quota" in msg.lower() or "余额" in msg or "欠费" in msg:
        return "账户余额不足或额度耗尽，请前往供应商控制台充值"
    if "rate limit" in msg.lower() or "限流" in msg or "too many" in msg.lower():
        return "请求过于频繁或额度不足（限流），请稍后重试"
    return msg or "供应商在流中返回了错误，请稍后重试"


def _parse_openai_sse_line(line):
    """解析 OpenAI 兼容 SSE 的 data: 行。

    返回增量文本；非增量返回 None；流中错误抛 AIError。
    """
    if not line.startswith("data:"):
        return None
    data = line[5:].strip()
    if not data or data == "[DONE]":
        return None
    try:
        obj = json.loads(data)
    except ValueError:
        return None
    err = _sse_error(obj)
    if err:
        raise AIError(err)
    try:
        return obj["choices"][0]["delta"].get("content") or ""
    except (KeyError, IndexError, TypeError):
        return None


def _parse_anthropic_sse_line(line):
    """解析 Anthropic 原生 SSE 的 data: 行（content_block_delta 的文本增量）。"""
    if not line.startswith("data:"):
        return None
    data = line[5:].strip()
    if not data:
        return None
    try:
        obj = json.loads(data)
    except ValueError:
        return None
    err = _sse_error(obj)
    if err:
        raise AIError(err)
    if obj.get("type") == "error":
        return None  # 已在 _sse_error 处理不到时的兜底，避免静默
    delta = obj.get("delta") or {}
    if delta.get("type") == "text_delta":
        return delta.get("text") or ""
    return None


def _try_parse_full_json(line, protocol):
    """兜底：某些网关忽略 stream 参数直接返回完整 JSON，此时一次性取出全文。"""
    if line.startswith("data:") or not line.startswith("{"):
        return None
    try:
        obj = json.loads(line)
    except ValueError:
        return None
    try:
        return _extract_text(obj, protocol)
    except AIError:
        return None


# ---------- 错误分类 ----------
def classify_http_error(status, body_text=""):
    body_text = body_text or ""
    if status in (401, 403):
        return AIError("API Key 无效或没有权限（HTTP 401/403），请检查配置中的 Key", status)
    if status == 402:
        return AIError("账户余额不足或欠费（HTTP 402），请前往供应商控制台充值", status)
    if status == 404:
        return AIError("接口地址或模型不存在（HTTP 404），请检查 Base URL 与模型名", status)
    if status == 429:
        return AIError("请求过于频繁或额度不足（HTTP 429），请稍后重试或检查套餐余额", status)
    if status == 400:
        return AIError("请求参数有误（HTTP 400），常见原因是模型名错误，请检查模型名", status)
    if 500 <= status < 600:
        return AIError(f"供应商服务端错误（HTTP {status}），请稍后重试", status)
    return AIError(f"请求失败（HTTP {status}），请检查 Base URL 与网络", status)


def _network_error(exc):
    if isinstance(exc, httpx.ConnectError):
        return AIError("网络连接失败：无法访问供应商接口，请检查网络或 Base URL")
    if isinstance(exc, httpx.TimeoutException):
        return AIError("请求超时：供应商响应过慢，请稍后重试")
    return AIError(f"网络异常：{exc}")


# ---------- 对话 ----------
async def chat_once(cfg, messages, system=""):
    """一次性（非流式）对话，返回完整回复文本。"""
    payload, headers, url = _build_request(cfg, messages, system, stream=False)
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as e:
        raise _network_error(e)
    if resp.status_code >= 400:
        raise classify_http_error(resp.status_code, resp.text)
    return _extract_text(resp.json(), cfg.get("protocol", "openai"))


async def chat_stream(cfg, messages, system=""):
    """流式对话生成器：逐段 yield 增量文本。

    若供应商忽略 stream 直接返回完整 JSON，会一次性 yield 全文（优雅降级）。
    流中错误对象 / 流后置错误 / 空流都会抛 AIError，绝不静默返回空内容。
    """
    protocol = cfg.get("protocol", "openai")
    payload, headers, url = _build_request(cfg, messages, system, stream=True)
    got_any = False
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "replace")
                    raise classify_http_error(resp.status_code, body)
                parse = (
                    _parse_anthropic_sse_line if protocol == "anthropic"
                    else _parse_openai_sse_line
                )
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    chunk = parse(line)
                    if chunk is None:
                        chunk = _try_parse_full_json(line, protocol)
                    if chunk:
                        got_any = True
                        yield chunk
    except httpx.HTTPError as e:
        raise _network_error(e)
    if not got_any:
        raise AIError("供应商返回了空回复（可能是内容被拦截、限流或服务异常），请稍后重试或换个问题")
