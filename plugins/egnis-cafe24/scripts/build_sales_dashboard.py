#!/usr/bin/env python3
"""
build_sales_dashboard.py

Reads raw Cafe24 MCP JSON dumps from <raw-dir>, aggregates daily sales,
writes sales_daily.csv (+ sales_by_mall.csv if multi-mall), and renders
dashboard.html from the template.

Inputs in raw-dir:
  hourlysales_<mall_id>.json              (one per mall, contains all days)
  members_sales_<mall_id>_<YYYY-MM-DD>.json (one per mall per day)

Usage:
  python3 build_sales_dashboard.py \
    --raw-dir ./reports/cafe24/labnosh/2026-05-12_to_2026-05-18/data/raw \
    --out-dir ./reports/cafe24/labnosh/2026-05-12_to_2026-05-18 \
    --brand-label 한끼통살 \
    --period-start 2026-05-12 \
    --period-end   2026-05-18
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path

MALL_LABELS = {
    "cloop": "클룹",
    "sprint": "스프린트",
    "labnosh": "한끼통살",
    "braye": "브레이",
    "oneday1ball": "오원",
    "groceryseoul": "그로서리서울",
    "exerapy": "엑쎄라피",
    "drlabnosh": "랩노쉬",
    "medileeds": "메디리즈",
}

TEMPLATE_PATH = Path(__file__).parent / "templates" / "sales_dashboard.html"


def daterange(start: dt.date, end: dt.date):
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


def load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[warn] failed to parse {path}: {e}", file=sys.stderr)
        return None


def aggregate_hourlysales(raw: dict) -> dict[str, dict]:
    """
    Returns {date_str: {orders, items, sales, actual, refund}}.
    Sums across hours and shop_no.
    """
    out: dict[str, dict] = {}
    if not raw or not raw.get("ok"):
        return out
    rows = raw.get("data", {}).get("hourlysales", []) or []
    for r in rows:
        date = r.get("collection_date")
        if not date:
            continue
        a = out.setdefault(date, {"orders": 0, "items": 0, "sales": 0, "actual": 0, "refund": 0})
        a["orders"] += int(r.get("order_count") or 0)
        a["items"] += int(r.get("item_count") or 0)
        a["sales"] += float(r.get("sales") or 0)
        a["actual"] += float(r.get("actual_order_amount") or 0)
        a["refund"] += float(r.get("refund_amount") or 0)
    return out


def aggregate_members_sales(raw: dict) -> dict[str, int]:
    """
    Returns {date_str: buyers_count} (member_order_count + nonmember_order_count).
    Note: members/sales returns 1 row per call (period totals), so caller must
    invoke per-day for daily granularity.
    """
    if not raw or not raw.get("ok"):
        return {}
    rows = raw.get("data", {}).get("sales", []) or []
    if not rows:
        return {}
    # Single-day call: 1 row aggregated for the date supplied
    row = rows[0]
    m = int(row.get("member_order_count") or 0)
    n = int(row.get("nonmember_order_count") or 0)
    # date is encoded in the filename; caller resolves
    return {"_total": m + n}


def build(args) -> int:
    raw_dir = Path(args.raw_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir = out_dir / "data"
    data_dir.mkdir(exist_ok=True)

    period_start = dt.date.fromisoformat(args.period_start)
    period_end = dt.date.fromisoformat(args.period_end)
    days = [d.isoformat() for d in daterange(period_start, period_end)]

    # Discover all mall_ids present in raw dir
    mall_ids: set[str] = set()
    for p in raw_dir.glob("hourlysales_*.json"):
        mall_ids.add(p.stem.replace("hourlysales_", ""))
    for p in raw_dir.glob("members_sales_*.json"):
        # filename: members_sales_<mall>_<date>.json
        stem = p.stem.replace("members_sales_", "")
        # mall_id might contain underscore? our mall_ids don't, so split rfind '_'
        idx = stem.rfind("_")
        if idx > 0:
            mall_ids.add(stem[:idx])
    mall_ids_list = sorted(mall_ids)
    if not mall_ids_list:
        print(f"[error] no raw JSON files found in {raw_dir}", file=sys.stderr)
        return 1

    # Per-mall daily aggregates
    # mall_data[mall_id][date] = {orders, items, sales, buyers, actual, refund}
    mall_data: dict[str, dict[str, dict]] = {}
    for mall in mall_ids_list:
        hs = load_json(raw_dir / f"hourlysales_{mall}.json")
        agg = aggregate_hourlysales(hs)
        per_day = {}
        for d in days:
            row = agg.get(d, {"orders": 0, "items": 0, "sales": 0, "actual": 0, "refund": 0})
            # buyers from per-day members/sales
            ms = load_json(raw_dir / f"members_sales_{mall}_{d}.json")
            buyers = 0
            if ms:
                buyers = aggregate_members_sales(ms).get("_total", 0)
            per_day[d] = {**row, "buyers": buyers}
        mall_data[mall] = per_day

    # Combined daily across all malls
    daily_rows = []
    for d in days:
        tot = {"orders": 0, "items": 0, "sales": 0, "buyers": 0}
        for mall in mall_ids_list:
            row = mall_data[mall].get(d, {})
            tot["orders"] += row.get("orders", 0)
            tot["items"] += row.get("items", 0)
            tot["sales"] += row.get("sales", 0)
            tot["buyers"] += row.get("buyers", 0)
        daily_rows.append({"date": d, **tot})

    # Previous-day compare from the FULL window (so the first display day also has compare).
    # We rely on caller to also fetch period_start - 1 day; if absent, compare=None for day 0.
    prev_day = (period_start - dt.timedelta(days=1)).isoformat()
    prev_sales_total = 0
    have_prev = False
    for mall in mall_ids_list:
        hs = load_json(raw_dir / f"hourlysales_{mall}.json")
        agg = aggregate_hourlysales(hs)
        if prev_day in agg:
            prev_sales_total += agg[prev_day]["sales"]
            have_prev = True

    # Fill compare/delta
    last_sales = prev_sales_total if have_prev else None
    for row in daily_rows:
        row["compare"] = last_sales
        row["delta"] = (row["sales"] - last_sales) if last_sales is not None else None
        last_sales = row["sales"]

    # KPI totals
    total_buyers = sum(r["buyers"] for r in daily_rows)
    total_orders = sum(r["orders"] for r in daily_rows)
    total_items = sum(r["items"] for r in daily_rows)
    total_sales = sum(r["sales"] for r in daily_rows)

    # by_mall (only useful if >1 mall)
    by_mall_rows = []
    if len(mall_ids_list) > 1:
        for mall in mall_ids_list:
            agg = {"buyers": 0, "orders": 0, "items": 0, "sales": 0}
            for d in days:
                row = mall_data[mall].get(d, {})
                agg["buyers"] += row.get("buyers", 0)
                agg["orders"] += row.get("orders", 0)
                agg["items"] += row.get("items", 0)
                agg["sales"] += row.get("sales", 0)
            aov = int(agg["sales"] / agg["orders"]) if agg["orders"] else 0
            by_mall_rows.append({
                "mall_id": mall,
                "mall_label": MALL_LABELS.get(mall, mall),
                **agg,
                "aov": aov,
            })
        by_mall_rows.sort(key=lambda r: r["sales"], reverse=True)

    # ---- Write CSVs ----
    csv_path = data_dir / "sales_daily.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["일시", "구매자수", "구매건수", "구매개수", "매출액", "비교값(전일)", "증감"])
        for r in daily_rows:
            w.writerow([
                r["date"], r["buyers"], r["orders"], r["items"],
                int(r["sales"]),
                "" if r["compare"] is None else int(r["compare"]),
                "" if r["delta"] is None else int(r["delta"]),
            ])
        w.writerow([])
        w.writerow(["합계", total_buyers, total_orders, total_items, int(total_sales), "", ""])

    if by_mall_rows:
        by_mall_path = data_dir / "sales_by_mall.csv"
        with by_mall_path.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["자사몰", "mall_id", "구매자수", "구매건수", "구매개수", "매출액", "건당객단가"])
            for r in by_mall_rows:
                w.writerow([
                    r["mall_label"], r["mall_id"], r["buyers"],
                    r["orders"], r["items"], int(r["sales"]), r["aov"],
                ])

    # ---- Render HTML ----
    tpl = TEMPLATE_PATH.read_text(encoding="utf-8")
    period_label = f"{args.period_start} ~ {args.period_end}"
    period_days = (period_end - period_start).days + 1
    generated_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    daily_json = [
        {
            "date": r["date"],
            "buyers": r["buyers"],
            "orders": r["orders"],
            "items": r["items"],
            "sales": int(r["sales"]),
            "compare": None if r["compare"] is None else int(r["compare"]),
            "delta": None if r["delta"] is None else int(r["delta"]),
        }
        for r in daily_rows
    ]
    by_mall_json = [
        {
            "mall_id": r["mall_id"],
            "mall_label": r["mall_label"],
            "buyers": r["buyers"],
            "orders": r["orders"],
            "items": r["items"],
            "sales": int(r["sales"]),
            "aov": r["aov"],
        }
        for r in by_mall_rows
    ]

    html = (
        tpl.replace("__BRAND_LABEL__", args.brand_label)
           .replace("__PERIOD_LABEL__", period_label)
           .replace("__PERIOD_DAYS__", str(period_days))
           .replace("__PERIOD_FILE__", f"{args.period_start}_to_{args.period_end}")
           .replace("__GENERATED_AT__", generated_at)
           .replace("__KPI_BUYERS__", f"{total_buyers:,}")
           .replace("__KPI_ORDERS__", f"{total_orders:,}")
           .replace("__KPI_ITEMS__", f"{total_items:,}")
           .replace("__KPI_SALES__", f"{int(total_sales):,}")
           .replace("__DAILY_JSON__", json.dumps(daily_json, ensure_ascii=False))
           .replace("__BY_MALL_JSON__", json.dumps(by_mall_json, ensure_ascii=False))
    )
    (out_dir / "dashboard.html").write_text(html, encoding="utf-8")

    # ---- Console summary ----
    print(f"✓ dashboard.html  → {out_dir / 'dashboard.html'}")
    print(f"✓ sales_daily.csv → {csv_path}")
    if by_mall_rows:
        print(f"✓ sales_by_mall.csv → {data_dir / 'sales_by_mall.csv'}")
    print()
    print(f"기간 합계: 구매자 {total_buyers:,}명 · "
          f"구매 {total_orders:,}건 · 개수 {total_items:,}개 · "
          f"매출 ₩{int(total_sales):,}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--brand-label", required=True, help="표시용 브랜드 라벨 (예: 한끼통살 / 전체)")
    ap.add_argument("--period-start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--period-end", required=True, help="YYYY-MM-DD")
    return build(ap.parse_args())


if __name__ == "__main__":
    sys.exit(main())
