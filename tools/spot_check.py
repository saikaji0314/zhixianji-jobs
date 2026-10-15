#!/usr/bin/env python3
"""对某个批次的采集结果做独立核对，并打印可读抽样。

这个脚本的存在是为了让 docs/validation.md 里的结论可复现：它不复用采集阶段的判断，
而是从**数据库和归档的原始 HTML** 重新核对，默认不访问网络。

核对内容：

A. 计数：窗口内岗位、详情状态、解析路径、来源分布、JD 字数分布、标题一致性。
B. 反伪造：入库的职责／要求文本必须能在该岗位归档的原始 HTML 里找到
   （可见正文 + JSON-LD 里的全部字符串）。找不到就是危险信号。
C. 完整 JD 是否优于列表摘要：入库正文应长于列表接口的 description_excerpt。
D. 实时复核（需 --live）：随机抽样重新访问站点，与库中内容逐字比较。

用法：
    python tools/spot_check.py --db data/zhixianji.db --batch <批次ID>
    python tools/spot_check.py --batch <批次ID> --live --sample 5 --show 2
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import random
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zhixianji.htmlparse import parse_detail  # noqa: E402
from zhixianji.http_client import HttpClient  # noqa: E402

SCRIPT_RE = re.compile(r"<(script|style)\b.*?</\1>", re.DOTALL | re.IGNORECASE)
JSONLD_RE = re.compile(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def squash(text: str) -> str:
    """去掉所有空白字符，用于宽松的包含判断。"""
    return WS_RE.sub("", text or "")


def visible_text(page_html: str) -> str:
    body = SCRIPT_RE.sub(" ", page_html)
    body = TAG_RE.sub(" ", body)
    return html_mod.unescape(body)


def evidence_text(page_html: str) -> str:
    """页面里所有可能承载 JD 的文本：可见正文 + JSON-LD 里的全部字符串。

    不能只取可见正文：入库正文可能来自 ld+json 的 responsibilities/qualifications，
    那部分在 <script> 里，去脚本后自然找不到——那是检查方法的漏洞，不是数据问题。
    """
    parts = [visible_text(page_html)]
    for match in JSONLD_RE.finditer(page_html):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue

        def walk(node):
            if isinstance(node, str):
                parts.append(node)
            elif isinstance(node, dict):
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(data)
    return squash(" ".join(parts))


def latest_batch(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT batch_id FROM batches ORDER BY created_at DESC LIMIT 1").fetchone()
    return row[0] if row else None


def group_counts(conn: sqlite3.Connection, sql: str, batch_id: str) -> dict[str, int]:
    return {row[0] or "none": row[1] for row in conn.execute(sql, (batch_id,))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="独立核对某个批次的采集结果")
    parser.add_argument("--db", default=str(ROOT / "data" / "zhixianji.db"))
    parser.add_argument("--batch", default=None, help="批次 ID；默认取最近一个批次")
    parser.add_argument("--live", action="store_true", help="抽样重新访问站点做实时复核")
    parser.add_argument("--sample", type=int, default=5, help="实时复核抽样条数")
    parser.add_argument("--show", type=int, default=2, help="打印几条完整 JD 供人工阅读")
    parser.add_argument("--seed", type=int, default=20261015, help="抽样随机种子，便于复现")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.is_file():
        print(f"找不到数据库：{db_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    batch_id = args.batch or latest_batch(conn)
    if not batch_id:
        print("数据库里没有批次", file=sys.stderr)
        return 2
    batch = conn.execute("SELECT * FROM batches WHERE batch_id = ?", (batch_id,)).fetchone()
    if batch is None:
        print(f"找不到批次：{batch_id}", file=sys.stderr)
        return 2

    print("=" * 78)
    print("批次:", batch_id)
    print("创建:", batch["created_at"])
    print("窗口:", json.dumps(json.loads(batch["window_json"]), ensure_ascii=False))
    print("=" * 78)

    rows = list(
        conn.execute(
            "SELECT l.*, d.status AS jd_status, d.responsibilities, d.qualifications, d.jd_chars,"
            " d.raw_path, d.parse_source, d.title_matches"
            " FROM list_items l LEFT JOIN details d"
            " ON l.batch_id = d.batch_id AND l.source = d.source AND l.source_job_id = d.source_job_id"
            " WHERE l.batch_id = ? AND l.in_window = 1"
            " ORDER BY l.source, l.source_job_id",
            (batch_id,),
        )
    )
    ok_rows = [r for r in rows if r["jd_status"] == "ok"]

    # ---------------- A. 计数 ----------------
    print("\n[A] 计数")
    print(f"  窗口内岗位        : {len(rows)}")
    print("  详情状态          : " + json.dumps(group_counts(
        conn, "SELECT status, COUNT(*) FROM details WHERE batch_id=? GROUP BY status", batch_id
    ), ensure_ascii=False))
    print("  解析路径          : " + json.dumps(group_counts(
        conn,
        "SELECT parse_source, COUNT(*) FROM details WHERE batch_id=? GROUP BY parse_source",
        batch_id,
    ), ensure_ascii=False))
    sources = list(
        conn.execute(
            "SELECT source, COUNT(*) n FROM list_items WHERE batch_id=? AND in_window=1"
            " GROUP BY source ORDER BY n DESC",
            (batch_id,),
        )
    )
    print(f"  招聘来源数        : {len(sources)}")
    print("  来源分布          : " + ", ".join(f"{r['source']}={r['n']}" for r in sources))
    if rows:
        print(f"  完整 JD (ok)      : {len(ok_rows)} / {len(rows)}  ({len(ok_rows)/len(rows)*100:.1f}%)")
    chars = sorted(r["jd_chars"] or 0 for r in ok_rows)
    if chars:
        n = len(chars)
        print(f"  JD 字数 中位/p10/p90: {chars[n//2]} / {chars[n//10]} / {chars[int(n*0.9)]}")
        print(f"  JD 字数 <100 条数 : {sum(1 for c in chars if c < 100)}")
    print(f"  标题一致性取值    : {sorted({r['title_matches'] for r in ok_rows}, key=lambda x: (x is None, x))}")

    # ---------------- B. 反伪造 ----------------
    print("\n[B] 反伪造：入库正文必须能在该岗位归档页面里找到")
    checked = missing_text = no_raw = 0
    examples: list[str] = []
    for row in ok_rows:
        raw_path = row["raw_path"]
        if not raw_path or not Path(raw_path).is_file():
            no_raw += 1
            continue
        page = evidence_text(Path(raw_path).read_text(encoding="utf-8"))
        checked += 1
        for label, value in (("职责", row["responsibilities"]), ("要求", row["qualifications"])):
            if squash(value) and squash(value) not in page:
                missing_text += 1
                examples.append(f"{row['source']}/{row['source_job_id']} 的{label}无法溯源")
    print(f"  核对条数          : {checked}")
    print(f"  缺归档 HTML 条数  : {no_raw}")
    print(f"  正文无法溯源条数  : {missing_text}")
    for line in examples[:5]:
        print("    -", line)

    # ---------------- C. 摘要 vs 完整 JD ----------------
    print("\n[C] 完整 JD 是否优于列表摘要")
    longer = sum(1 for r in ok_rows if (r["jd_chars"] or 0) > len(r["description_excerpt"] or ""))
    print(f"  JD 长于列表摘要   : {longer}")
    print(f"  JD 不长于列表摘要 : {len(ok_rows) - longer}")

    # ---------------- D. 实时复核 ----------------
    if args.live:
        print(f"\n[D] 实时复核：随机抽 {args.sample} 条重新访问站点并逐字比较")
        random.seed(args.seed)
        client = HttpClient(user_agent="zhixianji-jobs/0.1 (validation)", timeout_ms=30000, max_retries=2)
        sample = random.sample(ok_rows, min(args.sample, len(ok_rows))) if ok_rows else []
        identical = 0
        for row in sample:
            url = f"{batch['base_url']}{row['public_path']}"
            response = client.get(url, accept="text/html")
            if not response.ok:
                print(f"  [跳过] {row['source']}/{row['source_job_id']} HTTP {response.status}")
                continue
            parsed = parse_detail(response.text)
            same = squash(parsed["responsibilities"]) == squash(
                row["responsibilities"]
            ) and squash(parsed["qualifications"]) == squash(row["qualifications"])
            identical += 1 if same else 0
            print(
                f"  [{'一致' if same else '不一致'}] {row['source']}/{row['source_job_id']} "
                f"{row['title'][:40]}"
            )
        print(f"  → {identical}/{len(sample)} 与实时页面逐字一致")
    else:
        print("\n[D] 实时复核：已跳过（加 --live 开启，会访问站点）")

    # ---------------- E. 人工可读 ----------------
    if args.show:
        print(f"\n[E] 人工可读抽样（{args.show} 条完整 JD）")
        random.seed(args.seed)
        for row in (random.sample(ok_rows, min(args.show, len(ok_rows))) if ok_rows else []):
            print("-" * 78)
            print(f"标题: {row['title']}")
            print(f"公司: {row['organization']}   地点: {row['location']}   类别: {row['category']}")
            print(f"收录: {row['first_seen_at']}   官网发布: {row['published_at']} ({row['published_at_source']})")
            print(f"来源: {row['source_label']}   解析: {row['parse_source']}")
            print(f"站点: {batch['base_url']}{row['public_path']}")
            print("【岗位职责】")
            print((row["responsibilities"] or "")[:1200])
            print("【职位要求】")
            print((row["qualifications"] or "")[:1200])

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
