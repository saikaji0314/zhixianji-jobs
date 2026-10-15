"""详情抓取测试：身份核对、失败分类、不能把别的岗位正文写成目标岗位。"""

from __future__ import annotations

import unittest
from pathlib import Path

from . import temp_workdir
from zhixianji.detail import fetch_detail
from zhixianji.http_client import FetchError, Response

FIXTURES = Path(__file__).resolve().parent / "fixtures"

ITEM = {
    "source": "example",
    "source_job_id": "EX-0001",
    "title": "示例 Agent 后端工程师",
    "public_path": "/jobs/example/EX-0001",
}


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeClient:
    def __init__(self, response: Response | Exception) -> None:
        self.response = response
        self.urls: list[str] = []

    def get(self, url: str, accept: str = "application/json") -> Response:
        self.urls.append(url)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def html_response(html: str, status: int = 200) -> Response:
    return Response(url="x", status=status, text=html, headers={})


class HappyPathTest(unittest.TestCase):
    def test_ok_record(self) -> None:
        client = FakeClient(html_response(load("detail_with_jsonld.html")))
        record = fetch_detail(client, "https://example.invalid", ITEM)
        self.assertEqual(record["status"], "ok")
        self.assertEqual(record["title_matches"], 1)
        self.assertGreater(record["jd_chars"], 0)
        self.assertEqual(record["http_status"], 200)
        self.assertTrue(record["content_sha256"])
        self.assertEqual(client.urls, ["https://example.invalid/jobs/example/EX-0001"])

    def test_raw_html_is_written_when_dir_given(self) -> None:
        
        with temp_workdir() as tmp:
            client = FakeClient(html_response(load("detail_with_jsonld.html")))
            record = fetch_detail(client, "https://example.invalid", ITEM, raw_dir=Path(tmp))
            self.assertTrue(record["raw_path"])
            self.assertTrue(Path(record["raw_path"]).is_file())


class IdentityTest(unittest.TestCase):
    def test_mismatched_id_is_rejected_and_jd_cleared(self) -> None:
        # 目标 EX-0009，但页面是 EX-0001：不能把 EX-0001 的正文写成 EX-0009 的 JD。
        item = dict(ITEM, source_job_id="EX-0009", public_path="/jobs/example/EX-0009")
        client = FakeClient(html_response(load("detail_with_jsonld.html")))
        record = fetch_detail(client, "https://example.invalid", item)
        self.assertEqual(record["status"], "mismatch")
        self.assertEqual(record["jd_text"], "")
        self.assertEqual(record["responsibilities"], "")
        self.assertEqual(record["jd_chars"], 0)
        self.assertIn("与目标不一致", record["error"])

    def test_missing_identity_attributes_is_unverified(self) -> None:
        html = '<html><body><main><h1 id="job-title">只有标题</h1></main></body></html>'
        client = FakeClient(html_response(html))
        record = fetch_detail(client, "https://example.invalid", ITEM)
        self.assertEqual(record["status"], "unverified")
        self.assertEqual(record["jd_text"], "")

    def test_title_difference_is_recorded_without_discarding_jd(self) -> None:
        item = dict(ITEM, title="完全不同的标题")
        client = FakeClient(html_response(load("detail_with_jsonld.html")))
        record = fetch_detail(client, "https://example.invalid", item)
        self.assertEqual(record["status"], "ok")
        self.assertEqual(record["title_matches"], 0)
        self.assertGreater(record["jd_chars"], 0)


class FailureTest(unittest.TestCase):
    def test_404_is_missing(self) -> None:
        client = FakeClient(html_response("", status=404))
        record = fetch_detail(client, "https://example.invalid", ITEM)
        self.assertEqual(record["status"], "missing")
        self.assertEqual(record["http_status"], 404)

    def test_server_error_is_error(self) -> None:
        client = FakeClient(html_response("boom", status=503))
        record = fetch_detail(client, "https://example.invalid", ITEM)
        self.assertEqual(record["status"], "error")
        self.assertIn("503", record["error"])

    def test_network_failure_is_error_not_exception(self) -> None:
        client = FakeClient(FetchError("x", "连接被拒绝"))
        record = fetch_detail(client, "https://example.invalid", ITEM)
        self.assertEqual(record["status"], "error")
        self.assertIn("连接被拒绝", record["error"])

    def test_missing_one_section_is_incomplete_but_keeps_the_other(self) -> None:
        html = load("detail_no_jsonld.html").replace("职位要求", "其他说明").replace(
            'id="job-requirements"', 'id="job-other"'
        )
        client = FakeClient(html_response(html))
        record = fetch_detail(client, "https://example.invalid", dict(ITEM, source="example2", source_job_id="EX-0002"))
        self.assertEqual(record["status"], "incomplete")
        self.assertIn("职位要求", record["error"])
        self.assertIn("负责示例平台的能力建设", record["responsibilities"])


if __name__ == "__main__":
    unittest.main()
