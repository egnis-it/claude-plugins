#!/usr/bin/env python3
"""
scripts/fetch_products.py — cafe24-products-export skill용 (Tier 2 snapshot)

Plan: .omc/plans/cafe24-mcp-skills-v3.md §7 Phase 4.A
ADR-003 §3.2: urllib only

Endpoints:
  GET /api/v2/admin/products/count            (dry-run 단일)
  GET /api/v2/admin/products                  (페이지네이션)
  GET /api/v2/admin/products/{no}/variants    (--include-variants)

SCOPE: mall.read_product
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from lib.cafe24_client import Cafe24Client, Cafe24RateLimited, Cafe24TokenExpired  # noqa: E402
from lib.brand_registry import resolve_brand  # noqa: E402

# Live 검증 (2026-05-18): /admin/products는 limit 최대 100 (422 "should be between 1 and 100").
# orders와 달리 별도 한도. count=188 → 최소 2 페이지 필요.
PRODUCTS_LIMIT = 100
PRODUCT_FIELDS = (
    "product_no,product_code,product_name,price,supply_price,retail_price,"
    "display,selling,price_excluding_tax,description,category_no,product_tag,"
    "brand_code,manufacturer_code,supplier_code"
)


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


def fetch_count(client, mall_id):
    return client.get(mall_id, "/api/v2/admin/products/count")


def fetch_products(client, mall_id, total_count: int | None = None):
    """전체 상품 페이지네이션. limit=100 (cafe24 강제 한도).

    Live 검증 (2026-05-18): /admin/products 응답에 'links.next' 부재 (orders와 다름).
    offset 기반 수동 페이지네이션 사용.

    total_count: count endpoint에서 얻은 총 상품 수. 페이지 한도 산정용.
    """
    pages: list[dict] = []
    # 안전 한도: total_count + 여유 1 페이지, 또는 50K
    max_iter = (((total_count or 50_000) // PRODUCTS_LIMIT) + 2) if total_count else 500
    for i in range(max_iter):
        offset = i * PRODUCTS_LIMIT
        result = client.get(
            mall_id, "/api/v2/admin/products",
            params={"limit": str(PRODUCTS_LIMIT), "offset": str(offset)},
        )
        body = result.get("body", {}) if isinstance(result, dict) else {}
        if not isinstance(body, dict):
            # 422 등 에러
            pages.append({"raw": str(body)[:300], "status": result.get("status")})
            break
        products = body.get("products", [])
        if not products:
            break  # 더 이상 상품 없음
        pages.append(body)
        if len(products) < PRODUCTS_LIMIT:
            break  # 마지막 페이지
    return pages


def fetch_variants(client, mall_id, product_no):
    """특정 상품의 품목 (--include-variants)."""
    return client.get(
        mall_id, f"/api/v2/admin/products/{product_no}/variants"
    )


def run(args) -> int:
    try:
        mall_ids = resolve_brand(args.brand)
    except KeyError as e:
        print(f"ERROR: unknown brand alias: {e}", file=sys.stderr)
        return 1

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

    print(f"[fetch_products] brands={available} dry_run={args.dry_run} include_variants={args.include_variants}")

    failed_auth: list[str] = []
    for mall_id in available:
        try:
            count = fetch_count(client, mall_id)
            count_value = count.get("body", {}).get("count", None) if isinstance(count.get("body"), dict) else None
            _save_json(out_dir / f"products_count_{mall_id}.json", count)
            print(f"  [{mall_id}] product count={count_value if count_value is not None else 'N/A'}")

            if args.dry_run:
                continue

            pages = fetch_products(client, mall_id, total_count=count_value)
            _save_json(out_dir / f"products_{mall_id}.json", pages)
            total = sum(len(p.get("products", [])) for p in pages if isinstance(p, dict))
            print(f"  [{mall_id}] pages={len(pages)} products={total}")

            if args.include_variants:
                # 상품별 variants 조회 (호출량 큼 — 옵션)
                variants_results = []
                product_nos = []
                for page in pages:
                    for prod in page.get("products", []):
                        pn = prod.get("product_no")
                        if pn is not None:
                            product_nos.append(pn)
                # 안전 한도: 변형 조회는 상품당 1콜이므로 최대 200개로 제한
                for pn in product_nos[:200]:
                    try:
                        v = fetch_variants(client, mall_id, pn)
                        variants_results.append({"product_no": pn, "variants": v.get("body", {}).get("variants", [])})
                    except (Cafe24TokenExpired, Cafe24RateLimited):
                        raise
                    except Exception as e:
                        print(f"  WARN [{mall_id}] variants for {pn} failed: {e}", file=sys.stderr)
                _save_json(out_dir / f"products_variants_{mall_id}.json", variants_results)

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

    print(f"[fetch_products] done. raw → {out_dir}")
    return 0


def build_parser():
    p = argparse.ArgumentParser(description="Cafe24 products fetcher (Tier 2 snapshot)")
    p.add_argument("--brand", default="all")
    p.add_argument("--out-dir", default="./reports/cafe24/all/_snapshot/data/raw")
    p.add_argument("--include-variants", action="store_true",
                   help="상품별 품목 추가 호출 (최대 200건)")
    p.add_argument("--dry-run", action="store_true",
                   help="products/count 1회만 호출 후 종료")
    return p


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
