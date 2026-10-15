"""导出：CSV、JSONL、可读 Markdown 与批次摘要。

导出只读取本地数据库，不访问站点。每次导出写入新的批次目录，不覆盖历史结果。
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from .store import Store

CSV_COLUMNS = [
    "source", "source_job_id", "title", "organization", "location", "category",
    "job_type", "query_score", "match_scope",
    "first_seen_at", "published_at", "published_at_source",
    "updated_at", "posted_at", "is_active", "source_label", "public_path",
    "detail_url", "jd_status", "jd_parse_source", "jd_title_matches", "jd_chars",
    "responsibilities", "qualifications", "detail_date_posted",
    "site_collected_at", "fetched_at", "jd_error",
]

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def guard_formula(value: Any) -> Any:
    """避免表格软件把 JD 里的 =/+/-/@ 开头内容当作公式执行。"""
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def safe_filename(source: str, source_job_id: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]", "_", f"{source}__{source_job_id}")[:150]


# 站点用 query_score 表示命中位置，实测（2026-10-15，q=agent）只有三档：
#   110 / 100 -> 标题命中关键词（110 与 100 的区别未查明）
#   10        -> 标题不含关键词，靠「岗位详情」命中
# 站点搜索页自己也写明「标题命中优先，其次匹配岗位详情」。
# 因此 >= TITLE_HIT_MIN_SCORE 等价于「标题命中」，该值随站点实现可能变化。
TITLE_HIT_MIN_SCORE = 100


def is_title_hit(item: dict[str, Any]) -> bool:
    """该岗位是否属于「标题命中关键词」那一档。"""
    score = item.get("query_score")
    if score is None:
        return False
    try:
        return int(score) >= TITLE_HIT_MIN_SCORE
    except (TypeError, ValueError):
        return False


def build_rows(store: Store, batch_id: str, title_only: bool = False) -> list[dict[str, Any]]:
    details = store.get_details(batch_id)
    rows: list[dict[str, Any]] = []
    for item in store.get_list_items(batch_id, in_window_only=True):
        if title_only and not is_title_hit(item):
            continue
        key = (item["source"], item["source_job_id"])
        detail = details.get(key, {})
        rows.append(
            {
                "source": item["source"],
                "source_job_id": item["source_job_id"],
                "title": item["title"],
                "organization": item["organization"],
                "location": item["location"],
                "category": item["category"],
                "job_type": item["job_type"],
                "query_score": item["query_score"],
                "match_scope": "title" if is_title_hit(item) else "description",
                "first_seen_at": item["first_seen_at"],
                "published_at": item["published_at"],
                "published_at_source": item["published_at_source"],
                "updated_at": item["updated_at"],
                "posted_at": item["posted_at"],
                "is_active": item["is_active"],
                "source_label": item["source_label"],
                "public_path": item["public_path"],
                "detail_url": item["detail_url"],
                "jd_status": detail.get("status", "not_fetched"),
                "jd_parse_source": detail.get("parse_source", ""),
                "jd_title_matches": detail.get("title_matches"),
                "jd_chars": detail.get("jd_chars", 0),
                "responsibilities": detail.get("responsibilities", ""),
                "qualifications": detail.get("qualifications", ""),
                "detail_date_posted": detail.get("date_posted", ""),
                "site_collected_at": detail.get("site_collected_at", ""),
                "fetched_at": detail.get("fetched_at", ""),
                "jd_error": detail.get("error", ""),
            }
        )
    return rows


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: guard_formula(row.get(key)) for key in CSV_COLUMNS})


def write_jsonl(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_markdown(rows: list[dict[str, Any]], directory: Path) -> int:
    directory.mkdir(parents=True, exist_ok=True)
    written = 0
    for row in rows:
        if row["jd_status"] != "ok":
            continue
        name = safe_filename(str(row["source"]), str(row["source_job_id"])) + ".md"
        lines = [
            f"# {row['title']}",
            "",
            f"- 公司/组织：{row['organization']}",
            f"- 地点：{row['location']}",
            f"- 类别：{row['category']}",
            f"- 岗位类型：{row['job_type']}（社招/校招/实习由站点给出）",
            f"- 来源：{row['source_label']}（{row['source']}）",
            f"- 收录时间：{row['first_seen_at']}",
            f"- 官网发布时间：{row['published_at']}（{row['published_at_source']}）",
            f"- 站点链接：https://jobs.ultraai.site{row['public_path']}",
            f"- 官网链接：{row['detail_url']}",
            f"- 抓取时间：{row['fetched_at']}",
            "",
            "## 岗位职责",
            "",
            row["responsibilities"] or "（缺失）",
            "",
            "## 职位要求",
            "",
            row["qualifications"] or "（缺失）",
            "",
        ]
        (directory / name).write_text("\n".join(lines), encoding="utf-8")
        written += 1
    return written


def write_summary(
    store: Store,
    batch_id: str,
    path: Path,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    batch = store.get_batch(batch_id) or {}
    details = store.get_details(batch_id)
    rows = build_rows(store, batch_id)

    out_of_window_reasons: dict[str, int] = {}
    for item in store.get_list_items(batch_id, in_window_only=False):
        if item["in_window"]:
            continue
        reason = item["window_reason"] or "unknown"
        out_of_window_reasons[reason] = out_of_window_reasons.get(reason, 0) + 1

    parse_sources: dict[str, int] = {}
    for record in details.values():
        key = record.get("parse_source") or "none"
        parse_sources[key] = parse_sources.get(key, 0) + 1

    # 命中位置分布：站点把「标题命中」和「只在岗位详情里命中」放在同一个结果集里，
    # 分析前需要区分，否则会把「有 Agent 经验者优先」的无关岗位当成 Agent 岗位。
    match_scope = {"title": 0, "description": 0}
    for item in store.get_list_items(batch_id, in_window_only=True):
        match_scope["title" if is_title_hit(item) else "description"] += 1

    summary: dict[str, Any] = {
        "batchId": batch_id,
        "createdAt": batch.get("created_at"),
        "baseUrl": batch.get("base_url"),
        "search": json.loads(batch.get("config_json") or "{}").get("search"),
        "window": json.loads(batch.get("window_json") or "{}"),
        "counts": {
            "listItemsStored": store.window_counts(batch_id)["in_window"]
            + store.window_counts(batch_id)["out_of_window"],
            "inWindow": store.window_counts(batch_id)["in_window"],
            "inWindowTitleHit": match_scope["title"],
            "inWindowDescriptionOnly": match_scope["description"],
            "outOfWindow": store.window_counts(batch_id)["out_of_window"],
            "detailsFetched": len(details),
            "jdOk": sum(1 for row in rows if row["jd_status"] == "ok"),
            "withFullJd": sum(1 for row in rows if row["jd_chars"]),
        },
        "outOfWindowReasons": out_of_window_reasons,
        "detailStatus": store.detail_status_counts(batch_id),
        "detailParseSource": parse_sources,
        "sourcesInWindow": store.source_counts(batch_id, in_window_only=True),
        "matchScope": {
            "note": "站点按「标题命中优先，其次匹配岗位详情」返回结果；query_score>=100 为标题命中",
            "titleHitMinScore": TITLE_HIT_MIN_SCORE,
            "inWindow": match_scope,
        },
        "outputs": {
            "csv": "jobs.csv",
            "jsonl": "jobs.jsonl",
            "markdownDir": "jd/",
            "rawListDir": "raw/list/",
            "rawHtmlDir": "raw/html/",
        },
    }
    if extra:
        summary.update(extra)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 详情阶段会再次导出同一个批次。此时保留上一阶段写入的额外信息（例如列表读取统计），
    # 只刷新本次重算的计数，避免后一次导出把前一次的证据丢掉。
    merged = summary
    if path.is_file():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            previous = None
        if isinstance(previous, dict):
            merged = {**previous, **summary}
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return merged


def export_all(
    store: Store,
    batch_id: str,
    output_dir: Path,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """导出整个窗口，并同时给出「标题命中」子集。

    站点的 `q` 同时匹配标题和岗位详情，两者混在一个结果集里。分析前必须能分开，
    否则「有 Agent 经验者优先」这类只在要求里顺带提一句的岗位会被当成 Agent 岗位。
    所以这里总是把两种口径都落盘，而不是加一个开关——开关会带来「上次导出带了、
    这次没带」的状态问题。
    """
    rows = build_rows(store, batch_id)
    write_csv(rows, output_dir / "jobs.csv")
    write_jsonl(rows, output_dir / "jobs.jsonl")
    md_count = write_markdown(rows, output_dir / "jd")

    title_rows = [row for row in rows if row["match_scope"] == "title"]
    write_csv(title_rows, output_dir / "jobs-title-hit.csv")
    write_jsonl(title_rows, output_dir / "jobs-title-hit.jsonl")
    title_md = write_markdown(title_rows, output_dir / "jd-title-hit")

    summary = write_summary(store, batch_id, output_dir / "summary.json", extra)
    summary.setdefault("counts", {})["titleHitRows"] = len(title_rows)
    summary.setdefault("outputs", {}).update(
        {
            "markdownFiles": md_count,
            "titleHitCsv": "jobs-title-hit.csv",
            "titleHitJsonl": "jobs-title-hit.jsonl",
            "titleHitMarkdownDir": "jd-title-hit/",
            "titleHitMarkdownFiles": title_md,
        }
    )
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary
