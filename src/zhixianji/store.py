"""本地存储：SQLite 保存列表快照、窗口判定与详情正文。

数据库与导出结果都在被 .gitignore 忽略的目录里；仓库只保留程序与文档。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .window import CN_TZ

SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    batch_id     TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL,
    base_url     TEXT NOT NULL,
    config_json  TEXT NOT NULL,
    window_json  TEXT NOT NULL,
    notes        TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS list_items (
    batch_id        TEXT NOT NULL,
    source          TEXT NOT NULL,
    source_job_id   TEXT NOT NULL,
    job_type        TEXT,
    title           TEXT,
    organization    TEXT,
    category        TEXT,
    location        TEXT,
    updated_at      TEXT,
    posted_at       TEXT,
    first_seen_at   TEXT,
    last_seen_at    TEXT,
    deactivated_at  TEXT,
    is_active       INTEGER,
    published_at    TEXT,
    published_at_source TEXT,
    query_score     INTEGER,
    has_description INTEGER,
    description_excerpt TEXT,
    public_path     TEXT,
    source_label    TEXT,
    detail_url      TEXT,
    list_page       INTEGER,
    in_window       INTEGER NOT NULL DEFAULT 0,
    window_reason   TEXT,
    window_value    TEXT,
    raw_json        TEXT,
    collected_at    TEXT,
    PRIMARY KEY (batch_id, source, source_job_id)
);

CREATE TABLE IF NOT EXISTS details (
    batch_id        TEXT NOT NULL,
    source          TEXT NOT NULL,
    source_job_id   TEXT NOT NULL,
    status          TEXT NOT NULL,
    http_status     INTEGER,
    fetch_url       TEXT,
    parse_source    TEXT,
    title           TEXT,
    company         TEXT,
    location        TEXT,
    category        TEXT,
    site_collected_at TEXT,
    date_posted     TEXT,
    identifier_value TEXT,
    identifier_name TEXT,
    responsibilities TEXT,
    qualifications  TEXT,
    jd_text         TEXT,
    jd_chars        INTEGER,
    sections_json   TEXT,
    warnings_json   TEXT,
    title_matches   INTEGER,
    content_sha256  TEXT,
    raw_path        TEXT,
    attempts        INTEGER DEFAULT 1,
    error           TEXT,
    fetched_at      TEXT,
    duration_ms     INTEGER,
    PRIMARY KEY (batch_id, source, source_job_id)
);

CREATE INDEX IF NOT EXISTS idx_list_items_window ON list_items (batch_id, in_window);
CREATE INDEX IF NOT EXISTS idx_details_status ON details (batch_id, status);
"""

LIST_COLUMNS = [
    "batch_id", "source", "source_job_id", "job_type", "title", "organization",
    "category", "location", "updated_at", "posted_at", "first_seen_at",
    "last_seen_at", "deactivated_at", "is_active", "published_at",
    "published_at_source", "query_score", "has_description", "description_excerpt",
    "public_path", "source_label", "detail_url", "list_page", "in_window",
    "window_reason", "window_value", "raw_json", "collected_at",
]

DETAIL_COLUMNS = [
    "batch_id", "source", "source_job_id", "status", "http_status", "fetch_url",
    "parse_source", "title", "company", "location", "category",
    "site_collected_at", "date_posted", "identifier_value", "identifier_name",
    "responsibilities", "qualifications", "jd_text", "jd_chars", "sections_json",
    "warnings_json", "title_matches", "content_sha256", "raw_path", "attempts",
    "error", "fetched_at", "duration_ms",
]


def now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


class Store:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---------------------------------------------------------------- batches

    def next_batch_id(self, keyword: str) -> str:
        stamp = datetime.now(CN_TZ).strftime("%Y%m%d-%H%M")
        slug = "".join(ch if (ch.isalnum() or ch in "-_") else "-" for ch in keyword).strip("-")
        slug = slug[:24] or "batch"
        candidate = f"{stamp}-{slug}"
        suffix = 2
        while self.get_batch(candidate) is not None:
            candidate = f"{stamp}-{slug}-{suffix}"
            suffix += 1
        return candidate

    def create_batch(
        self,
        batch_id: str,
        base_url: str,
        config_json: dict[str, Any],
        window_json: dict[str, Any],
        notes: str = "",
    ) -> None:
        """登记批次。同一批次重复执行列表阶段时更新条件，但保留最初的创建时间。"""
        self.conn.execute(
            "INSERT INTO batches (batch_id, created_at, base_url, config_json, window_json, notes)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(batch_id) DO UPDATE SET"
            "   base_url = excluded.base_url,"
            "   config_json = excluded.config_json,"
            "   window_json = excluded.window_json,"
            "   notes = excluded.notes",
            (
                batch_id,
                now_iso(),
                base_url,
                json.dumps(config_json, ensure_ascii=False),
                json.dumps(window_json, ensure_ascii=False),
                notes,
            ),
        )
        self.conn.commit()

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM batches WHERE batch_id = ?", (batch_id,)).fetchone()
        return dict(row) if row else None

    def list_batches(self, limit: int | None = None) -> list[dict[str, Any]]:
        """按创建时间倒序列出批次；``limit`` 为正整数时只取最近 N 个。"""
        sql = """
            SELECT b.*,
                   (SELECT COUNT(*) FROM list_items l WHERE l.batch_id = b.batch_id) AS list_count,
                   (SELECT COUNT(*) FROM list_items l WHERE l.batch_id = b.batch_id AND l.in_window = 1) AS window_count,
                   (SELECT COUNT(*) FROM details d WHERE d.batch_id = b.batch_id AND d.status = 'ok') AS ok_count
            FROM batches b ORDER BY b.created_at DESC
        """
        params: tuple[Any, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    # -------------------------------------------------------------- list items

    def save_list_items(self, batch_id: str, items: Iterable[dict[str, Any]]) -> int:
        collected_at = now_iso()
        rows = []
        for item in items:
            window = item.get("_window") or {}
            rows.append(
                tuple(
                    [
                        batch_id,
                        item.get("source"),
                        item.get("source_job_id"),
                        item.get("job_type"),
                        item.get("title"),
                        item.get("organization"),
                        item.get("category"),
                        item.get("location"),
                        item.get("updated_at"),
                        item.get("posted_at"),
                        item.get("first_seen_at"),
                        item.get("last_seen_at"),
                        item.get("deactivated_at"),
                        1 if item.get("is_active") else 0,
                        item.get("published_at"),
                        item.get("published_at_source"),
                        item.get("query_score"),
                        1 if item.get("has_description") else 0,
                        item.get("description_excerpt"),
                        item.get("public_path"),
                        item.get("source_label"),
                        item.get("detail_url"),
                        item.get("_list_page"),
                        1 if window.get("in_window") else 0,
                        window.get("reason"),
                        window.get("value"),
                        json.dumps(
                            {k: v for k, v in item.items() if not k.startswith("_")},
                            ensure_ascii=False,
                            default=str,
                        ),
                        collected_at,
                    ]
                )
            )
        placeholders = ", ".join(["?"] * len(LIST_COLUMNS))
        self.conn.executemany(
            f"INSERT OR REPLACE INTO list_items ({', '.join(LIST_COLUMNS)}) VALUES ({placeholders})",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def get_list_items(self, batch_id: str, in_window_only: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM list_items WHERE batch_id = ?"
        if in_window_only:
            sql += " AND in_window = 1"
        sql += " ORDER BY first_seen_at DESC, source, source_job_id"
        return [dict(row) for row in self.conn.execute(sql, (batch_id,)).fetchall()]

    def window_counts(self, batch_id: str) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT in_window, COUNT(*) AS n FROM list_items WHERE batch_id = ? GROUP BY in_window",
            (batch_id,),
        ).fetchall()
        counts = {"in_window": 0, "out_of_window": 0}
        for row in rows:
            counts["in_window" if row["in_window"] else "out_of_window"] = row["n"]
        return counts

    # ----------------------------------------------------------------- details

    def save_detail(self, batch_id: str, record: dict[str, Any]) -> None:
        values = [batch_id] + [record.get(column) for column in DETAIL_COLUMNS[1:]]
        placeholders = ", ".join(["?"] * len(DETAIL_COLUMNS))
        self.conn.execute(
            f"INSERT OR REPLACE INTO details ({', '.join(DETAIL_COLUMNS)}) VALUES ({placeholders})",
            values,
        )
        self.conn.commit()

    def get_details(self, batch_id: str) -> dict[tuple[str, str], dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM details WHERE batch_id = ?", (batch_id,)).fetchall()
        return {(row["source"], row["source_job_id"]): dict(row) for row in rows}

    def pending_details(self, batch_id: str, retry_failed: bool = False) -> list[dict[str, Any]]:
        """需要在 window 内、但还没有成功正文的岗位。"""
        items = self.get_list_items(batch_id, in_window_only=True)
        details = self.get_details(batch_id)
        pending = []
        for item in items:
            key = (item["source"], item["source_job_id"])
            record = details.get(key)
            if record is None:
                pending.append(item)
                continue
            if record["status"] == "ok":
                continue
            if retry_failed:
                pending.append(item)
        return pending

    def detail_status_counts(self, batch_id: str) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM details WHERE batch_id = ? GROUP BY status",
            (batch_id,),
        ).fetchall()
        return {row["status"]: row["n"] for row in rows}

    def source_counts(self, batch_id: str, in_window_only: bool = True) -> dict[str, int]:
        sql = "SELECT source, COUNT(*) AS n FROM list_items WHERE batch_id = ?"
        if in_window_only:
            sql += " AND in_window = 1"
        sql += " GROUP BY source ORDER BY n DESC"
        return {row["source"]: row["n"] for row in self.conn.execute(sql, (batch_id,)).fetchall()}
