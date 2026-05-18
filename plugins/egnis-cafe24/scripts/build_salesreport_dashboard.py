#!/usr/bin/env python3
"""
scripts/build_salesreport_dashboard.py — salesreport raw → CSV 5종 + 5탭 dashboard.html

Plan: .omc/plans/cafe24-mcp-skills-v3.md §7 Phase 4.B
ADR-003 §1: render_dashboard 함수형 진입점만 사용
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


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _safe_float(v) -> float:
    try:
        return float(v) if v not in (None, "") else 0.0
    except (ValueError, TypeError):
        return 0.0


def _load_resource(raw_dir: Path, prefix: str, mall_id: str):
    """resource raw JSON 로드. pages list 또는 단일 dict 둘 다 처리."""
    path = raw_dir / f"{prefix}_{mall_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _flatten_pages(pages_or_dict, key: str) -> list[dict]:
    """pages list → 모든 rows 평탄화. 단일 dict면 body[key]."""
    rows: list[dict] = []
    if isinstance(pages_or_dict, list):
        for page in pages_or_dict:
            if isinstance(page, dict):
                items = page.get(key, [])
                if isinstance(items, list):
                    rows.extend(items)
    elif isinstance(pages_or_dict, dict):
        body = pages_or_dict.get("body", {})
        if isinstance(body, dict):
            items = body.get(key, [])
            if isinstance(items, list):
                rows.extend(items)
    return rows


# ---- CSV writers ----
def write_dailysales_csv(out_path: Path, mall_data: dict[str, list[dict]]) -> int:
    _ensure_dir(out_path.parent)
    rows_written = 0
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["brand", "date", "shop_no", "payment_amount", "refund_amount", "sales_count"])
        for mall_id, rows in mall_data.items():
            for r in rows:
                w.writerow([
                    mall_id,
                    r.get("date", ""),
                    r.get("shop_no", ""),
                    _safe_float(r.get("payment_amount")),
                    _safe_float(r.get("refund_amount")),
                    r.get("sales_count", 0),
                ])
                rows_written += 1
    return rows_written


def write_monthlysales_csv(out_path: Path, mall_data: dict[str, list[dict]]) -> int:
    _ensure_dir(out_path.parent)
    rows_written = 0
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["brand", "month", "shop_no", "payment_amount", "refund_amount", "sales_count"])
        for mall_id, rows in mall_data.items():
            for r in rows:
                w.writerow([
                    mall_id,
                    r.get("month", ""),
                    r.get("shop_no", ""),
                    _safe_float(r.get("payment_amount")),
                    _safe_float(r.get("refund_amount")),
                    r.get("sales_count", 0),
                ])
                rows_written += 1
    return rows_written


def write_hourlysales_csv(out_path: Path, mall_data: dict[str, list[dict]]) -> int:
    _ensure_dir(out_path.parent)
    rows_written = 0
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["brand", "collection_date", "collection_hour", "order_count", "item_count",
                    "actual_order_amount", "refund_amount", "sales"])
        for mall_id, rows in mall_data.items():
            for r in rows:
                w.writerow([
                    mall_id,
                    r.get("collection_date", ""),
                    r.get("collection_hour", ""),
                    r.get("order_count", 0),
                    r.get("item_count", 0),
                    _safe_float(r.get("actual_order_amount")),
                    _safe_float(r.get("refund_amount")),
                    _safe_float(r.get("sales")),
                ])
                rows_written += 1
    return rows_written


def write_productsales_csv(out_path: Path, mall_data: dict[str, list[dict]]) -> int:
    _ensure_dir(out_path.parent)
    rows_written = 0
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["brand", "collection_date", "collection_hour", "product_no", "variants_code",
                    "product_price", "settle_count", "refund_count", "sale_count",
                    "total_sale_count", "total_cancel_count"])
        for mall_id, rows in mall_data.items():
            for r in rows:
                w.writerow([
                    mall_id,
                    r.get("collection_date", ""),
                    r.get("collection_hour", ""),
                    r.get("product_no", ""),
                    r.get("variants_code", ""),
                    _safe_float(r.get("product_price")),
                    r.get("settle_count", 0),
                    r.get("refund_count", 0),
                    r.get("sale_count", 0),
                    r.get("total_sale_count", 0),
                    r.get("total_cancel_count", 0),
                ])
                rows_written += 1
    return rows_written


def write_salesvolume_csv(out_path: Path, mall_data: dict[str, list[dict]]) -> int:
    _ensure_dir(out_path.parent)
    rows_written = 0
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["brand", "collection_date", "collection_hour", "product_no", "variants_code",
                    "product_price", "settle_count", "total_sales"])
        for mall_id, rows in mall_data.items():
            for r in rows:
                w.writerow([
                    mall_id,
                    r.get("collection_date", ""),
                    r.get("collection_hour", ""),
                    r.get("product_no", ""),
                    r.get("variants_code", ""),
                    _safe_float(r.get("product_price")),
                    r.get("settle_count", 0),
                    r.get("total_sales", 0),
                ])
                rows_written += 1
    return rows_written


def _aggregate_kpis(daily_data: dict[str, list[dict]]) -> dict:
    """전체 합계 KPI 계산."""
    total_payment = 0.0
    total_refund = 0.0
    total_count = 0
    for rows in daily_data.values():
        for r in rows:
            total_payment += _safe_float(r.get("payment_amount"))
            total_refund += _safe_float(r.get("refund_amount"))
            total_count += int(r.get("sales_count") or 0)
    refund_rate = (total_refund / total_payment * 100.0) if total_payment else 0.0
    return {
        "total_payment": total_payment,
        "total_refund": total_refund,
        "total_count": total_count,
        "refund_rate": refund_rate,
        "net_sales": total_payment - total_refund,
    }


def _format_won(amount: float) -> str:
    return f"₩{int(round(amount)):,}"


def build_dashboard(
    daily: dict, monthly: dict, hourly: dict, product: dict, volume: dict,
    brand_label: str, period_start: str, period_end: str,
) -> str:
    kpi_data = _aggregate_kpis(daily)
    kpis = [
        {"label": "총 결제 금액", "value": _format_won(kpi_data["total_payment"]),
         "delta": None, "accent": "neutral"},
        {"label": "순매출 (결제-환불)", "value": _format_won(kpi_data["net_sales"]),
         "delta": None, "accent": "positive"},
        {"label": "환불 금액", "value": _format_won(kpi_data["total_refund"]),
         "delta": None, "accent": "neutral"},
        {"label": "환불률",
         "value": f"{kpi_data['refund_rate']:.2f}%",
         "delta": None,
         "accent": "negative" if kpi_data["refund_rate"] > 5.0 else "positive"},
        {"label": "판매 건수", "value": f"{kpi_data['total_count']:,}건",
         "delta": None, "accent": "neutral"},
    ]

    # 대표 표: dailysales (5탭 중 첫번째)
    all_dates: set[str] = set()
    for rows in daily.values():
        for r in rows:
            d = r.get("date")
            if d:
                all_dates.add(str(d))
    sorted_dates = sorted(all_dates)

    table_rows: list[list] = []
    for d in sorted_dates:
        pay = sum(_safe_float(r.get("payment_amount"))
                  for rows in daily.values() for r in rows if r.get("date") == d)
        refund = sum(_safe_float(r.get("refund_amount"))
                     for rows in daily.values() for r in rows if r.get("date") == d)
        count = sum(int(r.get("sales_count") or 0)
                    for rows in daily.values() for r in rows if r.get("date") == d)
        table_rows.append([d, int(round(pay)), int(round(refund)), count])
    table = {
        "columns": ["일시", "결제 금액", "환불 금액", "판매 건수"],
        "rows": table_rows,
        "csv_filename": "dailysales.csv",
    }

    chart = {
        "type": "line",
        "x_labels": sorted_dates,
        "series": [{
            "label": "결제 금액 (₩)",
            "data": [
                int(round(sum(_safe_float(r.get("payment_amount"))
                              for rows in daily.values() for r in rows if r.get("date") == d)))
                for d in sorted_dates
            ],
        }, {
            "label": "환불 금액 (₩)",
            "data": [
                int(round(sum(_safe_float(r.get("refund_amount"))
                              for rows in daily.values() for r in rows if r.get("date") == d)))
                for d in sorted_dates
            ],
        }],
        "options": {"responsive": True, "maintainAspectRatio": False},
    }

    # 브랜드 토글: 5탭 raw 데이터를 mall별로 인라인
    brands: list[dict] = [{
        "mall_id": "_all",
        "label": brand_label or "전체",
        "data": {
            "totals": kpi_data,
            "dailysales": daily, "monthlysales": monthly,
            "hourlysales": hourly, "productsales": product, "salesvolume": volume,
        },
    }]
    for mall_id in MALL_ORDER:
        if mall_id in daily or mall_id in monthly or mall_id in hourly or mall_id in product:
            brands.append({
                "mall_id": mall_id,
                "label": label_for(mall_id),
                "data": {
                    "dailysales": daily.get(mall_id, []),
                    "monthlysales": monthly.get(mall_id, []),
                    "hourlysales": hourly.get(mall_id, []),
                    "productsales": product.get(mall_id, []),
                    "salesvolume": volume.get(mall_id, []),
                },
            })

    meta = {
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "cafe24api (5 reports)",
        "period": f"{period_start} ~ {period_end}",
        "version": "v3",
        "api_version": CAFE24_API_VERSION,
    }

    return render_dashboard(
        layout="timeseries",
        title=f"Cafe24 매출통계 5종 — {brand_label}",
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

    # mall 발견: dailysales_*.json 패턴
    mall_ids = sorted({
        f.stem[len("dailysales_"):]
        for f in raw_dir.glob("dailysales_*.json")
    })

    daily: dict[str, list[dict]] = {}
    monthly: dict[str, list[dict]] = {}
    hourly: dict[str, list[dict]] = {}
    product: dict[str, list[dict]] = {}
    volume: dict[str, list[dict]] = {}

    for mall_id in mall_ids:
        d = _load_resource(raw_dir, "dailysales", mall_id)
        if d is not None:
            daily[mall_id] = _flatten_pages(d, "dailysales")
        m = _load_resource(raw_dir, "monthlysales", mall_id)
        if m is not None:
            monthly[mall_id] = _flatten_pages(m, "monthlysales")
        h = _load_resource(raw_dir, "hourlysales", mall_id)
        if h is not None:
            hourly[mall_id] = _flatten_pages(h, "hourlysales")
        p = _load_resource(raw_dir, "productsales", mall_id)
        if p is not None:
            product[mall_id] = _flatten_pages(p, "productsales")
        v = _load_resource(raw_dir, "salesvolume", mall_id)
        if v is not None:
            volume[mall_id] = _flatten_pages(v, "salesvolume")

    # CSV 5종
    data_dir = out_dir / "data"
    write_dailysales_csv(data_dir / "dailysales.csv", daily)
    write_monthlysales_csv(data_dir / "monthlysales.csv", monthly)
    write_hourlysales_csv(data_dir / "hourlysales.csv", hourly)
    write_productsales_csv(data_dir / "productsales.csv", product)
    write_salesvolume_csv(data_dir / "salesvolume.csv", volume)
    print(f"  CSV 5종 → {data_dir}")

    # HTML
    html = build_dashboard(
        daily, monthly, hourly, product, volume,
        brand_label=args.brand_label or "전체",
        period_start=args.period_start,
        period_end=args.period_end,
    )
    dashboard_path = out_dir / "dashboard.html"
    _ensure_dir(dashboard_path.parent)
    dashboard_path.write_text(html, encoding="utf-8")
    print(f"  dashboard.html → {dashboard_path}")
    return 0


def build_parser():
    p = argparse.ArgumentParser(description="salesreport raw → CSV 5종 + dashboard.html")
    p.add_argument("--raw-dir", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--brand-label", default="전체")
    p.add_argument("--period-start", required=True)
    p.add_argument("--period-end", required=True)
    return p


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
