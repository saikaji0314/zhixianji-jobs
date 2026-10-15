"""命中位置判定的测试。

站点的搜索结果是「标题命中」与「只在岗位详情里命中」混在一起的一个集合：
q=agent 时 4780 条里只有 1001 条标题含关键词，其余靠岗位详情命中
（很多只是要求里顺带一句「有 Agent 经验者优先」）。分析前必须能区分，
这里固定 query_score 到命中位置的映射。

用真实数据验证过的对应关系（2026-10-15，q=agent）：
    标题含 agent 的 1001 条 -> query_score 只有 110(964) 与 100(37)
    标题不含的   3778 条 -> query_score 全部为 10
"""

from __future__ import annotations

import unittest

from zhixianji.export import TITLE_HIT_MIN_SCORE, is_title_hit


class IsTitleHitTest(unittest.TestCase):
    def test_observed_title_hit_tiers(self) -> None:
        for score in (110, 100):
            self.assertTrue(is_title_hit({"query_score": score}), msg=f"score={score} 应判为标题命中")

    def test_observed_description_only_tier(self) -> None:
        self.assertFalse(is_title_hit({"query_score": 10}))

    def test_missing_or_unusable_score_is_not_a_title_hit(self) -> None:
        for value in (None, "", "abc", "high"):
            self.assertFalse(is_title_hit({"query_score": value}), msg=repr(value))
        self.assertFalse(is_title_hit({}))

    def test_string_scores_from_json_are_handled(self) -> None:
        self.assertTrue(is_title_hit({"query_score": "110"}))
        self.assertFalse(is_title_hit({"query_score": "10"}))

    def test_threshold_is_the_documented_value(self) -> None:
        self.assertEqual(TITLE_HIT_MIN_SCORE, 100)


if __name__ == "__main__":
    unittest.main()
