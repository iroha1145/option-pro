"""手动跑一次首页「市场综合研判」，或只组装证据包看一眼。

    python -m app.tools.market_brief_run --slot pre_open|post_close [--date YYYY-MM-DD]
        [--trigger manual|scheduled] [--dry-run] [--data-dir PATH] [--no-structured-output]

发送前把请求参数摘要打印到标准输出（模型、effort、工具、max_tokens、缓存设置、证据字节数、
缺失模块），出现 400 时可以直接对照。结束时打印 run_id、状态、错误码、费用与记录路径；
运行失败退出码为 1。--dry-run 不需要密钥，也不发请求、不写记录。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from datetime import date, datetime, timezone
from typing import Any, Mapping, Sequence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one home-page market brief (Claude) or only assemble its evidence pack.",
    )
    parser.add_argument("--slot", required=True, choices=("pre_open", "post_close"))
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="Trading date (New York). Defaults to today's session for pre_open, the last completed one for post_close.",
    )
    parser.add_argument("--trigger", choices=("manual", "scheduled"), default="manual")
    parser.add_argument("--dry-run", action="store_true", help="Assemble and print the evidence only; no request.")
    parser.add_argument("--data-dir", default=None, help="Absolute DATA_DIR to read snapshots from and write the run to.")
    parser.add_argument(
        "--no-structured-output",
        action="store_true",
        help="Send no output_config.format (the system prompt carries the JSON schema instead).",
    )
    parser.add_argument(
        "--structured-output",
        action="store_true",
        help="Force output_config.format on for this run regardless of [market_brief].structured_output.",
    )
    return parser


def _print_json(title: str, value: Mapping[str, Any]) -> None:
    print(title)
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))
    sys.stdout.flush()


def _api_key(settings: Any) -> str:
    secret = getattr(settings, "anthropic_api_key", None)
    value = secret.get_secret_value() if hasattr(secret, "get_secret_value") else secret
    return value.strip() if isinstance(value, str) else ""


def default_trading_date(slot: str, now: datetime) -> date:
    from app.services.market_calendar import ET, last_completed_trading_day, next_trading_day

    if slot == "pre_open":
        return next_trading_day(now.astimezone(ET).date(), include_start=True)
    return last_completed_trading_day(now)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.data_dir:
        from app.data_paths import data_dir

        try:
            os.environ["DATA_DIR"] = str(data_dir(args.data_dir))
        except ValueError as exc:
            parser.error(str(exc))

    # 设好 DATA_DIR 之后再导入：部分模块（板块隐含波动率快照目录、设置缓存）在导入或首次读取时定下路径。
    from app.config import get_settings
    from app.personal_config import get_personal_config
    from app.services.market_brief.errors import ANTHROPIC_API_KEY_MISSING
    from app.services.market_brief.evidence import build_evidence
    from app.services.market_brief.runner import BriefRunConfig, run_brief
    from app.services.market_brief.store import BriefStore
    from app.services.market_calendar import is_trading_day

    now = datetime.now(timezone.utc)
    section = getattr(get_personal_config(), "market_brief", None)
    config = section.to_run_config() if section is not None else BriefRunConfig()
    if args.no_structured_output:
        config = replace(config, structured_output=False)
    elif args.structured_output:
        config = replace(config, structured_output=True)
    trading_date = args.date or default_trading_date(args.slot, now)
    if not is_trading_day(trading_date):
        parser.error(f"{trading_date.isoformat()} is not a NYSE trading day")
    if trading_date != default_trading_date(args.slot, now):
        print("提示：证据一律按当前时刻读取；--date 只决定槽位标签与财报、经济日历的窗口，不会回放历史数据。")
    store = BriefStore()

    if args.dry_run:
        pack = build_evidence(
            slot=args.slot,
            trading_date=trading_date,
            now=now,
            store=store,
            max_bytes=config.evidence_max_bytes,
        )
        _print_json(
            "== 证据覆盖（dry run，未发送请求）",
            {
                "slot": args.slot,
                "trading_date": trading_date.isoformat(),
                "evidence_bytes": pack.bytes,
                "evidence_ids": len(pack.evidence_ids),
                "allowed_codes": len(pack.allowed_codes),
                "source_texts": len(pack.source_texts),
                "coverage": pack.coverage,
            },
        )
        print("== 证据包前 2000 字符")
        print(json.dumps(pack.payload, ensure_ascii=False, separators=(",", ":"))[:2000])
        return 0

    api_key = _api_key(get_settings())
    if not api_key:
        print(f"ANTHROPIC_API_KEY 未配置（{ANTHROPIC_API_KEY_MISSING}），没有发送请求。", file=sys.stderr)
        return 1
    if section is not None and not getattr(section, "enabled", True):
        print("提示：personal.toml 里 [market_brief] enabled = false；命令行按要求照常运行一次。")
    record = run_brief(
        slot=args.slot,
        trading_date=trading_date,
        trigger=args.trigger,
        store=store,
        config=config,
        api_key=api_key,
        now=now,
        on_request=lambda meta: _print_json("== 请求参数摘要（即将发送）", meta),
    )
    _print_json(
        "== 运行结果",
        {
            "run_id": record.run_id,
            "status": record.status,
            "error_code": record.error_code,
            "error_detail": record.error_detail,
            "cost_usd": round(record.cost_microusd / 1_000_000, 4) if record.cost_microusd is not None else None,
            "usage": dict(record.usage),
            "usage_complete": record.usage_complete,
            "duration_seconds": record.duration_seconds,
            "continuation_count": record.continuation_count,
            "validation_warnings": list(record.validation_warnings),
            "path": str(store.record_path(record)),
        },
    )
    return 0 if record.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
