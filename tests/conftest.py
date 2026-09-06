# -*- coding: utf-8 -*-
"""pytest 共享配置：路径、离线化（屏蔽 parse_detail 的网络兜底）、manifest。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_manifest():
    return json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))


def load_fixture(name):
    return (FIXTURE_DIR / f"{name}.html").read_text(encoding="utf-8")


import pytest  # noqa: E402

from crawler import parse  # noqa: E402


@pytest.fixture(autouse=True)
def offline_parse(monkeypatch):
    """所有测试离线运行：屏蔽 parse_detail 的 iframe/PDF/图片网络兜底。"""
    monkeypatch.setattr(parse, "_extract_iframe_text", lambda *a, **k: "")
    monkeypatch.setattr(parse, "extract_pdf_text", lambda *a, **k: "")
    monkeypatch.setattr(parse, "extract_image_text", lambda *a, **k: "")
