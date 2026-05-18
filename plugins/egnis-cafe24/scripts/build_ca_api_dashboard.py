#!/usr/bin/env python3
"""
scripts/build_ca_api_dashboard.py — CA API raw → CSV 3종 + 전환율 매트릭스 HTML

Plan: .omc/plans/cafe24-mcp-skills-v3.md §7 Phase 4.D
ADR-003 §1: render_dashboard 함수형 진입점만 사용

슬랙 시나리오 직접 대응 (전환율 시계열 비교).
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


def _safe_int(v) -> int:
    try:
        return int(v) if v not in (None, "") else 0
    except (ValueError, TypeError):
        return 0


def _load(raw_dir: Path, prefix: str, mall_id: str):
    path = raw_dir / f"{prefix}_{mall_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _is_beta_unavailable(obj) -> bool:
    return isinstance(obj, dict) and obj.get("_beta_unavailable") is True


def _extract_rows(obj, *candidate_keys: str) -> list[dict]:
    """베타 응답 스키마가 변경 가능하므로 여러 후보 키 시도."""
    if obj is None or _is_beta_unavailable(obj):
        return []
    if isinstance(obj, dict):
        body = obj.get("body")
        if isinstance(body, dict):
            for k in candidate_keys:
                items = body.get(k)
                if isinstance(items, list):
                    return items
        # 직접 키
        for k in candidate_keys:
            items = obj.get(k)
            if isinstance(items, list):
                return items
    if isinstance(obj, list):
        return obj
    return []


# ---- CSV writers (Live 검증 2026-05-18 스키마 반영) ----
# /carts/action 응답: {product_no, product_name, count(=조회수), add_cart_count, add_cart_rate}
# /products/sales 응답: {product_no, product_name, order_count, order_product_count, order_amount}

def write_cart_actions_csv(out_path: Path, by_mall: dict[str, list[dict]]) -> int:
    """노출수(count) + 담김수(add_cart_count) + 전환율(add_cart_rate) 함께."""
    _ensure_dir(out_path.parent)
    n = 0
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["brand", "product_no", "product_name",
                    "hit_count", "add_cart_count", "add_cart_rate_pct"])
        for mall_id, rows in by_mall.items():
            for r in rows:
                w.writerow([
                    mall_id,
                    r.get("product_no", ""),
                    r.get("product_name", ""),
                    _safe_int(r.get("count")),
                    _safe_int(r.get("add_cart_count")),
                    r.get("add_cart_rate", ""),
                ])
                n += 1
    return n


def write_product_sales_csv(out_path: Path, by_mall: dict[str, list[dict]]) -> int:
    """판매건수(order_count) + 판매개수(order_product_count) + 매출(order_amount)."""
    _ensure_dir(out_path.parent)
    n = 0
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["brand", "product_no", "product_name",
                    "order_count", "order_product_count", "order_amount"])
        for mall_id, rows in by_mall.items():
            for r in rows:
                w.writerow([
                    mall_id,
                    r.get("product_no", ""),
                    r.get("product_name", ""),
                    _safe_int(r.get("order_count")),
                    _safe_int(r.get("order_product_count")),
                    r.get("order_amount", 0),
                ])
                n += 1
    return n


# ---- 전환율 매트릭스 (Live 스키마 기반) ----
def build_conversion_matrix(carts, sales) -> dict:
    """product_no별 노출(count) / 담김(add_cart_count) / 판매(order_count) / 전환율 통합.

    carts/action 단일 endpoint가 hits + cart 정보 모두 보유.
    """
    by_pid: dict = {}

    def _bucket(pid):
        return by_pid.setdefault(pid, {
            "product_name": "",
            "hits": 0, "cart": 0, "sales": 0,
            "order_product_count": 0, "order_amount": 0.0,
        })

    # Live 검증 (2026-05-18) 결과: CA API는 동일 product_no를 product_name별로 중복 반환하며,
    # add_cart_count는 product_no 전체 합계를 매 row마다 반복 노출 (베타 버그).
    # 대응:
    #   - hits/count: row별 sum (옵션/상품명 분기별 노출수는 서로 다름 — 합산 정상)
    #   - add_cart_count: product_no 단위로 max 1회만 채택 (중복 합산 방지)
    #   - sales: row별 sum (옵션별 order_count는 서로 다름 — 합산 정상)
    seen_cart_pid: set = set()
    for rows in carts.values():
        for r in rows:
            pid = r.get("product_no")
            if pid is None:
                continue
            b = _bucket(pid)
            b["hits"] += _safe_int(r.get("count"))
            # add_cart_count는 product_no당 최초 row만 채택 (CA API 중복 버그 회피)
            if pid not in seen_cart_pid:
                b["cart"] += _safe_int(r.get("add_cart_count"))
                seen_cart_pid.add(pid)
            if not b["product_name"]:
                b["product_name"] = r.get("product_name", "")

    for rows in sales.values():
        for r in rows:
            pid = r.get("product_no")
            if pid is None:
                continue
            b = _bucket(pid)
            b["sales"] += _safe_int(r.get("order_count"))
            b["order_product_count"] += _safe_int(r.get("order_product_count"))
            try:
                b["order_amount"] += float(r.get("order_amount") or 0)
            except (ValueError, TypeError):
                pass
            if not b["product_name"]:
                b["product_name"] = r.get("product_name", "")

    # 전환율
    for pid, b in by_pid.items():
        h = b["hits"] or 1
        b["cart_rate"] = round(b["cart"] / h * 100.0, 2)
        b["sales_rate"] = round(b["sales"] / h * 100.0, 2)
    return by_pid


def build_dashboard(
    carts, sales,
    brand_label: str, period_start: str, period_end: str,
    beta_warn: list[str],
) -> str:
    matrix = build_conversion_matrix(carts, sales)
    total_hits = sum(b["hits"] for b in matrix.values())
    total_cart = sum(b["cart"] for b in matrix.values())
    total_sales = sum(b["sales"] for b in matrix.values())
    total_amount = sum(b["order_amount"] for b in matrix.values())

    cart_rate = (total_cart / total_hits * 100.0) if total_hits else 0.0
    sales_rate = (total_sales / total_hits * 100.0) if total_hits else 0.0

    kpis = [
        {"label": "총 노출수", "value": f"{total_hits:,}",
         "delta": None, "accent": "neutral"},
        {"label": "총 담김수", "value": f"{total_cart:,}",
         "delta": None, "accent": "neutral"},
        {"label": "장바구니 전환율", "value": f"{cart_rate:.2f}%",
         "delta": None,
         "accent": "positive" if cart_rate >= 5.0 else "neutral"},
        {"label": "결제 전환율 (판매/노출)", "value": f"{sales_rate:.2f}%",
         "delta": None,
         "accent": "positive" if sales_rate >= 2.0 else "neutral"},
    ]

    # 표: product_no × {hits, cart, sales, rates}, 노출수 내림차순
    sorted_pids = sorted(matrix.keys(),
                        key=lambda p: matrix[p]["hits"],
                        reverse=True)
    rows: list[list] = []
    for pid in sorted_pids[:200]:  # 안전 한도
        b = matrix[pid]
        name = (b.get("product_name") or "")[:40]
        rows.append([pid, name, b["hits"], b["cart"], b["sales"],
                     f"{b['cart_rate']}%", f"{b['sales_rate']}%"])

    table = {
        "columns": ["상품번호", "상품명", "노출수", "담김수", "판매건수",
                    "장바구니 전환율", "결제 전환율"],
        "rows": rows,
        "csv_filename": "ca_api_conversion.csv",
    }

    # 차트: 상위 N개 상품의 노출수 (CA API는 기간 합산 데이터라 일자별 시계열 부재)
    # 슬랙 시나리오의 시계열 비교는 사용자가 period를 둘로 쪼개 2회 실행 후 dashboard.html 비교로 충족.
    top_for_chart = sorted_pids[:15]  # 상위 15개
    chart_labels = [
        ((matrix[pid].get("product_name") or str(pid))[:25])
        for pid in top_for_chart
    ]

    chart = {
        "type": "line",  # Tier 1 timeseries (G_UNIFORM_2)
        "x_labels": chart_labels,
        "series": [
            {"label": "노출수", "data": [matrix[pid]["hits"] for pid in top_for_chart]},
            {"label": "담김수", "data": [matrix[pid]["cart"] for pid in top_for_chart]},
            {"label": "판매건수", "data": [matrix[pid]["sales"] for pid in top_for_chart]},
        ],
        "options": {"responsive": True, "maintainAspectRatio": False},
    }

    brands: list[dict] = [{
        "mall_id": "_all",
        "label": brand_label or "전체",
        "data": {
            "matrix_top": [
                {"product_no": pid, **matrix[pid]} for pid in sorted_pids[:50]
            ],
            "total_hits": total_hits, "total_cart": total_cart, "total_sales": total_sales,
        },
    }]
    for mall_id in MALL_ORDER:
        if mall_id in carts or mall_id in sales:
            brands.append({
                "mall_id": mall_id,
                "label": label_for(mall_id),
                "data": {
                    "hits_count": sum(_safe_int(r.get("count"))
                                      for r in carts.get(mall_id, [])),
                    "cart_count": sum(_safe_int(r.get("add_cart_count"))
                                      for r in carts.get(mall_id, [])),
                    "sales_count": sum(_safe_int(r.get("order_count"))
                                       for r in sales.get(mall_id, [])),
                },
            })

    meta = {
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "ca-api.cafe24data.com (베타)",
        "period": f"{period_start} ~ {period_end}",
        "version": "v3",
        "api_version": CAFE24_API_VERSION,
        "beta_warn": beta_warn,  # 배너에 표시
    }

    title_suffix = " (베타 — endpoint 변경 가능)"
    if beta_warn:
        title_suffix += f" — {len(beta_warn)} endpoint 미사용"

    return render_dashboard(
        layout="timeseries",
        title=f"Cafe24 전환율 (CA API){title_suffix} — {brand_label}",
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
        f.stem[len("cart_actions_"):]
        for f in raw_dir.glob("cart_actions_*.json")
    })

    carts: dict[str, list[dict]] = {}
    sales: dict[str, list[dict]] = {}
    beta_warn: list[str] = []

    for mall_id in mall_ids:
        c = _load(raw_dir, "cart_actions", mall_id)
        if _is_beta_unavailable(c):
            beta_warn.append(f"{mall_id}/cart_actions")
        # Live 스키마: body.action → list of {product_no, count, add_cart_count, add_cart_rate, ...}
        carts[mall_id] = _extract_rows(c, "action", "cart_actions", "carts", "items")

        s = _load(raw_dir, "product_sales", mall_id)
        if _is_beta_unavailable(s):
            beta_warn.append(f"{mall_id}/product_sales")
        # Live 스키마: body.sales → list of {product_no, order_count, order_product_count, order_amount, ...}
        sales[mall_id] = _extract_rows(s, "sales", "product_sales", "products", "items")

    data_dir = out_dir / "data"
    write_cart_actions_csv(data_dir / "cart_actions.csv", carts)
    write_product_sales_csv(data_dir / "product_sales.csv", sales)
    print(f"  CSV 2종 → {data_dir}")
    if beta_warn:
        print(f"  [beta_warn] {len(beta_warn)} endpoint(s) unavailable: {beta_warn[:5]}",
              file=sys.stderr)

    html = build_dashboard(
        carts, sales,
        brand_label=args.brand_label or "전체",
        period_start=args.period_start,
        period_end=args.period_end,
        beta_warn=beta_warn,
    )
    dashboard_path = out_dir / "dashboard.html"
    _ensure_dir(dashboard_path.parent)
    dashboard_path.write_text(html, encoding="utf-8")
    print(f"  dashboard.html → {dashboard_path}")
    return 0


def build_parser():
    p = argparse.ArgumentParser(description="CA API raw → CSV 3종 + 전환율 dashboard")
    p.add_argument("--raw-dir", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--brand-label", default="전체")
    p.add_argument("--period-start", required=True)
    p.add_argument("--period-end", required=True)
    return p


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
