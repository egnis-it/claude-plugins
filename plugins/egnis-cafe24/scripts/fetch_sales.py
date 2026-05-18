#!/usr/bin/env python3
"""
fetch_sales.py — Directly hit Cafe24 Admin/Analytics APIs using tokens minted
by the egnis-mcp `cafe24_get_access_token` tool.

Input is a JSON env var `CAFE24_TOKENS_JSON`:
  {
    "<mall_id>": {
      "access_token": "...",
      "api_host": "https://<source_mall_id>.cafe24api.com",
      "source_mall_id": "<source_mall_id>",
      "shop_no": <int or null>
    },
    ...
  }

Output:
  <out-dir>/hourlysales_<mall_id>.json              (one per mall, all days)
  <out-dir>/members_sales_<mall_id>_<YYYY-MM-DD>.json (one per mall per day)

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
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

CAFE24_API_VERSION = "2026-03-01"
ANALYTICS_HOST = "https://ca-api.cafe24data.com"
HOURLYSALES_FIELDS = "collection_date,order_count,item_count,sales"
TIMEOUT_SECONDS = 30


class AuthError(Exception):
    def __init__(self, mall_id: str, status: int):
        self.mall_id = mall_id
        self.status = status
        super().__init__(f"auth failed for mall={mall_id} status={status}")


def daterange(start: dt.date, end: dt.date):
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


def http_get_json(url: str, headers: dict[str, str]) -> dict:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            body = resp.read().decode("utf-8")
            return {"status": resp.status, "body": json.loads(body) if body else {}}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"status": e.code, "body": body}


def fetch_hourlysales_day(token_info: dict, mall_id: str, day: str) -> list[dict]:
    """One day of hourlysales for one mall. Returns the data.hourlysales[] array."""
    api_host = token_info["api_host"].rstrip("/")
    params = {
        "start_date": day,
        "end_date": day,
        "fields": HOURLYSALES_FIELDS,
    }
    shop_no = token_info.get("shop_no")
    if shop_no is not None:
        params["shop_no"] = str(shop_no)
    url = f"{api_host}/api/v2/admin/reports/hourlysales?" + urllib.parse.urlencode(params)
    headers = {
        "Authorization": f"Bearer {token_info['access_token']}",
        "X-Cafe24-Api-Version": CAFE24_API_VERSION,
        "Content-Type": "application/json",
    }
    result = http_get_json(url, headers)
    if result["status"] in (401, 403):
        raise AuthError(mall_id, result["status"])
    if result["status"] != 200:
        raise RuntimeError(
            f"hourlysales {mall_id} {day} returned {result['status']}: {str(result['body'])[:200]}"
        )
    rows = (result["body"] or {}).get("hourlysales", []) or []
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


def fetch_members_sales_day(token_info: dict, mall_id: str, day: str) -> dict:
    """One day of members/sales for one mall. Returns a single aggregated row."""
    params = {
        "mall_id": token_info.get("source_mall_id") or mall_id,
        "start_date": day,
        "end_date": day,
    }
    shop_no = token_info.get("shop_no")
    if shop_no is not None:
        params["shop_no"] = str(shop_no)
    url = f"{ANALYTICS_HOST}/members/sales?" + urllib.parse.urlencode(params)
    headers = {
        "Authorization": f"Bearer {token_info['access_token']}",
    }
    result = http_get_json(url, headers)
    if result["status"] in (401, 403):
        raise AuthError(mall_id, result["status"])
    if result["status"] != 200:
        raise RuntimeError(
            f"members/sales {mall_id} {day} returned {result['status']}: {str(result['body'])[:200]}"
        )
    sales = (result["body"] or {}).get("sales", []) or []
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
    token_info: dict,
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
            row = fetch_members_sales_day(token_info, mall_id, day)
            members_file.write_text(
                json.dumps({"ok": True, "mall_id": mall_id, "data": {"sales": [row]}}, ensure_ascii=False),
                encoding="utf-8",
            )

        # hourlysales: per-day call, aggregated into one mall-level file below.
        rows = fetch_hourlysales_day(token_info, mall_id, day)
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
        help="Also fetch period_start - 1 day so the dashboard can compute the day-over-day delta for the first display day",
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

    auth_failures: list[str] = []
    other_failures: list[str] = []

    # Parallelize across malls; per-mall calls stay serial to keep below the
    # Token Bucket per-URL burst cap.
    with ThreadPoolExecutor(max_workers=min(9, len(tokens))) as pool:
        futures = {
            pool.submit(collect_mall, mall_id, token_info, days, out_dir): mall_id
            for mall_id, token_info in tokens.items()
        }
        for fut in as_completed(futures):
            mall_id = futures[fut]
            try:
                fut.result()
                print(f"✓ {mall_id} — {len(days)} days fetched", file=sys.stderr)
            except AuthError as e:
                auth_failures.append(e.mall_id)
                print(f"✗ {mall_id} — auth failure (status={e.status})", file=sys.stderr)
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
