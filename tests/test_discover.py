"""列表发现测试：翻页、去重、上限与列表变动提示（全部离线）。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from . import temp_workdir
from zhixianji.discover import DiscoverError, discover, fetch_page
from zhixianji.http_client import Response


def page_payload(jobs: list[dict], page: int, pages: int, total: int, page_size: int = 60) -> str:
    return json.dumps({"total": total, "page": page, "pages": pages, "page_size": page_size, "jobs": jobs})


def job(source: str, job_id: str, first_seen: str = "2026-10-12T09:30:00+08:00") -> dict:
    return {
        "source": source,
        "source_job_id": job_id,
        "title": f"岗位 {job_id}",
        "first_seen_at": first_seen,
        "public_path": f"/jobs/{source}/{job_id}",
    }


class FakeClient:
    """按 URL 里的 page 参数返回预置页面。"""

    def __init__(self, pages: dict[int, str]) -> None:
        self.pages = pages
        self.urls: list[str] = []

    def get(self, url: str, accept: str = "application/json") -> Response:
        self.urls.append(url)
        page = int(url.split("page=")[1].split("&")[0])
        body = self.pages.get(page)
        if body is None:
            return Response(url=url, status=404, text="", headers={})
        return Response(url=url, status=200, text=body, headers={})


class PagingTest(unittest.TestCase):
    def test_query_string_mirrors_the_site_and_carries_page(self) -> None:
        client = FakeClient({1: page_payload([job("a", "1")], 1, 1, 1)})
        payload = fetch_page(client, "https://example.invalid", "job_type=social&status=active&q=agent", 1)
        self.assertEqual(payload["total"], 1)
        self.assertEqual(
            client.urls[0],
            "https://example.invalid/api/jobs?job_type=social&status=active&q=agent&page=1",
        )

    def test_walks_every_page(self) -> None:
        client = FakeClient(
            {
                1: page_payload([job("a", "1"), job("a", "2")], 1, 2, 3),
                2: page_payload([job("b", "3")], 2, 2, 3),
            }
        )
        result = discover(client, "https://example.invalid", "q=agent")
        self.assertEqual([i["source_job_id"] for i in result.items], ["1", "2", "3"])
        self.assertEqual(result.pages_fetched, 2)
        self.assertEqual(result.total_reported, 3)

    def test_duplicate_rows_across_pages_are_collapsed_but_counted(self) -> None:
        client = FakeClient(
            {
                1: page_payload([job("a", "1"), job("a", "2")], 1, 2, 3),
                2: page_payload([job("a", "2"), job("b", "3")], 2, 2, 3),
            }
        )
        result = discover(client, "https://example.invalid", "q=agent")
        self.assertEqual([i["source_job_id"] for i in result.items], ["1", "2", "3"])
        self.assertEqual(result.duplicate_count, 1)

    def test_same_id_from_different_sources_is_not_a_duplicate(self) -> None:
        client = FakeClient({1: page_payload([job("a", "1"), job("b", "1")], 1, 1, 2)})
        result = discover(client, "https://example.invalid", "q=agent")
        self.assertEqual(len(result.items), 2)
        self.assertEqual(result.duplicate_count, 0)

    def test_row_without_identity_is_skipped_with_warning(self) -> None:
        client = FakeClient({1: page_payload([job("a", "1"), {"title": "缺少来源"}], 1, 1, 2)})
        result = discover(client, "https://example.invalid", "q=agent")
        self.assertEqual(len(result.items), 1)
        self.assertTrue(any("缺少 source" in w for w in result.warnings))

    def test_max_jobs_stops_early_and_says_so(self) -> None:
        client = FakeClient(
            {
                1: page_payload([job("a", "1"), job("a", "2")], 1, 3, 6),
                2: page_payload([job("a", "3"), job("a", "4")], 2, 3, 6),
                3: page_payload([job("a", "5"), job("a", "6")], 3, 3, 6),
            }
        )
        result = discover(client, "https://example.invalid", "q=agent", max_jobs=3)
        self.assertEqual(len(result.items), 3)
        self.assertTrue(any("maxJobs" in w for w in result.warnings))

    def test_max_pages_stops_early_and_says_so(self) -> None:
        client = FakeClient({p: page_payload([job("a", str(p))], p, 3, 3) for p in (1, 2, 3)})
        result = discover(client, "https://example.invalid", "q=agent", max_pages=2)
        self.assertEqual(len(result.items), 2)
        self.assertTrue(any("maxPages" in w for w in result.warnings))

    def test_total_change_during_paging_is_reported(self) -> None:
        client = FakeClient(
            {
                1: page_payload([job("a", "1")], 1, 2, 2),
                2: page_payload([job("a", "2")], 2, 2, 3),
            }
        )
        result = discover(client, "https://example.invalid", "q=agent")
        self.assertTrue(any("total" in w for w in result.warnings))

    def test_raw_pages_are_archived(self) -> None:
        client = FakeClient({1: page_payload([job("a", "1")], 1, 1, 1)})
        with temp_workdir() as tmp:
            discover(client, "https://example.invalid", "q=agent", raw_dir=Path(tmp))
            saved = json.loads((Path(tmp) / "page-001.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["jobs"][0]["source_job_id"], "1")

    def test_non_json_response_raises(self) -> None:
        client = FakeClient({1: "<html>不是 JSON</html>"})
        with self.assertRaises(DiscoverError):
            discover(client, "https://example.invalid", "q=agent")

    def test_missing_jobs_key_raises(self) -> None:
        client = FakeClient({1: json.dumps({"total": 0})})
        with self.assertRaises(DiscoverError):
            discover(client, "https://example.invalid", "q=agent")

    def test_unknown_page_404_propagates_as_fetch_error(self) -> None:
        from zhixianji.http_client import FetchError

        client = FakeClient({1: page_payload([job("a", "1")], 1, 3, 3)})
        with self.assertRaises(FetchError):
            discover(client, "https://example.invalid", "q=agent")


if __name__ == "__main__":
    unittest.main()
