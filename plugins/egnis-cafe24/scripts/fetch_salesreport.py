#!/usr/bin/env python3
"""
scripts/fetch_salesreport.py — cafe24-salesreport-export skill용 데이터 적재.

Plan: .omc/plans/cafe24-mcp-skills-v3.md §7 Phase 4.B
ADR-003 §3.2: urllib only (cafe24_client 라이브러리 경유)

5 endpoints:
  GET /api/v2/admin/financials/dailysales
  GET /api/v2/admin/financials/monthlysales
  GET /api/v2/admin/reports/hourlysales
  GET /api/v2/admin/reports/productsales
  GET /api/v2/admin/reports/salesvolume

SCOPE: mall.read_salesreport
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from lib.cafe24_client import Cafe24Client, Cafe24RateLimited, Cafe24TokenExpired  # noqa: E402
from lib.brand_registry import resolve_brand  # noqa: E402


def _default_period() -> tuple[date, date]:
    end = date.today()
    return end - timedelta(days=6), end


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _save_json(p: Path, data) -> None:
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_tokens() -> dict[str, dict]:
    raw = os.environ.get("CAFE24_TOKENS_JSON")
    if not raw:
        print("ERROR: CAFE24_TOKENS_JSON env var not set", file=sys.stderr)
        sys.exit(1)
    try:
        tokens = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)
    return tokens


def _date_to_month(d: str) -> str:
    """YYYY-MM-DD → YYYY-MM."""
    return d[:7]


def fetch_dailysales(client, mall_id, start, end, payment_method: str = "card"):
    """일별 매출 (PG 정보 포함).

    Live 검증 (2026-05-18): payment_gateway_name / partner_id / payment_method 중
    하나가 **필수** (공식 문서엔 옵션이라 명시됐으나 422 응답). default=card.
    """
    return client.get(
        mall_id,
        "/api/v2/admin/financials/dailysales",
        params={"start_date": start, "end_date": end, "payment_method": payment_method},
    )


def fetch_monthlysales(client, mall_id, start, end, payment_method: str = "card"):
    """월별 매출. start/end → YYYY-MM 형식.

    Live 검증 (2026-05-18):
      - payment_method 필수 (dailysales와 동일)
      - start_month는 **이전 달까지만** 조회 가능. 현재 달 포함시 422.
    """
    # 이전 달까지 강제 — 현재 달은 자동 제외
    import datetime as _dt
    today = _dt.date.today()
    # 현재 달 첫날
    cur_month_first = _dt.date(today.year, today.month, 1)
    # 이전 달 마지막 = 현재 달 첫날 - 1일
    last_complete_month = (cur_month_first - _dt.timedelta(days=1)).strftime("%Y-%m")
    start_m = _date_to_month(start)
    end_m = _date_to_month(end)
    # end_m이 last_complete_month보다 크면 last_complete_month로 보정
    if end_m > last_complete_month:
        end_m = last_complete_month
    if start_m > end_m:
        return {"status": 0, "body": {"monthlysales": []}, "_skipped": "no complete month in range"}
    return client.get(
        mall_id,
        "/api/v2/admin/financials/monthlysales",
        params={"start_month": start_m, "end_month": end_m, "payment_method": payment_method},
    )


def fetch_hourlysales(client, mall_id, start, end):
    """시간대별 매출. 페이지네이션 자동 (limit=1000)."""
    pages = client.get_all_pages(
        mall_id,
        "/api/v2/admin/reports/hourlysales",
        params={"start_date": start, "end_date": end, "limit": "1000"},
        max_pages=10,
    )
    return pages


def fetch_productsales(client, mall_id, start, end):
    """상품별 판매통계. 페이지네이션 자동."""
    pages = client.get_all_pages(
        mall_id,
        "/api/v2/admin/reports/productsales",
        params={"start_date": start, "end_date": end, "limit": "1000"},
        max_pages=20,
    )
    return pages


def fetch_salesvolume(client, mall_id, start, end, product_no: str | None = None):
    """판매수량 통계. product_no 또는 variants_code 중 하나 필수 — 본 스킬에서는 옵션."""
    if not product_no:
        # 본 스킬은 옵션. product_no 미지정 시 skip (필수 인자 누락)
        return None
    return client.get(
        mall_id,
        "/api/v2/admin/reports/salesvolume",
        params={"start_date": start, "end_date": end, "product_no": product_no},
    )


def run(args: argparse.Namespace) -> int:
    try:
        mall_ids = resolve_brand(args.brand)
    except KeyError as e:
        print(f"ERROR: unknown brand alias: {e}", file=sys.stderr)
        return 1

    if args.period_start and args.period_end:
        start_str, end_str = args.period_start, args.period_end
    else:
        s, e = _default_period()
        start_str, end_str = s.isoformat(), e.isoformat()

    tokens = _load_tokens()
    available = [m for m in mall_ids if m in tokens]
    missing = [m for m in mall_ids if m not in tokens]
    if missing:
        print(f"WARN: tokens missing for {missing}", file=sys.stderr)
    if not available:
        print("ERROR: no tokens available", file=sys.stderr)
        return 1

    client = Cafe24Client(tokens)
    out_dir = Path(args.out_dir)
    _ensure_dir(out_dir)

    print(f"[fetch_salesreport] brands={available} period={start_str}~{end_str} dry_run={args.dry_run}")

    failed_auth: list[str] = []
    def _check_status(name: str, mall_id: str, result) -> bool:
        """단일 응답 status 검증. 200/201/202 정상, 그 외 WARN. 반환: 정상 여부."""
        if isinstance(result, list):
            return True  # pages는 get_all_pages가 이미 검증
        status = result.get("status", 0) if isinstance(result, dict) else 0
        if 200 <= status < 300 or status == 0:
            return True
        body = result.get("body", "") if isinstance(result, dict) else ""
        msg = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
        print(f"  WARN [{mall_id}] {name} status={status}: {msg[:200]}", file=sys.stderr)
        return False

    for mall_id in available:
        try:
            # 1. dailysales (dry-run 단일 호출 대상)
            daily = fetch_dailysales(client, mall_id, start_str, end_str)
            _save_json(out_dir / f"dailysales_{mall_id}.json", daily)
            ok = _check_status("dailysales", mall_id, daily)
            print(f"  [{mall_id}] dailysales {'OK' if ok else 'WARN'}")

            if args.dry_run:
                continue

            # 2. monthlysales (이전 달까지만 자동 조정)
            monthly = fetch_monthlysales(client, mall_id, start_str, end_str)
            _save_json(out_dir / f"monthlysales_{mall_id}.json", monthly)
            if not isinstance(monthly, dict) or not monthly.get("_skipped"):
                _check_status("monthlysales", mall_id, monthly)

            # 3. hourlysales (페이지네이션)
            hourly_pages = fetch_hourlysales(client, mall_id, start_str, end_str)
            _save_json(out_dir / f"hourlysales_{mall_id}.json", hourly_pages)
            print(f"  [{mall_id}] hourlysales pages={len(hourly_pages) if isinstance(hourly_pages, list) else 1}")

            # 4. productsales (페이지네이션)
            product_pages = fetch_productsales(client, mall_id, start_str, end_str)
            _save_json(out_dir / f"productsales_{mall_id}.json", product_pages)
            print(f"  [{mall_id}] productsales pages={len(product_pages) if isinstance(product_pages, list) else 1}")

            # 5. salesvolume (옵션 — product_no 필수, 미제공 시 skip)
            if args.product_no:
                vol = fetch_salesvolume(client, mall_id, start_str, end_str, args.product_no)
                if vol is not None:
                    _save_json(out_dir / f"salesvolume_{mall_id}.json", vol)

            print(f"  [{mall_id}] all reports OK")

        except Cafe24TokenExpired as e:
            print(f"AUTH FAIL: mall={mall_id} status={e.status}", file=sys.stderr)
            failed_auth.append(mall_id)
        except Cafe24RateLimited as e:
            print(f"RATE LIMITED: mall={mall_id} remain={e.remain_seconds}s — abort",
                  file=sys.stderr)
            return 4

    if failed_auth:
        print(f"AUTH FAIL: refresh tokens for {failed_auth} and re-run", file=sys.stderr)
        return 2

    print(f"[fetch_salesreport] done. raw → {out_dir}")
    return 0


def build_parser():
    p = argparse.ArgumentParser(description="Cafe24 salesreport fetcher")
    p.add_argument("--brand", default="all")
    p.add_argument("--period-start")
    p.add_argument("--period-end")
    p.add_argument("--out-dir", default="./reports/cafe24/all/_latest/data/raw")
    p.add_argument("--product-no", help="salesvolume 호출 시 필요한 상품번호")
    p.add_argument("--dry-run", action="store_true",
                   help="dailysales 1회만 호출 후 종료")
    return p


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
