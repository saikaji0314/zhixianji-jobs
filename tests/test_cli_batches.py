"""`batches` 列表分页的离线测试。

覆盖：SQL 层排序与 LIMIT/OFFSET、只给 offset、跨页不重不漏、默认表格输出兼容、
空页退出 0、非法参数在打开数据库前以退出码 2 拒绝且不打印 Traceback。
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from . import REPO_ROOT, temp_workdir
from zhixianji.store import Store

RUN_PY = REPO_ROOT / "run.py"

SAME_SECOND = "2026-10-29T10:00:00+08:00"
OLDER = "2026-10-28T09:00:00+08:00"

# 同一秒内创建，顺序只能靠 batch_id 降序稳定区分。
SEED = [
    ("20261029-1000-alpha", SAME_SECOND),
    ("20261029-1000-gamma", SAME_SECOND),
    ("20261029-1000-beta", SAME_SECOND),
    ("20261028-0900-older", OLDER),
]
EXPECTED_ORDER = ["20261029-1000-gamma", "20261029-1000-beta", "20261029-1000-alpha", "20261028-0900-older"]


def seed_store(store: Store) -> None:
    for batch_id, created_at in SEED:
        store.create_batch(
            batch_id,
            "https://example.invalid",
            {"search": {"q": "智能体", "job_type": "social"}, "collection": {}},
            {},
        )
        # 固定创建时间，避免依赖 now_iso() 的秒级时钟。
        store.conn.execute("UPDATE batches SET created_at = ? WHERE batch_id = ?", (created_at, batch_id))
    store.conn.commit()
    store.save_list_items(
        "20261029-1000-gamma",
        [{"source": "example", "source_job_id": "J1", "title": "示例", "_window": {"in_window": True}}],
    )
    store.save_detail("20261029-1000-gamma", {"source": "example", "source_job_id": "J1", "status": "ok"})


def run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUN_PY), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def batch_ids(stdout: str) -> list[str]:
    """从文本表格中取第一列批次号；表头行不以日期开头。"""
    ids = []
    for line in stdout.splitlines():
        first = line.split(maxsplit=1)[0] if line.split() else ""
        if first[:8].isdigit():
            ids.append(first)
    return ids


class BatchPaginationStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_ctx = temp_workdir()
        self.tmp = self._tmp_ctx.__enter__()
        self.store = Store(self.tmp / "t.db")
        seed_store(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self._tmp_ctx.__exit__(None, None, None)

    def test_default_call_returns_all_rows_with_stable_order(self) -> None:
        rows = self.store.list_batches()
        self.assertEqual(
            [b["batch_id"] for b in rows],
            EXPECTED_ORDER,
            "created_at 降序、同秒按 batch_id 降序",
        )

    def test_pages_concatenate_without_gaps_or_duplicates(self) -> None:
        paged = []
        for offset in range(0, len(EXPECTED_ORDER) + 1, 2):
            paged.extend(b["batch_id"] for b in self.store.list_batches(limit=2, offset=offset))
        self.assertEqual(paged, EXPECTED_ORDER)

    def test_limit_with_offset_matches_slice(self) -> None:
        page = self.store.list_batches(limit=2, offset=1)
        self.assertEqual([b["batch_id"] for b in page], EXPECTED_ORDER[1:3])

    def test_offset_only_returns_the_remainder(self) -> None:
        rest = self.store.list_batches(offset=2)
        self.assertEqual([b["batch_id"] for b in rest], EXPECTED_ORDER[2:])

    def test_offset_beyond_total_is_empty(self) -> None:
        self.assertEqual(self.store.list_batches(limit=10, offset=99), [])

    def test_counts_are_preserved_for_partial_rows(self) -> None:
        row = self.store.list_batches(limit=1)[0]
        self.assertEqual((row["list_count"], row["window_count"], row["ok_count"]), (1, 1, 1))


class BatchCliPaginationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_ctx = temp_workdir()
        self.tmp = self._tmp_ctx.__enter__()
        self.db = self.tmp / "t.db"
        with Store(self.db) as store:
            seed_store(store)

    def tearDown(self) -> None:
        self._tmp_ctx.__exit__(None, None, None)

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return run_cli(["--db", str(self.db), "batches", *args], self.tmp)

    def test_default_output_keeps_header_counts_and_chinese_search(self) -> None:
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("批次", result.stdout)
        self.assertIn("列表", result.stdout)
        self.assertIn("窗口内", result.stdout)
        self.assertIn("含完整JD", result.stdout)
        self.assertIn("智能体", result.stdout, "search JSON 应保持中文，不以 \\u 转义")
        self.assertNotIn("\\u667a", result.stdout)
        self.assertIn("     1       1         1", result.stdout)
        self.assertEqual(batch_ids(result.stdout), EXPECTED_ORDER)

    def test_cli_pages_concatenate_to_full_list(self) -> None:
        collected = []
        for offset in (0, 2, 4):
            result = self._run("--limit", "2", "--offset", str(offset))
            self.assertEqual(result.returncode, 0, result.stderr)
            collected.extend(batch_ids(result.stdout))
        self.assertEqual(collected, EXPECTED_ORDER)

    def test_offset_only_returns_remainder(self) -> None:
        result = self._run("--offset", "3")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(batch_ids(result.stdout), EXPECTED_ORDER[3:])

    def test_empty_page_exits_zero_without_traceback(self) -> None:
        result = self._run("--offset", "99")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_invalid_arguments_exit_two_before_creating_database(self) -> None:
        cases = [
            ("--limit", "0"),
            ("--limit", "1001"),
            ("--limit", "abc"),
            ("--offset", "-1"),
            ("--offset", "1000001"),
            ("--offset", "abc"),
        ]
        for flag, value in cases:
            with self.subTest(flag=flag, value=value):
                missing = self.tmp / f"missing-{flag.lstrip('-')}-{value.lstrip('-')}" / "nested"
                db_path = missing / "x.db"
                result = run_cli(["--db", str(db_path), "batches", flag, value], self.tmp)
                self.assertEqual(result.returncode, 2, f"{flag} {value}: {result.stderr}")
                self.assertEqual(result.stdout, "", "错误信息只能写 stderr")
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(missing.exists(), "参数校验必须发生在创建数据库父目录之前")
                self.assertFalse(db_path.exists())


if __name__ == "__main__":
    unittest.main()
