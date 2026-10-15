"""存储与导出测试：窗口标记、批次恢复、CSV 可回读、公式注入防护。"""

from __future__ import annotations

import csv
import json
import unittest
from datetime import datetime
from pathlib import Path

from . import temp_workdir
from zhixianji.export import export_all, guard_formula
from zhixianji.store import Store
from zhixianji.window import CN_TZ, TimeWindow

FIXTURES = Path(__file__).resolve().parent / "fixtures"
AS_OF = datetime(2026, 10, 15, 19, 0, tzinfo=CN_TZ)


def load_list_items() -> list[dict]:
    payload = json.loads((FIXTURES / "list_page.json").read_text(encoding="utf-8"))
    window = TimeWindow("first_seen_at", 30, as_of=AS_OF)
    items = []
    for item in payload["jobs"]:
        decision = window.evaluate(item)
        item = dict(item)
        item["_window"] = {
            "in_window": decision.in_window,
            "reason": decision.reason,
            "value": decision.value,
        }
        item["_list_page"] = 1
        items.append(item)
    return items


class StoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_ctx = temp_workdir(); self.tmp = self._tmp_ctx.__enter__()
        self.store = Store(self.tmp / "t.db")
        self.batch_id = "20261015-1900-agent"
        self.store.create_batch(
            self.batch_id,
            "https://example.invalid",
            {"search": {"q": "agent"}, "collection": {}},
            TimeWindow("first_seen_at", 30, as_of=AS_OF).to_json(),
        )
        self.store.save_list_items(self.batch_id, load_list_items())

    def tearDown(self) -> None:
        self.store.close()
        self._tmp_ctx.__exit__(None, None, None)

    def test_window_marking_splits_the_fixture_rows(self) -> None:
        self.assertEqual(self.store.window_counts(self.batch_id), {"in_window": 3, "out_of_window": 1})

    def test_list_items_ordered_by_collected_time_desc(self) -> None:
        items = self.store.get_list_items(self.batch_id)
        self.assertEqual([i["source_job_id"] for i in items], ["EX-0001", "EX-0004", "EX-0002"])

    def test_out_of_window_row_keeps_its_reason(self) -> None:
        all_items = self.store.get_list_items(self.batch_id, in_window_only=False)
        old = [i for i in all_items if i["source_job_id"] == "EX-0003"][0]
        self.assertEqual(old["window_reason"], "before_window")
        self.assertEqual(old["first_seen_at"], "2026-08-03T18:04:35+08:00")

    def test_pending_details_starts_with_all_in_window_rows(self) -> None:
        self.assertEqual(len(self.store.pending_details(self.batch_id)), 3)

    def test_successful_detail_is_not_pending_again(self) -> None:
        self.store.save_detail(
            self.batch_id,
            {
                "source": "example",
                "source_job_id": "EX-0001",
                "status": "ok",
                "responsibilities": "职责",
                "qualifications": "要求",
                "jd_text": "岗位职责\n职责",
                "jd_chars": 10,
            },
        )
        pending = self.store.pending_details(self.batch_id)
        self.assertEqual([p["source_job_id"] for p in pending], ["EX-0004", "EX-0002"])

    def test_failed_detail_is_retried_only_when_asked(self) -> None:
        self.store.save_detail(
            self.batch_id,
            {"source": "example", "source_job_id": "EX-0001", "status": "mismatch", "error": "身份不符"},
        )
        self.assertEqual([p["source_job_id"] for p in self.store.pending_details(self.batch_id)], ["EX-0004", "EX-0002"])
        retried = self.store.pending_details(self.batch_id, retry_failed=True)
        self.assertEqual(sorted(p["source_job_id"] for p in retried), ["EX-0001", "EX-0002", "EX-0004"])

    def test_detail_status_counts(self) -> None:
        self.store.save_detail(self.batch_id, {"source": "example", "source_job_id": "EX-0001", "status": "ok"})
        self.store.save_detail(self.batch_id, {"source": "example2", "source_job_id": "EX-0002", "status": "missing"})
        self.assertEqual(self.store.detail_status_counts(self.batch_id), {"ok": 1, "missing": 1})

    def test_next_batch_id_avoids_collision(self) -> None:
        first = self.store.next_batch_id("agent")
        self.store.create_batch(first, "https://example.invalid", {}, {})
        self.assertNotEqual(self.store.next_batch_id("agent"), first)

    def test_batch_listing_reports_counts(self) -> None:
        row = self.store.list_batches()[0]
        self.assertEqual(row["batch_id"], self.batch_id)
        self.assertEqual(row["list_count"], 4)
        self.assertEqual(row["window_count"], 3)

    def test_rerunning_the_list_phase_updates_conditions_without_crashing(self) -> None:
        # 真实使用中会带着固定的 --as-of 重跑列表阶段；这必须是更新而不是报 UNIQUE 冲突。
        original_created = self.store.get_batch(self.batch_id)["created_at"]
        self.store.create_batch(
            self.batch_id,
            "https://other.invalid",
            {"search": {"q": "x"}},
            TimeWindow("published_at", 7, as_of=AS_OF).to_json(),
        )

        batch = self.store.get_batch(self.batch_id)
        self.assertEqual(batch["base_url"], "https://other.invalid")
        self.assertEqual(batch["created_at"], original_created, "批次创建时间不应被覆盖")
        self.assertEqual(json.loads(batch["window_json"])["days"], 7)
        self.assertEqual(len(self.store.list_batches()), 1, "重跑不应产生第二个批次")


class ExportTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_ctx = temp_workdir(); self.tmp = self._tmp_ctx.__enter__()
        self.store = Store(self.tmp / "t.db")
        self.batch_id = "20261015-1900-agent"
        self.store.create_batch(
            self.batch_id,
            "https://example.invalid",
            {"search": {"q": "agent"}, "collection": {}},
            TimeWindow("first_seen_at", 30, as_of=AS_OF).to_json(),
        )
        self.store.save_list_items(self.batch_id, load_list_items())
        self.store.save_detail(
            self.batch_id,
            {
                "source": "example",
                "source_job_id": "EX-0001",
                "status": "ok",
                "parse_source": "json-ld",
                "title_matches": 1,
                "responsibilities": "1. 维护示例工具链。\n2. 带,逗号与\"引号\"。",
                "qualifications": "=公式开头的要求",
                "jd_text": "岗位职责\n1. 维护示例工具链。",
                "jd_chars": 40,
                "fetched_at": "2026-10-15T19:10:00+08:00",
            },
        )
        self.out = self.tmp / "out"
        self.summary = export_all(self.store, self.batch_id, self.out)

    def tearDown(self) -> None:
        self.store.close()
        self._tmp_ctx.__exit__(None, None, None)

    def test_csv_has_bom_and_reads_back_with_embedded_newlines(self) -> None:
        raw = (self.out / "jobs.csv").read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        with (self.out / "jobs.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 3)  # 只导出窗口内记录
        first = [r for r in rows if r["source_job_id"] == "EX-0001"][0]
        self.assertIn("带,逗号与\"引号\"", first["responsibilities"])
        self.assertEqual(first["jd_status"], "ok")

    def test_formula_prefix_is_neutralised(self) -> None:
        self.assertEqual(guard_formula("=1+1"), "'=1+1")
        self.assertEqual(guard_formula("+86"), "'+86")
        self.assertEqual(guard_formula("@x"), "'@x")
        self.assertEqual(guard_formula("正常文本"), "正常文本")

    def test_jsonl_round_trip(self) -> None:
        lines = (self.out / "jobs.jsonl").read_text(encoding="utf-8").strip().split("\n")
        self.assertEqual(len(lines), 3)
        records = [json.loads(line) for line in lines]
        self.assertEqual(records[0]["first_seen_at"], "2026-10-12T09:30:00+08:00")

    def test_markdown_written_for_ok_jobs_only(self) -> None:
        files = sorted(p.name for p in (self.out / "jd").glob("*.md"))
        self.assertEqual(files, ["example__EX-0001.md"])
        text = (self.out / "jd" / files[0]).read_text(encoding="utf-8")
        self.assertIn("# 示例 Agent 后端工程师", text)
        self.assertIn("## 岗位职责", text)
        self.assertIn("## 职位要求", text)

    def test_summary_counts_and_window_bounds(self) -> None:
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["counts"]["inWindow"], 3)
        self.assertEqual(summary["counts"]["outOfWindow"], 1)
        self.assertEqual(summary["counts"]["jdOk"], 1)
        self.assertEqual(summary["detailStatus"], {"ok": 1})
        self.assertEqual(summary["detailParseSource"], {"json-ld": 1})
        self.assertEqual(summary["outOfWindowReasons"], {"before_window": 1})
        self.assertEqual(summary["window"]["startInclusive"], "2026-09-15T19:00:00+08:00")
        self.assertEqual(summary["sourcesInWindow"], {"example": 1, "example2": 1, "example4": 1})

    def test_match_scope_splits_title_hits_from_description_only(self) -> None:
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["counts"]["inWindowTitleHit"], 2)  # EX-0001、EX-0002
        self.assertEqual(summary["counts"]["inWindowDescriptionOnly"], 1)  # EX-0004
        self.assertEqual(summary["matchScope"]["titleHitMinScore"], 100)

        with (self.out / "jobs-title-hit.csv").open(encoding="utf-8-sig", newline="") as handle:
            title_rows = list(csv.DictReader(handle))
        self.assertEqual(sorted(r["source_job_id"] for r in title_rows), ["EX-0001", "EX-0002"])
        self.assertEqual({r["match_scope"] for r in title_rows}, {"title"})

        with (self.out / "jobs.csv").open(encoding="utf-8-sig", newline="") as handle:
            all_rows = list(csv.DictReader(handle))
        scopes = {r["source_job_id"]: r["match_scope"] for r in all_rows}
        self.assertEqual(scopes["EX-0004"], "description")

    def test_title_hit_markdown_only_covers_title_rows(self) -> None:
        files = sorted(p.name for p in (self.out / "jd-title-hit").glob("*.md"))
        self.assertEqual(files, ["example__EX-0001.md"])
        self.assertEqual(self.summary["outputs"]["titleHitMarkdownFiles"], 1)
        self.assertEqual(self.summary["counts"]["titleHitRows"], 2)

    def test_markdown_count_reported_in_summary(self) -> None:
        self.assertEqual(self.summary["outputs"]["markdownFiles"], 1)

    def test_not_fetched_jobs_are_exported_with_status(self) -> None:
        with (self.out / "jobs.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        second = [r for r in rows if r["source_job_id"] == "EX-0002"][0]
        self.assertEqual(second["jd_status"], "not_fetched")


if __name__ == "__main__":
    unittest.main()
