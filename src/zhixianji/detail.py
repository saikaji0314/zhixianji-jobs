"""详情读取：抓取 /jobs/<source>/<id>，解析完整 JD 并核对岗位身份。

身份核对以页面上的 data-job-source / data-source-job-id 为准；核对不通过时
不把正文写成该岗位的 JD，只记录失败状态（见 AGENTS.md 第 1 条）。
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from .htmlparse import parse_detail
from .http_client import FetchError, HttpClient

STATUS_OK = "ok"
STATUS_INCOMPLETE = "incomplete"
STATUS_MISMATCH = "mismatch"
STATUS_UNVERIFIED = "unverified"
STATUS_MISSING = "missing"
STATUS_ERROR = "error"


def safe_name(source: str, source_job_id: str) -> str:
    keep = []
    for char in f"{source}__{source_job_id}":
        keep.append(char if (char.isalnum() or char in "-_.") else "_")
    return "".join(keep)[:150]


def _identity(item: dict[str, Any], parsed: dict[str, Any]) -> tuple[bool, str]:
    hero = parsed.get("hero") or {}
    source = hero.get("source") or ""
    source_job_id = hero.get("source_job_id") or ""
    if not source or not source_job_id:
        return False, "详情页缺少岗位身份属性（data-job-source / data-source-job-id）"
    if source != str(item.get("source", "")) or source_job_id != str(item.get("source_job_id", "")):
        return False, f"详情页身份为 {source}:{source_job_id}，与目标不一致"
    return True, ""


def fetch_detail(
    client: HttpClient,
    base_url: str,
    item: dict[str, Any],
    raw_dir: Path | None = None,
) -> dict[str, Any]:
    """抓取并解析单个岗位详情，返回可直接入库的记录。"""
    public_path = str(item.get("public_path") or "")
    if not public_path:
        public_path = f"/jobs/{item.get('source')}/{item.get('source_job_id')}"
    url = f"{base_url}{public_path}"

    record: dict[str, Any] = {
        "source": str(item.get("source", "")),
        "source_job_id": str(item.get("source_job_id", "")),
        "fetch_url": url,
        "http_status": None,
        "status": STATUS_ERROR,
        "parse_source": "",
        "title": "",
        "company": "",
        "location": "",
        "category": "",
        "site_collected_at": "",
        "date_posted": "",
        "identifier_value": "",
        "identifier_name": "",
        "responsibilities": "",
        "qualifications": "",
        "jd_text": "",
        "jd_chars": 0,
        "sections_json": "[]",
        "warnings_json": "[]",
        "title_matches": None,
        "content_sha256": "",
        "raw_path": "",
        "attempts": 1,
        "error": "",
        "fetched_at": "",
        "duration_ms": 0,
    }

    started = time.monotonic()
    try:
        response = client.get(url, accept="text/html")
    except FetchError as exc:
        record["error"] = str(exc)
        record["fetched_at"] = _now()
        record["duration_ms"] = int((time.monotonic() - started) * 1000)
        return record

    record["http_status"] = response.status
    record["fetched_at"] = _now()
    record["duration_ms"] = int((time.monotonic() - started) * 1000)

    if response.status == 404:
        record["status"] = STATUS_MISSING
        record["error"] = "详情页返回 404（可能已下线）"
        return record
    if not response.ok:
        record["error"] = f"HTTP {response.status}"
        return record

    html = response.text
    record["content_sha256"] = hashlib.sha256(html.encode("utf-8", errors="replace")).hexdigest()
    if raw_dir is not None:
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_file = raw_dir / f"{safe_name(record['source'], record['source_job_id'])}.html"
        raw_file.write_text(html, encoding="utf-8")
        record["raw_path"] = str(raw_file)

    try:
        parsed = parse_detail(html)
    except Exception as exc:  # noqa: BLE001 - 解析异常按失败记录，不隐藏
        record["status"] = STATUS_ERROR
        record["error"] = f"解析失败：{type(exc).__name__}: {exc}"
        return record

    import json as _json

    record.update(
        {
            "parse_source": parsed.get("parse_source", ""),
            "title": parsed.get("title", ""),
            "company": parsed.get("company", ""),
            "location": parsed.get("location", ""),
            "category": parsed.get("category", ""),
            "site_collected_at": parsed.get("site_collected_at", ""),
            "date_posted": parsed.get("date_posted", ""),
            "identifier_value": parsed.get("identifier_value", ""),
            "identifier_name": parsed.get("identifier_name", ""),
            "responsibilities": parsed.get("responsibilities", ""),
            "qualifications": parsed.get("qualifications", ""),
            "jd_text": parsed.get("jd_text", ""),
            "jd_chars": len(parsed.get("jd_text", "") or ""),
            "sections_json": _json.dumps(parsed.get("sections", []), ensure_ascii=False),
            "warnings_json": _json.dumps(parsed.get("warnings", []), ensure_ascii=False),
        }
    )

    ok, reason = _identity(item, parsed)
    if not ok:
        record["status"] = STATUS_UNVERIFIED if "缺少岗位身份属性" in reason else STATUS_MISMATCH
        record["error"] = reason
        record["title_matches"] = None
        # 身份无法确认时清空正文，避免把别的岗位内容写成这个岗位的 JD。
        record["responsibilities"] = ""
        record["qualifications"] = ""
        record["jd_text"] = ""
        record["jd_chars"] = 0
        return record

    expected_title = _normalize_title(str(item.get("title", "")))
    actual_title = _normalize_title(record["title"])
    record["title_matches"] = 1 if (not expected_title or expected_title == actual_title) else 0

    if not record["responsibilities"] or not record["qualifications"]:
        record["status"] = STATUS_INCOMPLETE
        missing = []
        if not record["responsibilities"]:
            missing.append("岗位职责")
        if not record["qualifications"]:
            missing.append("职位要求")
        record["error"] = "缺少：" + "、".join(missing)
        return record

    record["status"] = STATUS_OK
    return record


def _normalize_title(text: str) -> str:
    return "".join(text.split())


def _now() -> str:
    from datetime import datetime

    from .window import CN_TZ

    return datetime.now(CN_TZ).isoformat(timespec="seconds")
