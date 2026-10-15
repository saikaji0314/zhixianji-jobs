"""配置加载与校验。

搜索条件全部来自输入，程序不内置任何求职者背景、城市或岗位方向偏好。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "https://jobs.ultraai.site"

# 与站点 /discover 页面 currentFilters() 中的 URLSearchParams 对应。
# 这里只列出可安全透传的查询参数；未知参数会被拒绝，避免静默拼错。
ALLOWED_SEARCH_PARAMS = {
    "q",
    "job_type",
    "status",
    "active",
    "location",
    "source",
    "today",
    "organization",
    "role",
    "topic",
    "new_since",
    "offline_since",
}

ALLOWED_WINDOW_FIELDS = {"first_seen_at", "published_at", "last_seen_at", "posted_at", "updated_at"}


class ConfigError(ValueError):
    """配置缺失或取值非法。"""


@dataclass
class Window:
    """收录时间窗口。

    field 决定按哪个时间字段筛选；站点把「收录时间」显示为 first_seen_at。
    """

    field: str = "first_seen_at"
    days: int = 30
    as_of: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"field": self.field, "days": self.days, "asOf": self.as_of}


@dataclass
class Collection:
    # 站点固定每页 60 条且不接受 page_size 参数；分页以响应中的 page_size 为准。
    max_pages: int = 200
    max_jobs: int = 2000
    timeout_ms: int = 20000
    max_retries: int = 3
    list_delay_ms: int = 400
    detail_delay_ms: int = 700
    user_agent: str = "zhixianji-jobs/0.1 (personal job research; read-only)"

    def to_json(self) -> dict[str, Any]:
        return {
            "maxPages": self.max_pages,
            "maxJobs": self.max_jobs,
            "timeoutMs": self.timeout_ms,
            "maxRetries": self.max_retries,
            "listDelayMs": self.list_delay_ms,
            "detailDelayMs": self.detail_delay_ms,
        }


@dataclass
class Config:
    base_url: str = DEFAULT_BASE_URL
    search: dict[str, str] = field(default_factory=dict)
    window: Window = field(default_factory=Window)
    collection: Collection = field(default_factory=Collection)
    source_path: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "baseUrl": self.base_url,
            "search": dict(self.search),
            "window": self.window.to_json(),
            "collection": self.collection.to_json(),
        }

    def search_query(self) -> str:
        """构造 /api/jobs 的查询串（不含 page）。"""
        from urllib.parse import urlencode

        return urlencode([(k, self.search[k]) for k in sorted(self.search)])


def _require_type(value: Any, types: tuple[type, ...], name: str) -> Any:
    if not isinstance(value, types):
        raise ConfigError(f"{name} 类型不正确：期望 {types}，实际 {type(value).__name__}")
    return value


def load_config(path: str | Path | None) -> Config:
    """从 JSON 文件加载配置；path 为 None 时返回默认配置。"""
    if path is None:
        cfg = Config()
        cfg.search = {"q": "agent", "job_type": "social", "status": "active"}
        return cfg

    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"配置文件不存在：{p}")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"配置文件不是合法 JSON：{p}（{exc}）") from exc
    if not isinstance(raw, dict):
        raise ConfigError("配置文件顶层必须是 JSON 对象")

    cfg = Config(source_path=str(p))
    cfg.base_url = str(raw.get("baseUrl") or DEFAULT_BASE_URL).rstrip("/")

    search = _require_type(raw.get("search", {}), (dict,), "search")
    clean: dict[str, str] = {}
    for key, value in search.items():
        if key.startswith("$"):
            continue
        if key not in ALLOWED_SEARCH_PARAMS:
            raise ConfigError(
                f"search 中出现未知参数 {key!r}；允许的参数：{', '.join(sorted(ALLOWED_SEARCH_PARAMS))}"
            )
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            # 空串与纯空白都视为「未设置」，不拼进查询串。
            continue
        clean[key] = text
    if not clean.get("q"):
        # 站点允许空关键词，但本工具的用途是按关键词收敛范围，缺失时明确提示。
        raise ConfigError("search.q 不能为空：请填写要搜索的关键词")
    cfg.search = clean

    win = _require_type(raw.get("window", {}), (dict,), "window")
    window = Window()
    if "field" in win and win["field"] is not None:
        field_name = str(win["field"])
        if field_name not in ALLOWED_WINDOW_FIELDS:
            raise ConfigError(
                f"window.field 非法：{field_name!r}；允许：{', '.join(sorted(ALLOWED_WINDOW_FIELDS))}"
            )
        window.field = field_name
    if "days" in win and win["days"] is not None:
        days = int(win["days"])
        if days < 1 or days > 3650:
            raise ConfigError("window.days 必须在 1～3650 之间")
        window.days = days
    if win.get("asOf"):
        window.as_of = str(win["asOf"])
    cfg.window = window

    col = _require_type(raw.get("collection", {}), (dict,), "collection")
    collection = Collection()
    for json_key, attr, low, high in (
        ("maxPages", "max_pages", 1, 2000),
        ("maxJobs", "max_jobs", 1, 100000),
        ("timeoutMs", "timeout_ms", 1000, 120000),
        ("maxRetries", "max_retries", 0, 10),
        ("listDelayMs", "list_delay_ms", 0, 60000),
        ("detailDelayMs", "detail_delay_ms", 0, 60000),
    ):
        if col.get(json_key) is not None:
            value = int(col[json_key])
            if value < low or value > high:
                raise ConfigError(f"collection.{json_key} 必须在 {low}～{high} 之间")
            setattr(collection, attr, value)
    if col.get("userAgent"):
        collection.user_agent = str(col["userAgent"])
    cfg.collection = collection

    return cfg
