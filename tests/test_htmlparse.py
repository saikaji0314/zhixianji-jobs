"""详情页解析测试：结构化路径、回退路径、身份核对、正文不混入推荐位。"""

from __future__ import annotations

import unittest
from pathlib import Path

from zhixianji.htmlparse import parse_detail, parse_html

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class MiniDomTest(unittest.TestCase):
    def test_find_all_by_class_and_id(self) -> None:
        root = parse_html(load("detail_no_jsonld.html"))
        self.assertEqual(len(root.find_all("section", cls="dossier-section")), 3)
        self.assertIsNotNone(root.find(node_id="job-title"))

    def test_block_text_keeps_separate_lines(self) -> None:
        root = parse_html("<div><p>第一行</p><p>第二行</p></div>")
        self.assertEqual(root.text(), "第一行\n第二行")

    def test_script_content_is_not_text(self) -> None:
        root = parse_html('<div><script>var x = "噪声";</script><p>正文</p></div>')
        self.assertEqual(root.text(), "正文")


class ParseWithJsonLdTest(unittest.TestCase):
    def setUp(self) -> None:
        self.parsed = parse_detail(load("detail_with_jsonld.html"))

    def test_prefers_structured_data(self) -> None:
        self.assertEqual(self.parsed["parse_source"], "json-ld")

    def test_core_fields(self) -> None:
        self.assertEqual(self.parsed["title"], "示例 Agent 后端工程师")
        self.assertEqual(self.parsed["company"], "示例科技")
        self.assertEqual(self.parsed["location"], "示例市")
        self.assertEqual(self.parsed["date_posted"], "2026-10-10")
        self.assertEqual(self.parsed["identifier_value"], "example:EX-0001")

    def test_hero_identity_attributes(self) -> None:
        self.assertEqual(self.parsed["hero"]["source"], "example")
        self.assertEqual(self.parsed["hero"]["source_job_id"], "EX-0001")
        self.assertEqual(self.parsed["hero"]["status"], "active")

    def test_collected_time_comes_from_time_element(self) -> None:
        self.assertEqual(self.parsed["site_collected_at"], "2026-10-12T09:30:00+08:00")
        self.assertEqual(self.parsed["site_collected_text"], "2026-10-12 09:30")

    def test_responsibilities_and_qualifications_separated(self) -> None:
        self.assertIn("维护示例工具链", self.parsed["responsibilities"])
        self.assertIn("熟悉 Python", self.parsed["qualifications"])
        self.assertNotIn("熟悉 Python", self.parsed["responsibilities"])

    def test_jd_text_carries_both_headings(self) -> None:
        self.assertIn("岗位职责", self.parsed["jd_text"])
        self.assertIn("职位要求", self.parsed["jd_text"])

    def test_recommendations_are_not_mixed_into_jd(self) -> None:
        self.assertNotIn("不相关的相似岗位", self.parsed["jd_text"])
        self.assertNotIn("不相关的相似岗位", self.parsed["responsibilities"])


class ParseWithoutJsonLdTest(unittest.TestCase):
    def setUp(self) -> None:
        self.parsed = parse_detail(load("detail_no_jsonld.html"))

    def test_falls_back_to_html_sections(self) -> None:
        self.assertEqual(self.parsed["parse_source"], "html")

    def test_reads_hero_and_prose(self) -> None:
        self.assertEqual(self.parsed["title"], "示例 Agent 平台工程师")
        self.assertEqual(self.parsed["company"], "示例平台")
        self.assertEqual(self.parsed["location"], "示例市、另一市")
        self.assertEqual(self.parsed["category"], "技术类-平台")
        self.assertIn("负责示例平台的能力建设", self.parsed["responsibilities"])
        self.assertIn("本科及以上学历", self.parsed["qualifications"])

    def test_extra_sections_are_preserved_not_dropped(self) -> None:
        headings = [section["heading"] for section in self.parsed["sections"]]
        self.assertEqual(headings, ["岗位职责", "职位要求", "加分项"])
        bonus = [s for s in self.parsed["sections"] if s["heading"] == "加分项"][0]
        self.assertIn("有示例加分经验", bonus["text"])

    def test_single_paragraph_bullets_stay_one_block(self) -> None:
        # 站点把多个条目塞在同一个 <p> 里，解析不猜分隔符，保持原文。
        self.assertIn("-负责示例平台的能力建设", self.parsed["responsibilities"])

    def test_empty_document_degrades_with_warning(self) -> None:
        parsed = parse_detail("<html><body></body></html>")
        self.assertEqual(parsed["title"], "")
        self.assertEqual(parsed["responsibilities"], "")
        self.assertEqual(parsed["sections"], [])


if __name__ == "__main__":
    unittest.main()
