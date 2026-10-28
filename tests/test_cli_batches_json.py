"""batches --json 输出契约：只读本地库、单数组、六字段、文本模式不变。"""

from __future__ import annotations

import contextlib
import io
import json
import unittest

from . import temp_workdir
from zhixianji.cli import build_parser, main
from zhixianji.store import Store

FIELDS = {"batch_id", "created_at", "list_count", "window_count", "ok_count", "search"}


def run_cli(db_path, *args: str) -> tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main(["--db", str(db_path), *args])
    return code, buffer.getvalue()


def add_list_items(store: Store, batch_id: str, in_window_flags: list[bool]) -> None:
    items = [
        {
            "source": "example",
            "source_job_id": f"J-{index}",
            "_window": {"in_window": flag},
            "_list_page": 1,
        }
        for index, flag in enumerate(in_window_flags, start=1)
    ]
    store.save_list_items(batch_id, items)


class BatchesJsonTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_ctx = temp_workdir()
        self.tmp = self._tmp_ctx.__enter__()
        self.db = self.tmp / "t.db"

    def tearDown(self) -> None:
        self._tmp_ctx.__exit__(None, None, None)

    def test_empty_database_outputs_exactly_an_empty_array(self) -> None:
        code, out = run_cli(self.db, "batches", "--json")

        self.assertEqual(code, 0)
        self.assertEqual(out, "[]\n")
        self.assertEqual(json.loads(out), [])

    def test_json_items_have_exactly_six_fields_with_numeric_counts_and_chinese_search(self) -> None:
        batch_id = "20261015-1900-agent"
        with Store(self.db) as store:
            store.create_batch(
                batch_id,
                "https://example.invalid",
                {"search": {"q": "人工智能", "jobType": "social"}, "collection": {}},
                {"field": "first_seen_at", "days": 30},
            )
            add_list_items(store, batch_id, [True, True, False])
            store.save_detail(batch_id, {"source": "example", "source_job_id": "J-1", "status": "ok"})

        code, out = run_cli(self.db, "batches", "--json")
        payload = json.loads(out)

        self.assertEqual(code, 0)
        self.assertEqual(len(payload), 1)
        item = payload[0]
        self.assertEqual(set(item), FIELDS)
        self.assertEqual(item["batch_id"], batch_id)
        self.assertIsInstance(item["list_count"], int)
        self.assertIsInstance(item["window_count"], int)
        self.assertIsInstance(item["ok_count"], int)
        self.assertEqual(item["list_count"], 3)
        self.assertEqual(item["window_count"], 2)
        self.assertEqual(item["ok_count"], 1)
        self.assertEqual(item["search"], {"q": "人工智能", "jobType": "social"})
        # ensure_ascii=False：中文以 UTF-8 原样写出，可直接阅读。
        self.assertIn("人工智能", out)
        self.assertNotIn("\\u4eba", out)

    def test_missing_search_is_an_empty_object(self) -> None:
        with Store(self.db) as store:
            store.create_batch("batch-no-search", "https://example.invalid", {}, {})

        code, out = run_cli(self.db, "batches", "--json")
        payload = json.loads(out)

        self.assertEqual(code, 0)
        self.assertEqual(set(payload[0]), FIELDS)
        self.assertEqual(payload[0]["search"], {})

    def test_json_rows_follow_created_at_desc(self) -> None:
        with Store(self.db) as store:
            store.create_batch("older", "https://example.invalid", {"search": {"q": "旧"}}, {})
            store.create_batch("newer", "https://example.invalid", {"search": {"q": "新"}}, {})
            store.conn.execute(
                "UPDATE batches SET created_at = ? WHERE batch_id = ?",
                ("2026-10-15T19:00:00+08:00", "older"),
            )
            store.conn.execute(
                "UPDATE batches SET created_at = ? WHERE batch_id = ?",
                ("2026-10-16T10:00:00+08:00", "newer"),
            )
            store.conn.commit()

        code, out = run_cli(self.db, "batches", "--json")
        payload = json.loads(out)

        self.assertEqual(code, 0)
        self.assertEqual([item["batch_id"] for item in payload], ["newer", "older"])
        self.assertEqual([item["created_at"] for item in payload], ["2026-10-16T10:00:00+08:00", "2026-10-15T19:00:00+08:00"])

    def test_default_text_output_is_unchanged(self) -> None:
        with Store(self.db) as store:
            store.create_batch(
                "20261015-1900-agent",
                "https://example.invalid",
                {"search": {"q": "agent"}},
                {},
            )

        code, out = run_cli(self.db, "batches")
        lines = out.splitlines()

        self.assertEqual(code, 0)
        self.assertIn("批次", lines[0])
        self.assertIn("20261015-1900-agent", lines[1])
        self.assertIn('{"q": "agent"}', lines[1])
        self.assertNotIn("[", out)
        self.assertFalse(out.lstrip().startswith("{"))

    def test_text_output_keeps_empty_database_hint(self) -> None:
        code, out = run_cli(self.db, "batches")

        self.assertEqual(code, 0)
        self.assertEqual(out, "还没有批次。先运行：python run.py discover --config config/search.local.json\n")

    def test_batches_help_mentions_json(self) -> None:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            with self.assertRaises(SystemExit) as raised:
                build_parser().parse_args(["batches", "--help"])

        self.assertEqual(raised.exception.code, 0)
        self.assertIn("--json", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
