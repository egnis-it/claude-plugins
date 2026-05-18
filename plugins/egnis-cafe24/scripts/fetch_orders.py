#!/usr/bin/env python3
"""
scripts/fetch_orders.py — cafe24-orders-export skill용 데이터 적재 스크립트.

Plan: .omc/plans/cafe24-mcp-skills-v3.md §7 Phase 3
ADR-001: fetch_sales.py 무수정 (이 스크립트는 신규)
ADR-003 §3.2: urllib only (cafe24_client 라이브러리 경유)

cafe24_client.Cafe24Client 위에 도메인별 쿼리/저장 로직만 작성한다.
401/403 → exit 2 → skill이 토큰 재발급 → 재실행 (raw JSON 재사용 idempotent).

Endpoints used:
  GET /api/v2/admin/orders/count                       (dry-run 1회만)
  GET /api/v2/admin/orders                             (페이지네이션 자동)
  GET /api/v2/admin/cancellation/{claim_code}          (--include-cancels)
  GET /api/v2/admin/exchange/{claim_code}              (--include-exchanges)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

# scripts/ 디렉토리를 sys.path에 추가 (모든 호출 시나리오 호환 — spike 결과)
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from lib.cafe24_client import Cafe24Client, Cafe24RateLimited, Cafe24TokenExpired  # noqa: E402
from lib.brand_registry import resolve_brand  # noqa: E402

ORDERS_LIMIT = 1000
ORDERS_EMBED = "items,buyer,receivers"


def _today() -> date:
    return date.today()


def _default_period() -> tuple[date, date]:
    end = _today()
    start = end - timedelta(days=6)
    return start, end


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_tokens() -> dict[str, dict]:
    raw = os.environ.get("CAFE24_TOKENS_JSON")
    if not raw:
        print("ERROR: CAFE24_TOKENS_JSON env var not set", file=sys.stderr)
        sys.exit(1)
    try:
        tokens = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: CAFE24_TOKENS_JSON invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(tokens, dict) or not tokens:
        print("ERROR: CAFE24_TOKENS_JSON must be non-empty dict", file=sys.stderr)
        sys.exit(1)
    return tokens


def fetch_count(client: Cafe24Client, mall_id: str, start: str, end: str) -> dict:
    """주문 건수 (dry-run 단일 호출 대상)."""
    return client.get(
        mall_id,
        "/api/v2/admin/orders/count",
        params={
            "start_date": start,
            "end_date": end,
            "date_type": "order_date",
        },
    )


def fetch_orders_paginated(
    client: Cafe24Client,
    mall_id: str,
    start: str,
    end: str,
    include_cancels: bool = False,
    include_exchanges: bool = False,
) -> list[dict]:
    """주문 목록을 페이지네이션 자동 순회로 전부 수집.

    3개월 초과 기간은 자동 분할.
    반환: 페이지별 body dict의 list (각 body는 {"orders": [...], "links": [...]} 형태).
    """
    all_pages: list[dict] = []
    embed_parts = ["items", "buyer", "receivers"]
    if include_cancels:
        embed_parts.append("cancellation")
    if include_exchanges:
        embed_parts.append("exchange")
    embed_value = ",".join(embed_parts)

    for chunk_start, chunk_end in Cafe24Client.split_date_range(start, end, max_months=3):
        pages = client.get_all_pages(
            mall_id,
            "/api/v2/admin/orders",
            params={
                "start_date": chunk_start,
                "end_date": chunk_end,
                "date_type": "order_date",
                "limit": str(ORDERS_LIMIT),
                "embed": embed_value,
            },
            max_pages=20,  # 안전 한도 (20 * 1000 = 20K orders / chunk)
        )
        all_pages.extend(pages)
    return all_pages


def write_outputs(
    out_dir: Path,
    mall_id: str,
    count_result: dict,
    pages: list[dict] | None,
) -> None:
    """raw JSON을 out_dir에 mall별 1파일로 저장."""
    _ensure_dir(out_dir)
    count_path = out_dir / f"orders_count_{mall_id}.json"
    _save_json(count_path, count_result)
    if pages is not None:
        orders_path = out_dir / f"orders_{mall_id}.json"
        _save_json(orders_path, pages)


def run(args: argparse.Namespace) -> int:
    # 인자 해석
    try:
        mall_ids = resolve_brand(args.brand) if args.brand else resolve_brand("all")
    except KeyError as e:
        print(f"ERROR: unknown brand alias: {e}", file=sys.stderr)
        return 1

    if args.period_start and args.period_end:
        start_str, end_str = args.period_start, args.period_end
    else:
        start, end = _default_period()
        start_str, end_str = start.isoformat(), end.isoformat()

    tokens = _load_tokens()

    # 토큰에 없는 mall_id는 skip + WARN
    available = [m for m in mall_ids if m in tokens]
    missing = [m for m in mall_ids if m not in tokens]
    if missing:
        print(f"WARN: tokens missing for: {missing}", file=sys.stderr)
    if not available:
        print("ERROR: no tokens available for requested brands", file=sys.stderr)
        return 1

    client = Cafe24Client(tokens)
    out_dir = Path(args.out_dir)

    print(f"[fetch_orders] brands={available} period={start_str}~{end_str} dry_run={args.dry_run}")

    failed_auth: list[str] = []
    for mall_id in available:
        try:
            count = fetch_count(client, mall_id, start_str, end_str)
            print(f"  [{mall_id}] count={count.get('body', {}).get('count', 'N/A')}")

            if args.dry_run:
                # dry-run: count 1회만 저장, orders 본 조회는 skip
                write_outputs(out_dir, mall_id, count, pages=None)
                continue

            pages = fetch_orders_paginated(
                client, mall_id, start_str, end_str,
                include_cancels=args.include_cancels,
                include_exchanges=args.include_exchanges,
            )
            total_rows = sum(len(p.get("orders", [])) for p in pages if isinstance(p, dict))
            print(f"  [{mall_id}] pages={len(pages)} orders={total_rows}")
            write_outputs(out_dir, mall_id, count, pages)

        except Cafe24TokenExpired as e:
            print(f"AUTH FAIL: mall={mall_id} status={e.status}", file=sys.stderr)
            failed_auth.append(mall_id)
        except Cafe24RateLimited as e:
            print(f"RATE LIMITED: mall={mall_id} remain={e.remain_seconds}s — abort", file=sys.stderr)
            return 4

    if failed_auth:
        print(f"AUTH FAIL: refresh tokens for {failed_auth} and re-run", file=sys.stderr)
        return 2

    print(f"[fetch_orders] done. raw → {out_dir}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Cafe24 orders fetcher (cafe24-orders-export skill)")
    p.add_argument("--brand", default="all", help="brand alias (default: all)")
    p.add_argument("--period-start", help="YYYY-MM-DD (default: today - 6d)")
    p.add_argument("--period-end", help="YYYY-MM-DD (default: today)")
    p.add_argument("--out-dir", default="./reports/cafe24/all/_latest/data/raw",
                   help="output directory for raw JSON")
    p.add_argument("--include-cancels", action="store_true", help="include cancellation details")
    p.add_argument("--include-exchanges", action="store_true", help="include exchange details")
    p.add_argument("--dry-run", action="store_true",
                   help="run /orders/count only (token + 1 endpoint, no full fetch)")
    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    raise SystemExit(run(args))
