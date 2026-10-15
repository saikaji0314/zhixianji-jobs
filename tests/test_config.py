"""配置校验测试：未知参数、必填项、取值范围、默认值。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from . import temp_workdir
from zhixianji.config import ConfigError, load_config

EXAMPLE = Path(__file__).resolve().parents[1] / "config" / "search.example.json"


class LoadConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_ctx = temp_workdir()
        self.tmp = self._tmp_ctx.__enter__()

    def tearDown(self) -> None:
        self._tmp_ctx.__exit__(None, None, None)

    def write_config(self, payload: dict, name: str = "search.json") -> Path:
        path = self.tmp / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_reads_the_example_config(self) -> None:
        cfg = load_config(EXAMPLE)
        self.assertEqual(cfg.base_url, "https://jobs.ultraai.site")
        self.assertEqual(cfg.search["q"], "agent")
        self.assertEqual(cfg.search["job_type"], "social")
        self.assertEqual(cfg.search["status"], "active")
        self.assertEqual(cfg.window.field, "first_seen_at")
        self.assertEqual(cfg.window.days, 30)

    def test_search_query_is_url_encoded(self) -> None:
        cfg = load_config(self.write_config({"search": {"q": "大模型 agent", "status": "active"}}))
        self.assertIn("q=%E5%A4%A7%E6%A8%A1%E5%9E%8B+agent", cfg.search_query())

    def test_unknown_search_param_is_rejected(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            load_config(self.write_config({"search": {"q": "agent", "salery": "20"}}))
        self.assertIn("未知参数", str(ctx.exception))

    def test_empty_keyword_is_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            load_config(self.write_config({"search": {"q": "  "}}))

    def test_empty_optional_param_is_dropped_not_sent(self) -> None:
        cfg = load_config(self.write_config({"search": {"q": "agent", "location": "", "source": None}}))
        self.assertNotIn("location", cfg.search)
        self.assertNotIn("source", cfg.search)

    def test_window_field_must_be_known(self) -> None:
        with self.assertRaises(ConfigError):
            load_config(self.write_config({"search": {"q": "a"}, "window": {"field": "created_when"}}))

    def test_window_days_range(self) -> None:
        for days in (0, -1, 5000):
            with self.assertRaises(ConfigError):
                load_config(self.write_config({"search": {"q": "a"}, "window": {"days": days}}))

    def test_collection_overrides(self) -> None:
        cfg = load_config(
            self.write_config({"search": {"q": "a"}, "collection": {"maxPages": 5, "detailDelayMs": 0}})
        )
        self.assertEqual(cfg.collection.max_pages, 5)
        self.assertEqual(cfg.collection.detail_delay_ms, 0)

    def test_collection_range_is_checked(self) -> None:
        with self.assertRaises(ConfigError):
            load_config(self.write_config({"search": {"q": "a"}, "collection": {"maxRetries": 99}}))

    def test_broken_json_reports_the_path(self) -> None:
        path = self.tmp / "broken.json"
        path.write_text("{ not json", encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx:
            load_config(path)
        self.assertIn("不是合法 JSON", str(ctx.exception))

    def test_missing_file_is_reported(self) -> None:
        with self.assertRaises(ConfigError):
            load_config(self.tmp / "不存在的配置.json")


if __name__ == "__main__":
    unittest.main()
