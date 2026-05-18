#!/usr/bin/env python3
"""
build_sales_dashboard.py

Reads raw Cafe24 dumps from <raw-dir>, produces a self-contained
dashboard.html that supports client-side filtering by brand and rebucketing
to daily/weekly/monthly. Writes companion CSVs.

Inputs in raw-dir:
  hourlysales_<mall_id>.json              (one per mall, all days)
  members_sales_<mall_id>_<YYYY-MM-DD>.json (one per mall per day)

Usage:
  python3 build_sales_dashboard.py \
    --raw-dir ./reports/cafe24/all/2026-05-12_to_2026-05-18/data/raw \
    --out-dir ./reports/cafe24/all/2026-05-12_to_2026-05-18 \
    --brand-label 전체 \
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
# Stable order so the brand selector and overlay chart colors are consistent.
MALL_ORDER = list(MALL_LABELS.keys())

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
    out: dict[str, dict] = {}
    if not raw or not raw.get("ok"):
        return out
    rows = raw.get("data", {}).get("hourlysales", []) or []
    for r in rows:
        date = r.get("collection_date")
        if not date:
            continue
        a = out.setdefault(date, {"orders": 0, "items": 0, "sales": 0})
        a["orders"] += int(r.get("order_count") or 0)
        a["items"] += int(r.get("item_count") or 0)
        a["sales"] += float(r.get("sales") or 0)
    return out


def members_sales_buyers(raw: dict) -> int:
    if not raw or not raw.get("ok"):
        return 0
    rows = raw.get("data", {}).get("sales", []) or []
    if not rows:
        return 0
    row = rows[0]
    return int(row.get("member_order_count") or 0) + int(row.get("nonmember_order_count") or 0)


def discover_mall_ids(raw_dir: Path) -> list[str]:
    found: set[str] = set()
    for p in raw_dir.glob("hourlysales_*.json"):
        found.add(p.stem.replace("hourlysales_", ""))
    for p in raw_dir.glob("members_sales_*.json"):
        stem = p.stem.replace("members_sales_", "")
        idx = stem.rfind("_")
        if idx > 0:
            found.add(stem[:idx])
    # Keep canonical order; unknown malls appended last.
    return [m for m in MALL_ORDER if m in found] + sorted(m for m in found if m not in MALL_ORDER)


def build(args) -> int:
    raw_dir = Path(args.raw_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir = out_dir / "data"
    data_dir.mkdir(exist_ok=True)

    period_start = dt.date.fromisoformat(args.period_start)
    period_end = dt.date.fromisoformat(args.period_end)
    days = [d.isoformat() for d in daterange(period_start, period_end)]
    prev_day = (period_start - dt.timedelta(days=1)).isoformat()

    mall_ids = discover_mall_ids(raw_dir)
    if not mall_ids:
        print(f"[error] no raw JSON files found in {raw_dir}", file=sys.stderr)
        return 1

    # Per-mall daily rows + prev-day baseline for compare.
    # mall_data[mall_id][date] = {orders, items, sales, buyers}
    mall_data: dict[str, dict[str, dict]] = {}
    mall_prev_sales: dict[str, float] = {}
    for mall in mall_ids:
        hs_agg = aggregate_hourlysales(load_json(raw_dir / f"hourlysales_{mall}.json"))
        per_day: dict[str, dict] = {}
        for d in days:
            row = hs_agg.get(d, {"orders": 0, "items": 0, "sales": 0})
            buyers = members_sales_buyers(load_json(raw_dir / f"members_sales_{mall}_{d}.json"))
            per_day[d] = {**row, "buyers": buyers}
        mall_data[mall] = per_day
        if prev_day in hs_agg:
            mall_prev_sales[mall] = hs_agg[prev_day]["sales"]

    # ---- Combined daily across all malls (for CSV + the "전체" default view) ----
    daily_rows = []
    for d in days:
        tot = {"orders": 0, "items": 0, "sales": 0, "buyers": 0}
        for mall in mall_ids:
            row = mall_data[mall].get(d, {})
            tot["orders"] += row.get("orders", 0)
            tot["items"] += row.get("items", 0)
            tot["sales"] += row.get("sales", 0)
            tot["buyers"] += row.get("buyers", 0)
        daily_rows.append({"date": d, **tot})

    prev_sales_total = sum(mall_prev_sales.values()) if mall_prev_sales else None
    last_sales = prev_sales_total
    for row in daily_rows:
        row["compare"] = last_sales
        row["delta"] = (row["sales"] - last_sales) if last_sales is not None else None
        last_sales = row["sales"]

    total_buyers = sum(r["buyers"] for r in daily_rows)
    total_orders = sum(r["orders"] for r in daily_rows)
    total_items = sum(r["items"] for r in daily_rows)
    total_sales = sum(r["sales"] for r in daily_rows)

    # ---- CSV outputs ----
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

    # by_mall CSV — only if >1 mall in raw dir.
    by_mall_rows = []
    if len(mall_ids) > 1:
        for mall in mall_ids:
            agg = {"buyers": 0, "orders": 0, "items": 0, "sales": 0}
            for d in days:
                row = mall_data[mall].get(d, {})
                for k in agg:
                    agg[k] += row.get(k, 0)
            aov = int(agg["sales"] / agg["orders"]) if agg["orders"] else 0
            by_mall_rows.append({
                "mall_id": mall,
                "mall_label": MALL_LABELS.get(mall, mall),
                **agg,
                "aov": aov,
            })
        by_mall_rows.sort(key=lambda r: r["sales"], reverse=True)
        by_mall_path = data_dir / "sales_by_mall.csv"
        with by_mall_path.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["자사몰", "mall_id", "구매자수", "구매건수", "구매개수", "매출액", "건당객단가"])
            for r in by_mall_rows:
                w.writerow([
                    r["mall_label"], r["mall_id"], r["buyers"],
                    r["orders"], r["items"], int(r["sales"]), r["aov"],
                ])

    # ---- JSON payloads inlined into the HTML ----
    # MALL_DATA[mall_id] = { label, prev_sales, days: [{date, buyers, orders, items, sales}, ...] }
    mall_data_json = {}
    for mall in mall_ids:
        mall_data_json[mall] = {
            "label": MALL_LABELS.get(mall, mall),
            "prev_sales": int(mall_prev_sales.get(mall, 0)),
            "has_prev": mall in mall_prev_sales,
            "days": [
                {
                    "date": d,
                    "buyers": mall_data[mall][d]["buyers"],
                    "orders": mall_data[mall][d]["orders"],
                    "items": mall_data[mall][d]["items"],
                    "sales": int(mall_data[mall][d]["sales"]),
                }
                for d in days
            ],
        }

    # ---- Render HTML ----
    tpl = TEMPLATE_PATH.read_text(encoding="utf-8")
    period_label = f"{args.period_start} ~ {args.period_end}"
    period_days = (period_end - period_start).days + 1
    generated_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    html = (
        tpl.replace("__BRAND_LABEL__", args.brand_label)
           .replace("__PERIOD_LABEL__", period_label)
           .replace("__PERIOD_DAYS__", str(period_days))
           .replace("__PERIOD_FILE__", f"{args.period_start}_to_{args.period_end}")
           .replace("__PERIOD_START__", args.period_start)
           .replace("__PERIOD_END__", args.period_end)
           .replace("__GENERATED_AT__", generated_at)
           .replace("__MALL_DATA_JSON__", json.dumps(mall_data_json, ensure_ascii=False))
    )
    (out_dir / "dashboard.html").write_text(html, encoding="utf-8")

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
