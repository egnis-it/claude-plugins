#!/usr/bin/env python3
"""
scripts/build_orders_dashboard.py — orders raw JSON → CSV + dashboard.html

Plan: .omc/plans/cafe24-mcp-skills-v3.md §7 Phase 3
ADR-003 §1: dashboard_template.render_dashboard 함수형 진입점만 사용 (외부 HTML X)

입력: fetch_orders.py가 만든 raw 디렉토리 (orders_<mall>.json, orders_count_<mall>.json)
산출:
  <out-dir>/data/orders_summary.csv     (일자별 집계)
  <out-dir>/data/orders_detail.csv      (옵션 --with-detail, 주문 단건)
  <out-dir>/dashboard.html              (render_dashboard layout=timeseries)
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

from lib.brand_registry import MALL_LABELS, MALL_ORDER, label_for  # noqa: E402
from lib.dashboard_template import render_dashboard  # noqa: E402

CAFE24_API_VERSION = "2026-03-01"

# 주문 상태 → 분류 (집계용)
CANCELED_STATUSES = {
    "C00", "C10", "C11", "C34", "C35", "C36", "C40", "C41", "C42", "C43",
    "C47", "C48", "C49",
}
PAID_INDICATORS = {"T"}  # paid == "T"


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _read_orders_pages(raw_dir: Path, mall_id: str) -> list[dict]:
    """orders_<mall>.json 읽어 모든 주문 평탄화."""
    path = raw_dir / f"orders_{mall_id}.json"
    if not path.exists():
        return []
    pages = json.loads(path.read_text(encoding="utf-8"))
    orders: list[dict] = []
    for page in pages:
        if isinstance(page, dict):
            page_orders = page.get("orders", [])
            if isinstance(page_orders, list):
                orders.extend(page_orders)
    return orders


def _read_count(raw_dir: Path, mall_id: str) -> int | None:
    path = raw_dir / f"orders_count_{mall_id}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    body = data.get("body", {})
    if isinstance(body, dict):
        return body.get("count")
    return None


def _safe_float(v) -> float:
    try:
        return float(v) if v not in (None, "") else 0.0
    except (ValueError, TypeError):
        return 0.0


def _order_date_key(order: dict) -> str | None:
    """주문일을 YYYY-MM-DD로 추출."""
    date_str = order.get("order_date") or order.get("payment_date")
    if not date_str:
        return None
    # ISO8601 → 날짜 부분만
    return str(date_str)[:10]


def _is_canceled(order: dict) -> bool:
    canceled = order.get("canceled")
    return canceled == "T"


def _order_amount(order: dict) -> float:
    """주문 금액 (실결제금액 우선, fallback to payment_amount)."""
    actual = order.get("actual_order_amount") or {}
    if isinstance(actual, dict):
        amt = actual.get("payment_amount")
        if amt:
            return _safe_float(amt)
    return _safe_float(order.get("payment_amount"))


def aggregate_per_day(orders: list[dict]) -> dict[str, dict]:
    """일자별 집계: {date: {order_count, canceled_count, paid_count, sales}}."""
    by_day: dict[str, dict] = {}
    for o in orders:
        day = _order_date_key(o)
        if not day:
            continue
        bucket = by_day.setdefault(day, {
            "order_count": 0,
            "canceled_count": 0,
            "paid_count": 0,
            "sales": 0.0,
        })
        bucket["order_count"] += 1
        if _is_canceled(o):
            bucket["canceled_count"] += 1
        else:
            # 미취소만 매출 합산
            bucket["sales"] += _order_amount(o)
        if o.get("paid") in PAID_INDICATORS:
            bucket["paid_count"] += 1
    return by_day


def aggregate_total(by_day: dict[str, dict]) -> dict:
    """전체 KPI 합계."""
    total_orders = sum(d["order_count"] for d in by_day.values())
    total_canceled = sum(d["canceled_count"] for d in by_day.values())
    total_paid = sum(d["paid_count"] for d in by_day.values())
    total_sales = sum(d["sales"] for d in by_day.values())
    refund_rate = (total_canceled / total_orders * 100.0) if total_orders else 0.0
    avg_order_value = (total_sales / total_paid) if total_paid else 0.0
    return {
        "order_count": total_orders,
        "canceled_count": total_canceled,
        "paid_count": total_paid,
        "sales": total_sales,
        "refund_rate": refund_rate,
        "avg_order_value": avg_order_value,
    }


def write_summary_csv(out_path: Path, mall_summaries: dict[str, dict[str, dict]]) -> int:
    """orders_summary.csv: brand,date,order_count,canceled_count,paid_count,refund_rate,sales"""
    _ensure_dir(out_path.parent)
    rows_written = 0
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["brand", "date", "order_count", "canceled_count", "paid_count",
                    "refund_rate_pct", "sales"])
        for mall_id, by_day in mall_summaries.items():
            for day in sorted(by_day.keys()):
                d = by_day[day]
                rate = (d["canceled_count"] / d["order_count"] * 100.0) if d["order_count"] else 0.0
                w.writerow([
                    mall_id, day,
                    d["order_count"], d["canceled_count"], d["paid_count"],
                    f"{rate:.2f}", f"{d['sales']:.2f}",
                ])
                rows_written += 1
    return rows_written


def write_detail_csv(out_path: Path, mall_orders: dict[str, list[dict]]) -> int:
    """orders_detail.csv: brand,order_id,payment_date,paid,canceled,payment_amount,buyer_email_masked"""
    _ensure_dir(out_path.parent)
    rows_written = 0
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["brand", "order_id", "order_date", "payment_date",
                    "paid", "canceled", "payment_amount", "buyer_email_masked"])
        for mall_id, orders in mall_orders.items():
            for o in orders:
                email = o.get("member_email") or ""
                masked = _mask_email(email)
                w.writerow([
                    mall_id,
                    o.get("order_id", ""),
                    o.get("order_date", ""),
                    o.get("payment_date", ""),
                    o.get("paid", ""),
                    o.get("canceled", ""),
                    f"{_order_amount(o):.2f}",
                    masked,
                ])
                rows_written += 1
    return rows_written


def _mask_email(email: str) -> str:
    """이메일 마스킹: a***@example.com."""
    if "@" not in email:
        return email
    local, _, domain = email.partition("@")
    if len(local) <= 1:
        return f"{local}@{domain}"
    return f"{local[0]}***@{domain}"


def _format_won(amount: float) -> str:
    return f"₩{int(round(amount)):,}"


def _format_pct(value: float) -> str:
    return f"{value:.2f}%"


def build_dashboard(
    mall_summaries: dict[str, dict[str, dict]],
    mall_totals: dict[str, dict],
    brand_label: str,
    period_start: str,
    period_end: str,
    counts: dict[str, int | None],
) -> str:
    """render_dashboard 호출."""
    # 전체 합계
    overall = {
        "order_count": sum(t["order_count"] for t in mall_totals.values()),
        "canceled_count": sum(t["canceled_count"] for t in mall_totals.values()),
        "paid_count": sum(t["paid_count"] for t in mall_totals.values()),
        "sales": sum(t["sales"] for t in mall_totals.values()),
    }
    overall["refund_rate"] = (
        (overall["canceled_count"] / overall["order_count"] * 100.0)
        if overall["order_count"] else 0.0
    )
    overall["avg_order_value"] = (
        (overall["sales"] / overall["paid_count"])
        if overall["paid_count"] else 0.0
    )

    kpis = [
        {"label": "기간 주문 건수", "value": f"{overall['order_count']:,}건",
         "delta": None, "accent": "neutral"},
        {"label": "기간 GMV", "value": _format_won(overall["sales"]),
         "delta": None, "accent": "neutral"},
        {"label": "결제 건수", "value": f"{overall['paid_count']:,}건",
         "delta": None, "accent": "neutral"},
        {"label": "평균 주문 단가", "value": _format_won(overall["avg_order_value"]),
         "delta": None, "accent": "neutral"},
        {"label": "환불률", "value": _format_pct(overall["refund_rate"]),
         "delta": None,
         "accent": "negative" if overall["refund_rate"] > 5.0 else "positive"},
    ]

    # 일자별 표 (전체 합산)
    all_dates: set[str] = set()
    for by_day in mall_summaries.values():
        all_dates.update(by_day.keys())
    sorted_dates = sorted(all_dates)

    rows: list[list] = []
    for day in sorted_dates:
        order_sum = sum(mall_summaries.get(m, {}).get(day, {}).get("order_count", 0)
                        for m in mall_summaries)
        cancel_sum = sum(mall_summaries.get(m, {}).get(day, {}).get("canceled_count", 0)
                         for m in mall_summaries)
        sales_sum = sum(mall_summaries.get(m, {}).get(day, {}).get("sales", 0.0)
                        for m in mall_summaries)
        rate = (cancel_sum / order_sum * 100.0) if order_sum else 0.0
        rows.append([day, order_sum, cancel_sum, round(rate, 2), int(round(sales_sum))])

    table = {
        "columns": ["일시", "주문수", "취소수", "취소율(%)", "매출액"],
        "rows": rows,
        "csv_filename": "orders_summary.csv",
    }

    # 추이 라인 차트 — 취소율 추이
    chart = {
        "type": "line",
        "x_labels": sorted_dates,
        "series": [{
            "label": "취소율(%)",
            "data": [
                round(
                    (sum(mall_summaries.get(m, {}).get(d, {}).get("canceled_count", 0)
                         for m in mall_summaries)
                     / max(1, sum(mall_summaries.get(m, {}).get(d, {}).get("order_count", 0)
                                  for m in mall_summaries))
                     ) * 100.0,
                    2,
                )
                for d in sorted_dates
            ],
        }],
        "options": {"responsive": True, "maintainAspectRatio": False},
    }

    # brand 토글 데이터
    brands: list[dict] = [{
        "mall_id": "_all",
        "label": brand_label or "전체",
        "data": {"totals": overall, "by_day": _bind_all_by_day(mall_summaries)},
    }]
    for mall_id in MALL_ORDER:
        if mall_id in mall_summaries:
            brands.append({
                "mall_id": mall_id,
                "label": label_for(mall_id),
                "data": {
                    "totals": mall_totals.get(mall_id, {}),
                    "by_day": mall_summaries.get(mall_id, {}),
                    "count_endpoint": counts.get(mall_id),
                },
            })

    meta = {
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "cafe24api",
        "period": f"{period_start} ~ {period_end}",
        "version": "v3",
        "api_version": CAFE24_API_VERSION,
    }

    return render_dashboard(
        layout="timeseries",
        title=f"Cafe24 주문 데이터 — {brand_label}",
        brands=brands,
        kpis=kpis,
        table=table,
        chart=chart,
        meta=meta,
    )


def _bind_all_by_day(mall_summaries: dict[str, dict[str, dict]]) -> dict[str, dict]:
    """전체 brand의 일자별 합산 데이터 생성."""
    combined: dict[str, dict] = {}
    for by_day in mall_summaries.values():
        for day, d in by_day.items():
            bucket = combined.setdefault(day, {
                "order_count": 0, "canceled_count": 0,
                "paid_count": 0, "sales": 0.0,
            })
            bucket["order_count"] += d["order_count"]
            bucket["canceled_count"] += d["canceled_count"]
            bucket["paid_count"] += d["paid_count"]
            bucket["sales"] += d["sales"]
    return combined


def run(args: argparse.Namespace) -> int:
    raw_dir = Path(args.raw_dir)
    out_dir = Path(args.out_dir)
    if not raw_dir.exists():
        print(f"ERROR: raw_dir not found: {raw_dir}", file=sys.stderr)
        return 1

    # 모든 mall_id 검색 (orders_<mall>.json 패턴)
    mall_ids: list[str] = []
    for f in sorted(raw_dir.glob("orders_*.json")):
        name = f.stem
        if name.startswith("orders_count_"):
            continue
        mall_id = name[len("orders_"):]
        if mall_id:
            mall_ids.append(mall_id)
    if not mall_ids:
        print(f"WARN: no orders_*.json found in {raw_dir}", file=sys.stderr)

    mall_summaries: dict[str, dict[str, dict]] = {}
    mall_totals: dict[str, dict] = {}
    mall_orders: dict[str, list[dict]] = {}
    counts: dict[str, int | None] = {}

    for mall_id in mall_ids:
        orders = _read_orders_pages(raw_dir, mall_id)
        mall_orders[mall_id] = orders
        by_day = aggregate_per_day(orders)
        mall_summaries[mall_id] = by_day
        mall_totals[mall_id] = aggregate_total(by_day)
        counts[mall_id] = _read_count(raw_dir, mall_id)

    # CSV 산출
    summary_path = out_dir / "data" / "orders_summary.csv"
    summary_rows = write_summary_csv(summary_path, mall_summaries)
    print(f"  orders_summary.csv: {summary_rows} rows → {summary_path}")

    if args.with_detail:
        detail_path = out_dir / "data" / "orders_detail.csv"
        detail_rows = write_detail_csv(detail_path, mall_orders)
        print(f"  orders_detail.csv: {detail_rows} rows → {detail_path}")

    # HTML 산출
    html = build_dashboard(
        mall_summaries, mall_totals,
        brand_label=args.brand_label or "전체",
        period_start=args.period_start,
        period_end=args.period_end,
        counts=counts,
    )
    dashboard_path = out_dir / "dashboard.html"
    _ensure_dir(dashboard_path.parent)
    dashboard_path.write_text(html, encoding="utf-8")
    print(f"  dashboard.html → {dashboard_path}")

    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="orders raw → CSV + dashboard.html")
    p.add_argument("--raw-dir", required=True, help="fetch_orders.py 출력 디렉토리")
    p.add_argument("--out-dir", required=True, help="dashboard.html + data/ 출력 디렉토리")
    p.add_argument("--brand-label", default="전체", help="표시용 브랜드 라벨")
    p.add_argument("--period-start", required=True, help="YYYY-MM-DD")
    p.add_argument("--period-end", required=True, help="YYYY-MM-DD")
    p.add_argument("--with-detail", action="store_true", help="주문 단건 detail CSV도 생성")
    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    raise SystemExit(run(args))
