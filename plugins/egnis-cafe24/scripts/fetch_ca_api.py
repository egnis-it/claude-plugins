#!/usr/bin/env python3
"""
scripts/fetch_ca_api.py — cafe24-ca-api-export skill용 (베타)

Plan: .omc/plans/cafe24-mcp-skills-v3.md §7 Phase 4.D
ADR-003 §3.2: urllib only

CA API endpoints (ca-api.cafe24data.com, 베타):
  GET /products/hits          (상품별 노출수)
  GET /carts/action           (장바구니 담김 액션)
  GET /products/sales         (상품별 판매건수)

베타 변경 발견 시 endpoint path는 본 파일에서만 수정 (ADR-003 단일 진입점).
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

# CA API endpoint paths (Live 검증 2026-05-18 결과 반영)
# - /products/hits: 404 No endpoint (실제 endpoint 미존재)
# - /carts/action: 200 — 노출수(count) + 담김수(add_cart_count) + add_cart_rate(전환율) 모두 함께 응답.
#   즉 단일 endpoint로 hits + cart 정보 둘 다 커버됨.
# - /products/sales: 200 — product_no/name/order_count/order_product_count/order_amount 응답.
CA_CART_ACTIONS_PATH = "/carts/action"
CA_PRODUCT_SALES_PATH = "/products/sales"


def _default_period() -> tuple[date, date]:
    """CA API는 일 단위 집계 → 최근 30일 기본."""
    end = date.today()
    return end - timedelta(days=29), end


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
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)


def fetch_cart_actions(client, mall_id, start, end):
    """장바구니 액션 (노출수+담김수 통합). 베타.

    Live 검증 (2026-05-18): 단일 endpoint로 hits + cart 모두 응답.
    응답 키: 'action' (list of {product_no, product_name, count, add_cart_count, add_cart_rate}).
    """
    return client.get_ca(mall_id, CA_CART_ACTIONS_PATH,
                         params={"start_date": start, "end_date": end})


def fetch_product_sales(client, mall_id, start, end):
    """상품별 판매건수. 베타.

    Live 검증 (2026-05-18): 응답 키 'sales' (list of
    {product_no, product_name, order_count, order_product_count, order_amount}).
    """
    return client.get_ca(mall_id, CA_PRODUCT_SALES_PATH,
                         params={"start_date": start, "end_date": end})


def run(args) -> int:
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

    print(f"[fetch_ca_api] (베타) brands={available} period={start_str}~{end_str} dry_run={args.dry_run}")

    def _check_ca_status(name: str, mall_id: str, result, beta_failures: list[str]) -> bool:
        """CA 응답 status 검증. 4xx/5xx면 graceful degrade로 _beta_unavailable 저장."""
        status = result.get("status", 0) if isinstance(result, dict) else 0
        if 200 <= status < 300:
            return True
        body = result.get("body", "") if isinstance(result, dict) else ""
        msg = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
        print(f"WARN [{mall_id}] {name} status={status}: {msg[:200]}", file=sys.stderr)
        # graceful degrade: 응답을 _beta_unavailable로 표시
        result.clear() if isinstance(result, dict) else None
        if isinstance(result, dict):
            result.update({"_beta_unavailable": True, "status": status,
                          "error": msg[:200], "endpoint": name})
        beta_failures.append(f"{mall_id}/{name}")
        return False

    failed_auth: list[str] = []
    beta_failures: list[str] = []  # 베타 endpoint 깨졌을 때 graceful degrade

    for mall_id in available:
        try:
            # 1. carts/action (dry-run 단일 호출 대상 — hits + cart 통합)
            try:
                cart = fetch_cart_actions(client, mall_id, start_str, end_str)
                _check_ca_status("cart_actions", mall_id, cart, beta_failures)
                _save_json(out_dir / f"cart_actions_{mall_id}.json", cart)
                if 200 <= (cart.get("status", 0) if isinstance(cart, dict) else 0) < 300:
                    print(f"  [{mall_id}] cart_actions OK (hits + cart 통합)")
            except (Cafe24TokenExpired, Cafe24RateLimited):
                raise
            except Exception as e:
                print(f"WARN [{mall_id}] cart_actions exception: {e}", file=sys.stderr)
                _save_json(out_dir / f"cart_actions_{mall_id}.json",
                           {"_beta_unavailable": True, "error": str(e)})
                beta_failures.append(f"{mall_id}/cart_actions")

            if args.dry_run:
                continue

            # 2. products/sales
            try:
                psales = fetch_product_sales(client, mall_id, start_str, end_str)
                _check_ca_status("product_sales", mall_id, psales, beta_failures)
                _save_json(out_dir / f"product_sales_{mall_id}.json", psales)
            except (Cafe24TokenExpired, Cafe24RateLimited):
                raise
            except Exception as e:
                print(f"WARN [{mall_id}] product_sales exception: {e}", file=sys.stderr)
                _save_json(out_dir / f"product_sales_{mall_id}.json",
                           {"_beta_unavailable": True, "error": str(e)})
                beta_failures.append(f"{mall_id}/product_sales")

            print(f"  [{mall_id}] all CA endpoints attempted")

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

    if beta_failures:
        print(f"BETA WARN: {len(beta_failures)} endpoint(s) unavailable: {beta_failures[:5]}",
              file=sys.stderr)
    print(f"[fetch_ca_api] done. raw → {out_dir}")
    return 0


def build_parser():
    p = argparse.ArgumentParser(description="Cafe24 CA API fetcher (베타)")
    p.add_argument("--brand", default="all")
    p.add_argument("--period-start")
    p.add_argument("--period-end")
    p.add_argument("--out-dir", default="./reports/cafe24/all/_latest/data/raw")
    p.add_argument("--dry-run", action="store_true",
                   help="products/hits 1회만 호출 후 종료")
    return p


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
