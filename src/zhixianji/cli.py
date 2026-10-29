"""命令行入口。

命令划分刻意保持小：validate 只读配置，discover 只读列表，details 只读详情，
export 只动本地文件。没有任何写入站点或联系招聘方的能力。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .config import Config, ConfigError, load_config
from .detail import fetch_detail
from .discover import DiscoverError, discover
from .export import export_all, write_summary
from .http_client import FetchError, HttpClient
from .store import Store
from .window import TimeWindow, parse_site_time

DEFAULT_DB = "data/zhixianji.db"
DEFAULT_OUTPUT = "output"


def _eprint(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _bounded_int(name: str, minimum: int, maximum: int):
    """构造 argparse 的整数范围校验器。

    在 parse_args 阶段完成校验，非法值由 argparse 写 stderr 并以退出码 2 结束，
    不会进入命令函数，因此也不会打开或创建数据库。
    """

    def parse(text: str) -> int:
        try:
            value = int(text)
        except (TypeError, ValueError):
            raise argparse.ArgumentTypeError(f"{name} 需要整数，收到 {text!r}") from None
        if not minimum <= value <= maximum:
            raise argparse.ArgumentTypeError(
                f"{name} 需在 {minimum}—{maximum} 之间，收到 {value}"
            )
        return value

    return parse


def _build_window(cfg: Config, as_of: str | None) -> TimeWindow:
    pinned = None
    value = as_of or cfg.window.as_of
    if value:
        pinned = parse_site_time(value)
        if pinned is None:
            raise ConfigError(f"无法解析 window.asOf / --as-of：{value!r}（示例：2026-10-15T19:00+08:00）")
    return TimeWindow(cfg.window.field, cfg.window.days, as_of=pinned)


def _client_from_collection(collection: Any, delay_ms: int | None = None) -> HttpClient:
    return HttpClient(
        user_agent=collection.user_agent,
        timeout_ms=collection.timeout_ms,
        max_retries=collection.max_retries,
        delay_ms=collection.list_delay_ms if delay_ms is None else delay_ms,
    )


def _config_from_batch(batch: dict[str, Any]) -> Config:
    raw = json.loads(batch.get("config_json") or "{}")
    cfg = Config()
    cfg.base_url = raw.get("baseUrl") or batch.get("base_url") or "https://jobs.ultraai.site"
    cfg.search = raw.get("search") or {}
    window = raw.get("window") or {}
    cfg.window.field = window.get("field", "first_seen_at")
    cfg.window.days = int(window.get("days", 30))
    cfg.window.as_of = window.get("asOf")
    collection = raw.get("collection") or {}
    cfg.collection.max_pages = int(collection.get("maxPages", 200))
    cfg.collection.max_jobs = int(collection.get("maxJobs", 2000))
    cfg.collection.timeout_ms = int(collection.get("timeoutMs", 20000))
    cfg.collection.max_retries = int(collection.get("maxRetries", 3))
    cfg.collection.list_delay_ms = int(collection.get("listDelayMs", 400))
    cfg.collection.detail_delay_ms = int(collection.get("detailDelayMs", 700))
    if collection.get("userAgent"):
        cfg.collection.user_agent = collection["userAgent"]
    return cfg


# --------------------------------------------------------------------- commands


def cmd_validate(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    window = _build_window(cfg, args.as_of)
    print("配置有效。")
    print(f"  站点：{cfg.base_url}")
    print(f"  搜索：{cfg.search_query()}")
    print(f"  窗口：{window.field} 最近 {window.days} 天")
    print(f"  窗口区间：{window.start.isoformat(timespec='seconds')} ～ {window.as_of.isoformat(timespec='seconds')}")
    print(f"  列表地址示例：{cfg.base_url}/api/jobs?{cfg.search_query()}&page=1")
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    window = _build_window(cfg, args.as_of)
    output_root = Path(args.output)
    db_path = Path(args.db)

    with Store(db_path) as store:
        batch_id = args.batch or store.next_batch_id(cfg.search.get("q", "batch"))
        # 交给 cmd_run 使用，避免依赖「最近一个批次」这种在秒级时间戳下不牢靠的推断。
        args.discovered_batch_id = batch_id
        if store.get_batch(batch_id) is not None and args.batch:
            _eprint(f"批次 {batch_id} 已存在；列表结果会按主键覆盖，详情不受影响。")
        store.create_batch(batch_id, cfg.base_url, cfg.to_json(), window.to_json(), notes=args.notes or "")
        batch_dir = output_root / batch_id
        raw_list_dir = batch_dir / "raw" / "list"

        client = _client_from_collection(cfg.collection)
        print(f"批次：{batch_id}")
        print(f"列表：{cfg.base_url}/api/jobs?{cfg.search_query()}&page=1")
        print(f"窗口：{window.field} 最近 {window.days} 天（{window.start.isoformat(timespec='seconds')} 起）")

        def progress(page: int, pages: int, collected: int, total: int, page_size: int) -> None:
            print(f"  第 {page}/{pages} 页，累计 {collected} 条（站点 total={total}，每页 {page_size}）", flush=True)

        try:
            result = discover(
                client=client,
                base_url=cfg.base_url,
                search_query=cfg.search_query(),
                max_pages=cfg.collection.max_pages,
                max_jobs=cfg.collection.max_jobs,
                raw_dir=raw_list_dir,
                progress=progress,
            )
        except (DiscoverError, FetchError) as exc:
            _eprint(f"列表读取失败：{exc}")
            return 2

        in_window = 0
        for item in result.items:
            decision = window.evaluate(item)
            item["_window"] = {
                "in_window": decision.in_window,
                "reason": decision.reason,
                "value": decision.value,
            }
            if decision.in_window:
                in_window += 1
        store.save_list_items(batch_id, result.items)

        reasons: dict[str, int] = {}
        for item in result.items:
            if item["_window"]["in_window"]:
                continue
            reason = item["_window"]["reason"]
            reasons[reason] = reasons.get(reason, 0) + 1

        print(
            f"列表完成：读取 {result.pages_fetched} 页，站点报告 {result.total_reported} 条，"
            f"实际取得 {len(result.items)} 条（重复 {result.duplicate_count} 条）。"
        )
        print(f"窗口内 {in_window} 条；窗口外 {len(result.items) - in_window} 条 {reasons or ''}")
        for warning in result.warnings:
            _eprint(f"警告：{warning}")

        summary = write_summary(
            store,
            batch_id,
            batch_dir / "summary.json",
            extra={
                "listRun": {
                    "pagesFetched": result.pages_fetched,
                    "totalReported": result.total_reported,
                    "itemsCollected": len(result.items),
                    "duplicates": result.duplicate_count,
                    "warnings": result.warnings,
                    "requestsMade": client.request_count,
                }
            },
        )
        print(f"摘要：{batch_dir / 'summary.json'}")
        print(f"窗口内待抓详情：{summary['counts']['inWindow']} 条。下一步：python run.py details --batch {batch_id}")
    return 0


def cmd_details(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    output_root = Path(args.output)
    with Store(db_path) as store:
        batch = store.get_batch(args.batch)
        if batch is None:
            _eprint(f"找不到批次：{args.batch}（用 python run.py batches 查看）")
            return 2
        cfg = _config_from_batch(batch)
        pending = store.pending_details(args.batch, retry_failed=args.retry_failed)
        if args.limit is not None:
            pending = pending[: args.limit]
        if not pending:
            print("没有待抓取的详情。")
            export_all(store, args.batch, output_root / args.batch)
            return 0

        client = _client_from_collection(cfg.collection, delay_ms=cfg.collection.detail_delay_ms)
        raw_html_dir = output_root / args.batch / "raw" / "html"
        print(f"批次 {args.batch}：待抓取 {len(pending)} 条，间隔 {cfg.collection.detail_delay_ms} ms")
        counts: dict[str, int] = {}
        try:
            for index, item in enumerate(pending, start=1):
                record = fetch_detail(client, cfg.base_url, item, raw_dir=raw_html_dir)
                store.save_detail(args.batch, record)
                counts[record["status"]] = counts.get(record["status"], 0) + 1
                title = (record["title"] or item.get("title") or "")[:40]
                print(
                    f"  [{index}/{len(pending)}] {record['source']}/{record['source_job_id']} "
                    f"{record['status']} jd={record['jd_chars']}字 {title}",
                    flush=True,
                )
                if record["status"] not in ("ok",):
                    _eprint(f"    说明：{record['error']}")
        except KeyboardInterrupt:
            _eprint("\n已中断；已抓取的结果已保存，可用同一条命令继续。")

        print(f"详情完成统计：{counts}")
        summary = export_all(store, args.batch, output_root / args.batch)
        print(f"导出：{output_root / args.batch}/jobs.csv（通过 {summary['counts']['jdOk']} 条含完整 JD）")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    code = cmd_discover(args)
    if code != 0:
        return code
    batch_id = getattr(args, "discovered_batch_id", None) or args.batch
    if batch_id is None:
        _eprint("列表阶段没有返回批次号")
        return 2
    if args.skip_details:
        return 0
    detail_args = argparse.Namespace(**vars(args))
    detail_args.batch = batch_id
    return cmd_details(detail_args)


def cmd_export(args: argparse.Namespace) -> int:
    with Store(Path(args.db)) as store:
        batch = store.get_batch(args.batch)
        if batch is None:
            _eprint(f"找不到批次：{args.batch}")
            return 2
        summary = export_all(store, args.batch, Path(args.output) / args.batch)
    print(f"已导出到 {Path(args.output) / args.batch}")
    print(json.dumps(summary["counts"], ensure_ascii=False, indent=2))
    return 0


def cmd_batches(args: argparse.Namespace) -> int:
    limit = getattr(args, "limit", None)
    offset = getattr(args, "offset", 0) or 0
    with Store(Path(args.db)) as store:
        batches = store.list_batches(limit=limit, offset=offset)
    if not batches:
        if offset == 0 and limit is None:
            print("还没有批次。先运行：python run.py discover --config config/search.local.json")
        return 0
    print(f"{'批次':<28} {'创建时间':<26} {'列表':>6} {'窗口内':>7} {'含完整JD':>9}  搜索")
    for batch in batches:
        search = json.loads(batch["config_json"] or "{}").get("search") or {}
        print(
            f"{batch['batch_id']:<28} {batch['created_at']:<26} "
            f"{batch['list_count']:>6} {batch['window_count']:>7} {batch['ok_count']:>9}  "
            f"{json.dumps(search, ensure_ascii=False)}"
        )
    return 0


# ------------------------------------------------------------------------ parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zhixianji-jobs",
        description="职先机岗位采集：按搜索条件读取列表，抓取收录时间在窗口内的完整 JD。只读，不投递。",
    )
    parser.add_argument("--db", default=DEFAULT_DB, help=f"SQLite 路径（默认 {DEFAULT_DB}，相对当前目录）")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help=f"导出根目录（默认 {DEFAULT_OUTPUT}）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="校验配置文件，不访问网络")
    p_validate.add_argument("--config", required=True)
    p_validate.add_argument("--as-of", dest="as_of", default=None, help="把窗口结束时间固定为指定时间")
    p_validate.set_defaults(func=cmd_validate)

    p_discover = sub.add_parser("discover", help="按搜索条件读取列表并判定收录时间窗口")
    p_discover.add_argument("--config", required=True)
    p_discover.add_argument("--batch", default=None, help="指定批次号；默认自动生成")
    p_discover.add_argument("--as-of", dest="as_of", default=None)
    p_discover.add_argument("--notes", default="")
    p_discover.set_defaults(func=cmd_discover)

    p_details = sub.add_parser("details", help="为窗口内岗位抓取完整 JD")
    p_details.add_argument("--batch", required=True)
    p_details.add_argument("--limit", type=int, default=None, help="只抓前 N 条，用于小批验证")
    p_details.add_argument("--retry-failed", action="store_true", help="连同之前失败的记录一起重试")
    p_details.set_defaults(func=cmd_details)

    p_run = sub.add_parser("run", help="列表 + 详情 + 导出（最常用）")
    p_run.add_argument("--config", required=True)
    p_run.add_argument("--batch", default=None)
    p_run.add_argument("--as-of", dest="as_of", default=None)
    p_run.add_argument("--notes", default="")
    p_run.add_argument("--limit", type=int, default=None)
    p_run.add_argument("--retry-failed", action="store_true")
    p_run.add_argument("--skip-details", action="store_true", help="只读列表，不抓详情")
    p_run.set_defaults(func=cmd_run)

    p_export = sub.add_parser("export", help="从本地数据库重新导出，不访问站点")
    p_export.add_argument("--batch", required=True)
    p_export.set_defaults(func=cmd_export)

    p_batches = sub.add_parser("batches", help="列出本地批次")
    p_batches.add_argument(
        "--limit",
        type=_bounded_int("--limit", 1, 1000),
        default=None,
        help="本页最多显示 N 个批次（1—1000）；默认显示全部",
    )
    p_batches.add_argument(
        "--offset",
        type=_bounded_int("--offset", 0, 1_000_000),
        default=0,
        help="跳过前 M 个批次（0—1000000，默认 0）；只给 offset 时显示其余全部",
    )
    p_batches.set_defaults(func=cmd_batches)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ConfigError as exc:
        _eprint(f"配置错误：{exc}")
        return 2
    except FetchError as exc:
        _eprint(f"网络请求失败：{exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
