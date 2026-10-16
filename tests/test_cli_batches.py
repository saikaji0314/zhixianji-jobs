"""batches 命令 --limit 的离线测试。

覆盖：默认列出全部、limit 小于/大于批次数、空库、非法参数（0／负数／非整数），
全部使用虚构批次数据，不访问任何站点。非法参数必须在打开数据库之前被拒绝。
"""

from __future__ import annotations

import contextlib
import io
import unittest
from pathlib import Path

from . import temp_workdir
from zhixianji.cli import main
from zhixianji.store import Store

# (批次号, 创建时间) 使用明确虚构的数据，创建时间互不相同以便断言倒序。
BATCHES = [
    ("20261001-0900-alpha", "2026-10-01T09:00:00+08:00"),
    ("20261002-0900-beta", "2026-10-02T09:00:00+08:00"),
    ("20261003-0900-gamma", "2026-10-03T09:00:00+08:00"),
]
EXPECTED_DESC = [batch_id for batch_id, _ in reversed(BATCHES)]


def seed_batches(store: Store, rows: list[tuple[str, str]] = BATCHES) -> None:
    for batch_id, created_at in rows:
        store.create_batch(batch_id, "https://example.invalid", {"search": {"q": "虚构关键词"}}, {})
        # create_batch 用运行时刻写入 created_at（秒级），这里固定为显式时间以保证排序可断言。
        store.conn.execute("UPDATE batches SET created_at = ? WHERE batch_id = ?", (created_at, batch_id))
    store.conn.commit()


class ListBatchesLimitTest(unittest.TestCase):
    """存储查询层：限制在 SQL 中完成，排序仍为创建时间倒序。"""

    def setUp(self) -> None:
        self._tmp_ctx = temp_workdir()
        self.tmp = self._tmp_ctx.__enter__()
        self.store = Store(self.tmp / "t.db")
        seed_batches(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self._tmp_ctx.__exit__(None, None, None)

    def test_default_returns_all_in_desc_order(self) -> None:
        ids = [b["batch_id"] for b in self.store.list_batches()]
        self.assertEqual(ids, EXPECTED_DESC)

    def test_limit_smaller_than_count_returns_most_recent(self) -> None:
        ids = [b["batch_id"] for b in self.store.list_batches(limit=2)]
        self.assertEqual(ids, EXPECTED_DESC[:2])

    def test_limit_equal_to_count_returns_all(self) -> None:
        ids = [b["batch_id"] for b in self.store.list_batches(limit=3)]
        self.assertEqual(ids, EXPECTED_DESC)

    def test_limit_larger_than_count_returns_all(self) -> None:
        ids = [b["batch_id"] for b in self.store.list_batches(limit=10)]
        self.assertEqual(ids, EXPECTED_DESC)

    def test_counts_are_preserved_with_limit(self) -> None:
        row = self.store.list_batches(limit=1)[0]
        self.assertEqual(row["batch_id"], EXPECTED_DESC[0])
        self.assertIn("list_count", row)


class EmptyDatabaseTest(unittest.TestCase):
    def test_empty_database_returns_no_batches(self) -> None:
        with temp_workdir() as tmp, Store(tmp / "empty.db") as store:
            self.assertEqual(store.list_batches(), [])
            self.assertEqual(store.list_batches(limit=5), [])


class BatchesCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_ctx = temp_workdir()
        self.tmp = self._tmp_ctx.__enter__()
        self.db = self.tmp / "cli.db"
        self.out = self.tmp / "out"
        with Store(self.db) as store:
            seed_batches(store)

    def tearDown(self) -> None:
        self._tmp_ctx.__exit__(None, None, None)

    def run_cli(self, *extra: str) -> tuple[int, str]:
        argv = ["--db", str(self.db), "--output", str(self.out), "batches", *extra]
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(argv)
        return code, buffer.getvalue()

    @staticmethod
    def rows(output: str) -> list[str]:
        lines = output.splitlines()
        return [line for line in lines[1:] if line.strip()]

    def test_default_lists_every_batch(self) -> None:
        code, output = self.run_cli()
        self.assertEqual(code, 0)
        self.assertEqual(len(self.rows(output)), 3)
        self.assertIn(EXPECTED_DESC[0], output)

    def test_limit_returns_only_the_most_recent(self) -> None:
        code, output = self.run_cli("--limit", "2")
        self.assertEqual(code, 0)
        rows = self.rows(output)
        self.assertEqual(len(rows), 2)
        self.assertIn(EXPECTED_DESC[0], rows[0])
        self.assertIn(EXPECTED_DESC[1], rows[1])
        self.assertNotIn(EXPECTED_DESC[2], output)

    def test_limit_larger_than_count_lists_all(self) -> None:
        code, output = self.run_cli("--limit", "99")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.rows(output)), 3)

    def test_empty_database_prints_hint(self) -> None:
        empty_db = self.tmp / "no-batches.db"
        buffer = io.StringIO()
        argv = ["--db", str(empty_db), "--output", str(self.out), "batches"]
        with contextlib.redirect_stdout(buffer):
            code = main(argv)
        self.assertEqual(code, 0)
        self.assertIn("还没有批次", buffer.getvalue())

    def test_illegal_values_are_rejected_without_creating_files(self) -> None:
        for value in ("0", "-1", "abc", "2.5", ""):
            with self.subTest(value=value):
                rejected_db = self.tmp / f"rejected-{value or 'empty'}.db"
                rejected_out = self.tmp / "rejected-out"
                argv = ["--db", str(rejected_db), "--output", str(rejected_out), "batches", "--limit", value]
                stderr = io.StringIO()
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as ctx:
                        main(argv)
                self.assertEqual(ctx.exception.code, 2, stderr.getvalue())
                self.assertFalse(rejected_db.exists(), "非法 --limit 不应创建数据库")
                self.assertFalse(rejected_out.exists(), "非法 --limit 不应创建输出目录")


if __name__ == "__main__":
    unittest.main()
