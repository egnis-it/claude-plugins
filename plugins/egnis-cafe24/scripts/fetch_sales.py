#!/usr/bin/env python3
"""
fetch_sales.py — Cafe24 hourlysales + Analytics members/sales 데이터 수집.

PR-B 마이그레이션 (Phase 6, 2026-05-18):
  - 기존 자체 구현 (AuthError, http_get_json, CAFE24_API_VERSION, fetch_hourlysales_day,
    fetch_members_sales_day) 을 scripts/lib/cafe24_client.Cafe24Client 호출로 교체.
  - DRY-DEBT 주석 5건 모두 제거 (ADR-001 supersede).
  - 출력 JSON 형식은 기존과 byte-level 동일 (baseline diff = 0 보장).
    - hourlysales_<mall>.json: {"ok": True, "mall_id": ..., "data": {"hourlysales": [...]}}
    - members_sales_<mall>_<day>.json: {"ok": True, "mall_id": ..., "data": {"sales": [...]}}

Plan: .omc/plans/cafe24-mcp-skills-v3.md §7 Phase 6
ADR-001: .omc/plans/adr/ADR-001-fetch-sales-no-edit.md (PR-B에서 supersede)
ADR-003 §3.2: urllib only (cafe24_client 라이브러리도 동일 정책)

Input env: CAFE24_TOKENS_JSON
  {
    "<mall_id>": {
      "access_token": "...",
      "api_host": "https://<source_mall_id>.cafe24api.com",
      "source_mall_id": "<source_mall_id>",
      "shop_no": <int or null>
    },
    ...
  }

Exit codes:
  0 — success
  1 — bad invocation / unrecoverable error
  2 — 401/403 from cafe24api on at least one mall (caller should refresh token)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# scripts/ 디렉토리를 sys.path에 추가 (모든 호출 시나리오 호환 — Phase 0 spike 결과)
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from lib.cafe24_client import (  # noqa: E402
    Cafe24Client,
    Cafe24RateLimited,
    Cafe24TokenExpired,
)

HOURLYSALES_FIELDS = "collection_date,order_count,item_count,sales"


# 호환 alias: 기존 코드/외부 의존성이 fetch_sales.AuthError 를 import 할 수 있어 유지
AuthError = Cafe24TokenExpired


def daterange(start: dt.date, end: dt.date):
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


def fetch_hourlysales_day(client: Cafe24Client, mall_id: str, day: str) -> list[dict]:
    """One day of hourlysales for one mall. Returns the data.hourlysales[] array.

    Cafe24Client.get() 위임. 응답 형식 변환만 담당.
    """
    result = client.get(
        mall_id,
        "/api/v2/admin/reports/hourlysales",
        params={
            "start_date": day,
            "end_date": day,
            "fields": HOURLYSALES_FIELDS,
        },
    )
    status = result.get("status", 0)
    if status != 200:
        body = result.get("body", "")
        raise RuntimeError(
            f"hourlysales {mall_id} {day} returned {status}: {str(body)[:200]}"
        )
    body = result.get("body", {})
    rows = body.get("hourlysales", []) if isinstance(body, dict) else []
    rows = rows or []
    # Slim to required fields (already filtered by `fields` param but be defensive).
    return [
        {
            "collection_date": r.get("collection_date"),
            "order_count": int(r.get("order_count") or 0),
            "item_count": int(r.get("item_count") or 0),
            "sales": r.get("sales"),
        }
        for r in rows
    ]


def fetch_members_sales_day(client: Cafe24Client, mall_id: str, day: str) -> dict:
    """One day of members/sales for one mall. Returns a single aggregated row.

    CA Analytics API (ca-api.cafe24data.com/members/sales) 호출. Cafe24Client.get_ca() 위임.
    """
    result = client.get_ca(
        mall_id,
        "/members/sales",
        params={"start_date": day, "end_date": day},
    )
    status = result.get("status", 0)
    if status != 200:
        body = result.get("body", "")
        raise RuntimeError(
            f"members/sales {mall_id} {day} returned {status}: {str(body)[:200]}"
        )
    body = result.get("body", {})
    sales = body.get("sales", []) if isinstance(body, dict) else []
    sales = sales or []
    if not sales:
        return {
            "member_order_count": 0,
            "member_order_amount": "0",
            "nonmember_order_count": 0,
            "nonmember_order_amount": "0",
        }
    return sales[0]


def collect_mall(
    mall_id: str,
    client: Cafe24Client,
    days: list[str],
    out_dir: Path,
) -> tuple[str, list[str]]:
    """Fetch all days for one mall. Returns (mall_id, list of skipped days)."""
    skipped: list[str] = []
    hourly_rows: list[dict] = []

    for day in days:
        # Skip files that already exist for this day (resume-friendly).
        members_file = out_dir / f"members_sales_{mall_id}_{day}.json"
        if not members_file.exists():
            row = fetch_members_sales_day(client, mall_id, day)
            members_file.write_text(
                json.dumps(
                    {"ok": True, "mall_id": mall_id, "data": {"sales": [row]}},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

        # hourlysales: per-day call, aggregated into one mall-level file below.
        rows = fetch_hourlysales_day(client, mall_id, day)
        hourly_rows.extend(rows)

    # Write one combined file per mall.
    hourly_file = out_dir / f"hourlysales_{mall_id}.json"
    hourly_file.write_text(
        json.dumps(
            {"ok": True, "mall_id": mall_id, "data": {"hourlysales": hourly_rows}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return mall_id, skipped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True, help="raw JSON output directory")
    ap.add_argument("--period-start", required=True, help="YYYY-MM-DD inclusive")
    ap.add_argument("--period-end", required=True, help="YYYY-MM-DD inclusive")
    ap.add_argument(
        "--include-prev-day",
        action="store_true",
        help="Also fetch period_start - 1 day so the dashboard can compute the "
             "day-over-day delta for the first display day",
    )
    args = ap.parse_args()

    tokens_json = os.environ.get("CAFE24_TOKENS_JSON")
    if not tokens_json:
        print("error: CAFE24_TOKENS_JSON env var is required", file=sys.stderr)
        return 1
    try:
        tokens: dict[str, dict] = json.loads(tokens_json)
    except json.JSONDecodeError as e:
        print(f"error: invalid CAFE24_TOKENS_JSON: {e}", file=sys.stderr)
        return 1
    if not tokens:
        print("error: CAFE24_TOKENS_JSON has no malls", file=sys.stderr)
        return 1

    period_start = dt.date.fromisoformat(args.period_start)
    period_end = dt.date.fromisoformat(args.period_end)
    fetch_start = period_start - dt.timedelta(days=1) if args.include_prev_day else period_start
    days = [d.isoformat() for d in daterange(fetch_start, period_end)]

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 단일 Cafe24Client 인스턴스를 모든 mall이 공유 (tokens_json 전체 전달).
    client = Cafe24Client(tokens)

    auth_failures: list[str] = []
    other_failures: list[str] = []

    # Parallelize across malls; per-mall calls stay serial to keep below the
    # Token Bucket per-URL burst cap.
    with ThreadPoolExecutor(max_workers=min(9, len(tokens))) as pool:
        futures = {
            pool.submit(collect_mall, mall_id, client, days, out_dir): mall_id
            for mall_id in tokens.keys()
        }
        for fut in as_completed(futures):
            mall_id = futures[fut]
            try:
                fut.result()
                print(f"✓ {mall_id} — {len(days)} days fetched", file=sys.stderr)
            except Cafe24TokenExpired as e:
                auth_failures.append(e.mall_id)
                print(f"✗ {mall_id} — auth failure (status={e.status})", file=sys.stderr)
            except Cafe24RateLimited as e:
                other_failures.append(mall_id)
                print(f"✗ {mall_id} — rate limited (remain={e.remain_seconds}s)",
                      file=sys.stderr)
            except Exception as e:
                other_failures.append(mall_id)
                print(f"✗ {mall_id} — {e}", file=sys.stderr)

    if auth_failures:
        print(
            f"\nauth_failures: {','.join(auth_failures)}\n"
            "→ caller must re-invoke cafe24_get_access_token for these malls "
            "and retry fetch_sales.py.",
            file=sys.stderr,
        )
        return 2
    if other_failures:
        print(f"\nother_failures: {','.join(other_failures)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
