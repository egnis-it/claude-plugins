#!/usr/bin/env python3
"""
scripts/build_products_dashboard.py — products raw → CSV + snapshot HTML

Plan: .omc/plans/cafe24-mcp-skills-v3.md §7 Phase 4.A (Tier 2)
ADR-003: render_dashboard(layout="snapshot", ...) — 추이 라인 X, 분포 차트
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from lib.brand_registry import MALL_ORDER, label_for  # noqa: E402
from lib.dashboard_template import render_dashboard  # noqa: E402

CAFE24_API_VERSION = "2026-03-01"

# 가격대 구간 (₩, 분포 차트용)
PRICE_BUCKETS = [
    (0, 10_000, "1만 미만"),
    (10_000, 30_000, "1-3만"),
    (30_000, 50_000, "3-5만"),
    (50_000, 100_000, "5-10만"),
    (100_000, 300_000, "10-30만"),
    (300_000, float("inf"), "30만 이상"),
]


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _safe_float(v) -> float:
    try:
        return float(v) if v not in (None, "") else 0.0
    except (ValueError, TypeError):
        return 0.0


def _load(raw_dir: Path, prefix: str, mall_id: str):
    path = raw_dir / f"{prefix}_{mall_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _flatten_products(pages) -> list[dict]:
    products: list[dict] = []
    if isinstance(pages, list):
        for p in pages:
            if isinstance(p, dict):
                items = p.get("products", [])
                if isinstance(items, list):
                    products.extend(items)
    return products


def _classify_price(price: float) -> str:
    for low, high, label in PRICE_BUCKETS:
        if low <= price < high:
            return label
    return "기타"


def write_products_summary_csv(out_path: Path, mall_data: dict[str, list[dict]]) -> int:
    _ensure_dir(out_path.parent)
    n = 0
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["brand", "product_no", "product_code", "product_name",
                    "price", "supply_price", "retail_price",
                    "display", "selling", "category_no", "brand_code"])
        for mall_id, products in mall_data.items():
            for p in products:
                w.writerow([
                    mall_id,
                    p.get("product_no", ""),
                    p.get("product_code", ""),
                    p.get("product_name", ""),
                    _safe_float(p.get("price")),
                    _safe_float(p.get("supply_price")),
                    _safe_float(p.get("retail_price")),
                    p.get("display", ""),
                    p.get("selling", ""),
                    p.get("category_no", ""),
                    p.get("brand_code", ""),
                ])
                n += 1
    return n


def write_variants_csv(out_path: Path, mall_data: dict[str, list[dict]]) -> int:
    _ensure_dir(out_path.parent)
    n = 0
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["brand", "product_no", "variant_code", "options",
                    "additional_amount", "use_inventory", "display"])
        for mall_id, items in mall_data.items():
            for it in items:
                pn = it.get("product_no")
                for v in it.get("variants", []):
                    opts = v.get("options", [])
                    opt_str = ",".join(f"{o.get('option_name')}:{o.get('option_value')}"
                                       for o in opts if isinstance(o, dict))
                    w.writerow([
                        mall_id, pn,
                        v.get("variant_code", ""),
                        opt_str,
                        _safe_float(v.get("additional_amount")),
                        v.get("use_inventory", ""),
                        v.get("display", ""),
                    ])
                    n += 1
    return n


def _kpi_summary(mall_data: dict[str, list[dict]]) -> dict:
    total = 0
    displayed = 0
    selling = 0
    price_sum = 0.0
    for products in mall_data.values():
        for p in products:
            total += 1
            if p.get("display") == "T":
                displayed += 1
            if p.get("selling") == "T":
                selling += 1
            price_sum += _safe_float(p.get("price"))
    avg_price = (price_sum / total) if total else 0.0
    return {
        "total": total,
        "displayed": displayed,
        "selling": selling,
        "avg_price": avg_price,
        "display_rate": (displayed / total * 100.0) if total else 0.0,
    }


def _price_distribution(mall_data: dict[str, list[dict]]) -> dict[str, int]:
    dist: dict[str, int] = {label: 0 for _, _, label in PRICE_BUCKETS}
    for products in mall_data.values():
        for p in products:
            price = _safe_float(p.get("price"))
            label = _classify_price(price)
            dist[label] = dist.get(label, 0) + 1
    return dist


def build_dashboard(
    mall_data: dict[str, list[dict]],
    brand_label: str,
    snapshot_date: str,
) -> str:
    kpi = _kpi_summary(mall_data)
    dist = _price_distribution(mall_data)

    kpis = [
        {"label": "총 상품 수", "value": f"{kpi['total']:,}개",
         "delta": None, "accent": "neutral"},
        {"label": "진열 상품 (display=T)",
         "value": f"{kpi['displayed']:,}개 ({kpi['display_rate']:.1f}%)",
         "delta": None, "accent": "neutral"},
        {"label": "판매 활성 (selling=T)",
         "value": f"{kpi['selling']:,}개",
         "delta": None, "accent": "neutral"},
        {"label": "평균 판매가",
         "value": f"₩{int(round(kpi['avg_price'])):,}",
         "delta": None, "accent": "neutral"},
    ]

    # 상위 N개 상품 표 (snapshot — 현재 상태)
    all_products: list[tuple[str, dict]] = []
    for mall_id, plist in mall_data.items():
        for p in plist:
            all_products.append((mall_id, p))
    # 판매가 내림차순 상위 100개
    all_products.sort(key=lambda x: _safe_float(x[1].get("price")), reverse=True)

    rows: list[list] = []
    for mall_id, p in all_products[:100]:
        rows.append([
            mall_id,
            p.get("product_no", ""),
            p.get("product_code", ""),
            (p.get("product_name") or "")[:50],
            int(round(_safe_float(p.get("price")))),
            p.get("display", ""),
            p.get("selling", ""),
        ])
    table = {
        "columns": ["brand", "상품번호", "상품코드", "상품명", "판매가", "진열", "판매"],
        "rows": rows,
        "csv_filename": "products_summary.csv",
    }

    # 분포 차트 (snapshot Tier 2 — bar, line 부재)
    chart = {
        "type": "bar",
        "x_labels": [label for _, _, label in PRICE_BUCKETS],
        "series": [{
            "label": "상품 수",
            "data": [dist.get(label, 0) for _, _, label in PRICE_BUCKETS],
        }],
        "options": {"responsive": True, "maintainAspectRatio": False},
    }

    brands: list[dict] = [{
        "mall_id": "_all",
        "label": brand_label or "전체",
        "data": {"kpis": kpi, "price_dist": dist},
    }]
    for mall_id in MALL_ORDER:
        if mall_id in mall_data:
            mall_kpi = _kpi_summary({mall_id: mall_data[mall_id]})
            mall_dist = _price_distribution({mall_id: mall_data[mall_id]})
            brands.append({
                "mall_id": mall_id,
                "label": label_for(mall_id),
                "data": {"kpis": mall_kpi, "price_dist": mall_dist},
            })

    meta = {
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "cafe24api (products)",
        "period": f"snapshot @ {snapshot_date}",
        "version": "v3",
        "api_version": CAFE24_API_VERSION,
    }

    return render_dashboard(
        layout="snapshot",  # Tier 2: 추이 라인 부재, 분포 차트
        title=f"Cafe24 상품 현황 — {brand_label}",
        brands=brands,
        kpis=kpis,
        table=table,
        chart=chart,
        meta=meta,
    )


def run(args) -> int:
    raw_dir = Path(args.raw_dir)
    out_dir = Path(args.out_dir)
    if not raw_dir.exists():
        print(f"ERROR: raw_dir not found: {raw_dir}", file=sys.stderr)
        return 1

    mall_ids = sorted({
        f.stem[len("products_"):]
        for f in raw_dir.glob("products_*.json")
        if not f.stem.startswith("products_count_")
        and not f.stem.startswith("products_variants_")
    })

    mall_data: dict[str, list[dict]] = {}
    mall_variants: dict[str, list[dict]] = {}
    for mall_id in mall_ids:
        pages = _load(raw_dir, "products", mall_id)
        if pages is not None:
            mall_data[mall_id] = _flatten_products(pages)
        v = _load(raw_dir, "products_variants", mall_id)
        if v is not None:
            mall_variants[mall_id] = v if isinstance(v, list) else []

    # CSV 산출
    data_dir = out_dir / "data"
    summary_rows = write_products_summary_csv(data_dir / "products_summary.csv", mall_data)
    print(f"  products_summary.csv: {summary_rows} rows")

    if mall_variants:
        var_rows = write_variants_csv(data_dir / "products_variants.csv", mall_variants)
        print(f"  products_variants.csv: {var_rows} rows")

    snapshot_date = dt.date.today().isoformat()
    html = build_dashboard(mall_data, args.brand_label or "전체", snapshot_date)
    dashboard_path = out_dir / "dashboard.html"
    _ensure_dir(dashboard_path.parent)
    dashboard_path.write_text(html, encoding="utf-8")
    print(f"  dashboard.html → {dashboard_path}")
    return 0


def build_parser():
    p = argparse.ArgumentParser(description="products raw → CSV + snapshot dashboard")
    p.add_argument("--raw-dir", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--brand-label", default="전체")
    return p


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
