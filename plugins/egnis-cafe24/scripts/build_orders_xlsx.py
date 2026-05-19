#!/usr/bin/env python3
"""
build_orders_xlsx.py — cafe24-orders-incremental (v2) 의 Python 측 적재기.

설계 (2026-05-19 아키텍처 변경):
  이전: Python 이 urllib 로 직접 cafe24api 호출 → :30 KST 토큰 회전 윈도우 stale 401
  현재: Claude (skill) 가 MCP cafe24_get 으로 페이지별 응답 JSON 을 수집 → 임시 파일로
        전달 → 본 스크립트가 sanitize + flatten + xlsx/CSV append.

호출 흐름:
  Claude (SKILL.md) 가 9 mall 각각에 대해:
    1. mcp__cafe24_get(mall_id, path=orders, query={start,end,limit,offset,...})
       를 paging.has_next 가 false 가 될 때까지 반복 호출
    2. 페이지별 응답에서 body 부분만 모아 JSON 배열로 임시 파일에 기록
       (예: /tmp/cafe24_pages_<mall>.json, 형식: [{"orders": [...]}, ...])
    3. build_orders_xlsx.py --mall <mall_id> --input /tmp/... --base-dir ... 실행
    4. 본 스크립트가 PII 제거 → flat dict → CSV/xlsx append

Outputs (cafe24-orders-incremental 와 동일):
  <base-dir>/state/last_fetched.json
  <base-dir>/data/orders_all.csv  (UTF-8 BOM)
  <base-dir>/data/orders_all.xlsx (몰별 시트)
  <base-dir>/data/raw/<mall>/orders_<since>_to_<until>.json  (sanitize 후)
  <base-dir>/logs/run_<timestamp>.log

PII 적재 금지: fetch_orders_incremental.py 와 동일한 _sanitize_pages 사용.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from lib.brand_registry import label_for, resolve_brand  # noqa: E402
from lib.xlsx_writer import (  # noqa: E402
    ORDERS_DETAIL_COLUMNS,
    write_multi_sheet_xlsx,
)

# fetch_orders_incremental.py 의 PII/flatten 로직을 재사용 (동일 모듈에서 import).
from fetch_orders_incremental import (  # noqa: E402
    _PII_KEYS_ON_ORDER,
    _PII_NESTED_KEYS,
    _config_path,
    _flatten_order,
    _sanitize_pages,
    load_user_config,
    save_user_config,
    validate_base_dir,
)


CSV_HEADER_LABELS = [label for _, label in ORDERS_DETAIL_COLUMNS]
CSV_HEADER_KEYS = [key for key, _ in ORDERS_DETAIL_COLUMNS]


def _load_state(state_path: Path) -> dict:
    if state_path.exists():
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"WARN: corrupt state at {state_path}, treating as empty",
                  file=sys.stderr)
    return {"last_fetched": {}, "runs": []}


def _save_state(state_path: Path, state: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "Y" if v else "N"
    return v


def append_csv(csv_path: Path, rows: list[dict]) -> int:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not csv_path.exists()
    mode = "a" if not is_new else "w"
    written = 0
    with csv_path.open(mode, encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(CSV_HEADER_LABELS)
        for row in rows:
            writer.writerow([_cell(row.get(k)) for k in CSV_HEADER_KEYS])
            written += 1
    return written


def dedup_existing(csv_path: Path, fresh_rows: list[dict]) -> list[dict]:
    if not csv_path.exists():
        return fresh_rows
    existing: set[tuple[str, str]] = set()
    try:
        with csv_path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for r in reader:
                existing.add((r.get("몰ID", ""), str(r.get("주문번호", ""))))
    except Exception as e:
        print(f"WARN: dedup read failed ({e}); proceeding", file=sys.stderr)
        return fresh_rows
    return [
        r for r in fresh_rows
        if (r["mall_id"], str(r["order_id"])) not in existing
    ]


def load_pages(input_path: Path) -> list[dict]:
    """입력 JSON 파일 또는 stdin 에서 페이지 배열 로드.

    예상 형식:
      [
        {"orders": [...], "paging": {...}, "links": [...]},
        {"orders": [...], ...},
        ...
      ]
    또는 wrapper 형태 (mcp__cafe24_get 응답 그대로 모은 경우):
      [
        {"provider": "cafe24", "mall_id": "cloop", "path": "/orders",
         "status": 200, "ok": true, "data": {"orders": [...]}, "paging": {...}},
        ...
      ]
    """
    if str(input_path) == "-":
        raw = sys.stdin.read()
    else:
        raw = input_path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON in {input_path}: {e}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(data, list):
        print("ERROR: input must be a JSON array of page responses",
              file=sys.stderr)
        sys.exit(1)

    # mcp__cafe24_get 응답을 data wrapping 형태로 받았을 수 있으므로 정규화.
    pages: list[dict] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        if "orders" in entry:
            pages.append(entry)
        elif "data" in entry and isinstance(entry["data"], dict):
            pages.append(entry["data"])
    return pages


def run(args: argparse.Namespace) -> int:
    try:
        mall_ids = resolve_brand(args.mall)
    except KeyError as e:
        print(f"ERROR: unknown brand: {e}", file=sys.stderr)
        return 1
    if len(mall_ids) != 1:
        print(f"ERROR: --mall must resolve to exactly 1 mall, got {mall_ids}",
              file=sys.stderr)
        return 1
    mall_id = mall_ids[0]

    # base_dir 결정 (인자 > config > 에러)
    base_dir_raw = args.base_dir
    if not base_dir_raw:
        cfg = load_user_config()
        base_dir_raw = cfg.get("last_base_dir")
        if not base_dir_raw:
            print(
                "ERROR: --base-dir 미지정 + config.json 도 없음.\n"
                "  skill 이 사용자에게 경로를 컨펌받은 후 --base-dir 로 전달해야 함.",
                file=sys.stderr,
            )
            return 1
        print(f"[xlsx] config last_base_dir 재사용: {base_dir_raw}",
              file=sys.stderr)
    base_dir = Path(os.path.expanduser(base_dir_raw)).resolve()
    ok, msg = validate_base_dir(base_dir)
    if not ok:
        print(f"ERROR: base-dir 검증 실패: {msg}", file=sys.stderr)
        return 1

    state_path = base_dir / "state" / "last_fetched.json"
    data_dir = base_dir / "data"
    raw_dir = data_dir / "raw"
    csv_path = data_dir / "orders_all.csv"
    xlsx_path = data_dir / "orders_all.xlsx"
    log_dir = base_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # 입력 페이지 로드 + PII sanitize
    input_path = Path("-") if args.input == "-" else Path(args.input)
    pages = load_pages(input_path)
    pages = _sanitize_pages(pages)

    # raw 백업 (sanitize 된 상태로 저장)
    raw_path = raw_dir / mall_id / f"orders_{args.since}_to_{args.until}.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(
        json.dumps(pages, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # flatten
    rows: list[dict] = []
    for page in pages:
        for order in page.get("orders") or []:
            rows.append(_flatten_order(mall_id, order))

    if not rows:
        print(f"[xlsx] [{mall_id}] 페이지 응답에 주문 없음", file=sys.stderr)
    else:
        rows = dedup_existing(csv_path, rows)

    written_csv = 0
    sheet_counts: dict = {}
    if rows:
        written_csv = append_csv(csv_path, rows)
        sheet_name = f"{label_for(mall_id)}({mall_id})"
        try:
            sheet_counts = write_multi_sheet_xlsx(
                xlsx_path,
                {sheet_name: (ORDERS_DETAIL_COLUMNS, rows)},
                append=True,
            )
        except RuntimeError as e:
            print(f"WARN: xlsx 실패 (CSV 정상): {e}", file=sys.stderr)

    # state 갱신
    state = _load_state(state_path)
    state.setdefault("last_fetched", {})[mall_id] = args.until
    state.setdefault("runs", []).append({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "mall": mall_id,
        "since": args.since,
        "until": args.until,
        "new_rows": written_csv,
        "input_pages": len(pages),
    })
    state["runs"] = state["runs"][-100:]
    _save_state(state_path, state)

    save_user_config(base_dir)

    log_path = log_dir / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S_' + mall_id)}.log"
    log_path.write_text(
        f"mall={mall_id}\nsince={args.since}\nuntil={args.until}\n"
        f"input_pages={len(pages)}\nnew_rows={written_csv}\n"
        f"sheet_counts={sheet_counts}\n",
        encoding="utf-8",
    )

    # stdout: 한 줄 요약 (Claude 가 파싱하기 좋게)
    print(json.dumps({
        "ok": True,
        "mall_id": mall_id,
        "since": args.since,
        "until": args.until,
        "input_pages": len(pages),
        "new_rows": written_csv,
        "sheet_counts": sheet_counts,
        "base_dir": str(base_dir),
    }, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Build orders xlsx from MCP-collected JSON pages (cafe24-orders-incremental v2)"
    )
    p.add_argument("--mall", required=True,
                   help="브랜드 alias 또는 mall_id (단일 mall 만 허용)")
    p.add_argument("--input", required=True,
                   help="페이지 응답 JSON 파일 경로, 또는 '-' (stdin)")
    p.add_argument("--since", required=True, help="YYYY-MM-DD (수집 시작일)")
    p.add_argument("--until", required=True, help="YYYY-MM-DD (수집 종료일)")
    p.add_argument(
        "--base-dir",
        default=None,
        help="state/data/logs 저장 base 디렉토리. 미지정 시 "
             "~/.config/cafe24-orders-incremental/config.json 의 last_base_dir 사용.",
    )
    return p


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
