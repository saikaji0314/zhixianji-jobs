"""摘要合并行为的测试：后一阶段导出不能丢掉前一阶段写入的信息。"""

from __future__ import annotations

import json
import unittest

from . import temp_workdir
from zhixianji.export import write_summary
from zhixianji.store import Store
from zhixianji.window import TimeWindow
from datetime import datetime

from zhixianji.window import CN_TZ


class SummaryMergeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_ctx = temp_workdir()
        self.tmp = self._tmp_ctx.__enter__()
        self.store = Store(self.tmp / "t.db")
        self.batch_id = "20261015-1910-agent"
        self.store.create_batch(
            self.batch_id,
            "https://example.invalid",
            {"search": {"q": "agent"}, "collection": {}},
            TimeWindow("first_seen_at", 30, as_of=datetime(2026, 10, 15, 19, 0, tzinfo=CN_TZ)).to_json(),
        )
        self.path = self.tmp / "summary.json"

    def tearDown(self) -> None:
        self.store.close()
        self._tmp_ctx.__exit__(None, None, None)

    def test_list_phase_extra_survives_a_later_export(self) -> None:
        first = write_summary(
            self.store, self.batch_id, self.path, extra={"listRun": {"pagesFetched": 80, "duplicates": 0}}
        )
        self.assertEqual(first["listRun"]["pagesFetched"], 80)

        second = write_summary(self.store, self.batch_id, self.path)
        self.assertEqual(second["listRun"]["pagesFetched"], 80, "列表阶段信息被覆盖丢失")
        self.assertEqual(second["counts"]["inWindow"], 0)

    def test_recomputed_counts_win_over_stale_values(self) -> None:
        write_summary(self.store, self.batch_id, self.path, extra={"counts": {"inWindow": 999}})
        # 传进来的 extra 覆盖同名键，属于调用方显式指定，应生效
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["counts"]["inWindow"], 999)

        merged = write_summary(self.store, self.batch_id, self.path)
        self.assertEqual(merged["counts"]["inWindow"], 0)

    def test_broken_previous_summary_does_not_crash(self) -> None:
        self.path.write_text("{ 坏掉的 JSON", encoding="utf-8")
        summary = write_summary(self.store, self.batch_id, self.path)
        self.assertEqual(summary["batchId"], self.batch_id)


if __name__ == "__main__":
    unittest.main()
