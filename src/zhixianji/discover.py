"""列表发现：按搜索条件翻页读取 /api/jobs。

只发送站点自身使用的查询参数（筛选条件 + page），不发送 page_size，
因为实测该参数不被接受、每页固定 60 条。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from .http_client import FetchError, HttpClient


class DiscoverError(RuntimeError):
    """列表响应不符合预期。"""


@dataclass
class DiscoverResult:
    items: list[dict[str, Any]] = field(default_factory=list)
    pages_fetched: int = 0
    total_reported: int = 0
    duplicate_count: int = 0
    warnings: list[str] = field(default_factory=list)


def _item_key(item: dict[str, Any]) -> tuple[str, str]:
    return (str(item.get("source", "")), str(item.get("source_job_id", "")))


def fetch_page(client: HttpClient, base_url: str, search_query: str, page: int) -> dict[str, Any]:
    query = f"{search_query}&{urlencode({'page': page})}" if search_query else urlencode({"page": page})
    url = f"{base_url}/api/jobs?{query}"
    response = client.get(url, accept="application/json")
    if not response.ok:
        raise FetchError(url, f"HTTP {response.status}", response.status)
    try:
        payload = json.loads(response.text)
    except json.JSONDecodeError as exc:
        raise DiscoverError(f"{url} 返回的不是合法 JSON：{exc}") from exc
    if not isinstance(payload, dict) or "jobs" not in payload:
        raise DiscoverError(f"{url} 响应缺少 jobs 字段")
    payload["_url"] = url
    return payload


def discover(
    client: HttpClient,
    base_url: str,
    search_query: str,
    max_pages: int = 200,
    max_jobs: int = 2000,
    raw_dir: Path | None = None,
    progress=None,
) -> DiscoverResult:
    """逐页读取列表，按 (source, source_job_id) 去重。"""
    result = DiscoverResult()
    seen: set[tuple[str, str]] = set()
    page = 1
    pages_total = 1
    first_total: int | None = None
    final_total: int | None = None

    while page <= pages_total and page <= max_pages:
        payload = fetch_page(client, base_url, search_query, page)
        jobs = payload.get("jobs") or []
        if not isinstance(jobs, list):
            raise DiscoverError(f"{payload.get('_url')} 的 jobs 不是数组")

        server_page_size = int(payload.get("page_size") or len(jobs) or 0)
        pages_total = int(payload.get("pages") or 1)
        total = int(payload.get("total") or 0)
        if first_total is None:
            first_total = total
        final_total = total

        if raw_dir is not None:
            raw_dir.mkdir(parents=True, exist_ok=True)
            (raw_dir / f"page-{page:03d}.json").write_text(
                json.dumps({k: v for k, v in payload.items() if k != "_url"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        for item in jobs:
            if not isinstance(item, dict):
                continue
            key = _item_key(item)
            if not key[0] or not key[1]:
                result.warnings.append(f"第 {page} 页存在缺少 source/source_job_id 的记录，已跳过")
                continue
            if key in seen:
                result.duplicate_count += 1
                continue
            seen.add(key)
            item = dict(item)
            item["_list_page"] = page
            result.items.append(item)
            if len(result.items) >= max_jobs:
                result.warnings.append(
                    f"已达到 maxJobs={max_jobs} 上限，停止读取列表；第 {page} 页未读完。"
                )
                result.pages_fetched = page
                result.total_reported = final_total or 0
                return result

        result.pages_fetched = page
        if progress is not None:
            progress(page, pages_total, len(result.items), total, server_page_size)

        if not jobs:
            break
        page += 1

    if page > max_pages and page <= pages_total:
        result.warnings.append(f"已达到 maxPages={max_pages} 上限，站点报告共 {pages_total} 页，未全部读取。")

    result.total_reported = final_total or 0
    if first_total is not None and final_total is not None and first_total != final_total:
        result.warnings.append(
            f"翻页期间站点 total 从 {first_total} 变为 {final_total}，列表可能发生增删；结果已按去重保留。"
        )
    return result
