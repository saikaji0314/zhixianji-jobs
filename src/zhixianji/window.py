"""收录时间窗口。

站点把「收录时间」显示为 ``first_seen_at``（详情页 <p class="job-collected">），
本工具默认也按该字段筛选。窗口是「截至 as_of 的最近 N 天」的滚动窗口，
边界按日期时间比较，不用自然日近似。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

CN_TZ = timezone(timedelta(hours=8))

DATE_ONLY_RE = r"^\d{4}-\d{2}-\d{2}$"


def parse_site_time(value: Any) -> datetime | None:
    """解析站点时间字段。缺失或无法解析时返回 None（保持未知，不猜）。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        try:
            return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=CN_TZ)
        except ValueError:
            return None
    candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # 站点实际输出带 +08:00；无时区信息时按站点时区解释并记录在文档里。
        parsed = parsed.replace(tzinfo=CN_TZ)
    return parsed


@dataclass
class WindowDecision:
    in_window: bool
    reason: str
    value: str | None
    parsed: datetime | None


class TimeWindow:
    def __init__(self, field_name: str, days: int, as_of: datetime | None = None) -> None:
        self.field = field_name
        self.days = days
        self.as_of = (as_of or datetime.now(CN_TZ)).astimezone(CN_TZ)
        self.start = self.as_of - timedelta(days=days)

    def to_json(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "days": self.days,
            "asOf": self.as_of.isoformat(timespec="seconds"),
            "startInclusive": self.start.isoformat(timespec="seconds"),
        }

    def evaluate(self, item: dict[str, Any]) -> WindowDecision:
        raw = item.get(self.field)
        parsed = parse_site_time(raw)
        if parsed is None:
            return WindowDecision(False, "missing_date", None if raw is None else str(raw), None)
        if parsed > self.as_of:
            # 站点时间晚于本机当前时间：可能是时钟差异，保守地不计入并在摘要里体现。
            return WindowDecision(False, "after_as_of", str(raw), parsed)
        if parsed < self.start:
            return WindowDecision(False, "before_window", str(raw), parsed)
        return WindowDecision(True, "in_window", str(raw), parsed)
