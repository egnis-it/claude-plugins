#!/usr/bin/env python3
"""
fetch_orders_incremental.py — cafe24-orders-incremental skill용 증분 적재 스크립트.

설계:
  - last_fetched.json (state file)에 brand별 last_fetched_date 저장
  - 매 실행마다 (last_fetched + 1일) ~ today 범위만 fetch
  - 최초 실행 시 --since (default 2026-05-01) ~ today 전체 적재
  - CSV append + xlsx 누적 시트 행 추가
  - 동일 일자 재실행 시 idempotent (해당 일자 행 dedup)

호출 흐름:
  Claude(skill) → MCP token 발급 → 본 스크립트 실행
    → state file 읽어 brand별 시작일 결정
    → cafe24_client.get_all_pages() 로 fetch (페이지네이션)
    → CSV(BOM, UTF-8) + xlsx 시트 누적 append
    → state file 업데이트

Outputs:
  ./reports/cafe24/all/orders_incremental/
    ├── state/last_fetched.json
    ├── data/
    │   ├── orders_all.csv         (전체 누적, append 모드)
    │   ├── orders_all.xlsx        (몰별 시트, 누적)
    │   └── raw/<mall>/<date>.json (idempotent, 일자별 raw 백업)
    └── logs/run_<timestamp>.log

Exit codes:
  0 — success
  1 — 잘못된 인자 / 환경
  2 — 401/403 일부 mall (caller가 토큰 재발급 후 재시도)
  4 — rate limit (재시도 권장)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from lib.cafe24_client import Cafe24Client, Cafe24RateLimited, Cafe24TokenExpired  # noqa: E402
from lib.brand_registry import label_for, resolve_brand  # noqa: E402
from lib.xlsx_writer import (  # noqa: E402
    ORDERS_DETAIL_COLUMNS,
    write_multi_sheet_xlsx,
)

ORDERS_LIMIT = 1000
DEFAULT_SINCE = "2026-05-01"  # 사용자 요청 시작일


def _config_path() -> Path:
    """OS별 cafe24-orders-incremental config.json 경로.

    - Windows: %APPDATA%\\cafe24-orders-incremental\\config.json
    - macOS/Linux: ~/.config/cafe24-orders-incremental/config.json
    """
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "cafe24-orders-incremental" / "config.json"
    return Path.home() / ".config" / "cafe24-orders-incremental" / "config.json"


def load_user_config() -> dict:
    """사용자 config.json 로드. 없거나 손상이면 빈 dict."""
    p = _config_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_user_config(base_dir: Path) -> None:
    """마지막 사용 경로 + history 캐시 저장."""
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    cfg = load_user_config()
    abs_path = str(base_dir.resolve())
    cfg["last_base_dir"] = abs_path
    history = cfg.get("history", [])
    if abs_path in history:
        history.remove(abs_path)
    history.insert(0, abs_path)
    cfg["history"] = history[:10]  # 최근 10개만
    cfg["updated_at"] = datetime.now().isoformat(timespec="seconds")
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def validate_base_dir(base_dir: Path) -> tuple[bool, str]:
    """저장 경로 검증. (ok, message) 반환."""
    try:
        # 부모 디렉토리까지는 생성 가능해야 함
        parent = base_dir if base_dir.exists() else base_dir.parent
        # 부모도 없으면 그 위로 올라가며 가장 가까운 기존 디렉토리 찾기
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        if not os.access(parent, os.W_OK):
            return False, f"쓰기 권한 없음: {parent}"
        return True, "ok"
    except OSError as e:
        return False, str(e)

# CSV 헤더 (xlsx_writer의 ORDERS_DETAIL_COLUMNS와 동일 순서, 한국어 라벨)
CSV_HEADER_LABELS = [label for _, label in ORDERS_DETAIL_COLUMNS]
CSV_HEADER_KEYS = [key for key, _ in ORDERS_DETAIL_COLUMNS]


def _load_tokens() -> dict[str, dict]:
    raw = os.environ.get("CAFE24_TOKENS_JSON")
    if not raw:
        print("ERROR: CAFE24_TOKENS_JSON env var not set", file=sys.stderr)
        sys.exit(1)
    try:
        tokens = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid CAFE24_TOKENS_JSON: {e}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(tokens, dict) or not tokens:
        print("ERROR: CAFE24_TOKENS_JSON empty", file=sys.stderr)
        sys.exit(1)
    return tokens


def _load_state(state_path: Path) -> dict:
    if state_path.exists():
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"WARN: corrupt state file at {state_path}, treating as empty",
                  file=sys.stderr)
    return {"last_fetched": {}, "runs": []}


def _save_state(state_path: Path, state: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _decide_period(state: dict, mall_id: str, since: str, until: str) -> tuple[str, str] | None:
    """이 mall에 대해 fetch할 (start, end) 결정. None이면 skip."""
    last = state.get("last_fetched", {}).get(mall_id)
    if last:
        try:
            last_date = date.fromisoformat(last)
            start_date = last_date + timedelta(days=1)
        except ValueError:
            start_date = date.fromisoformat(since)
    else:
        start_date = date.fromisoformat(since)

    end_date = date.fromisoformat(until)
    if start_date > end_date:
        return None  # 이미 최신
    return start_date.isoformat(), end_date.isoformat()


def _flatten_order(mall_id: str, order: dict) -> dict:
    """cafe24 orders API 응답 1건 → flat dict (CSV/xlsx 셀)."""
    buyer = order.get("buyer") or {}
    items = order.get("items") or []
    items_count = sum(int(i.get("quantity") or 0) for i in items) if items else 0
    return {
        "mall_id": mall_id,
        "brand_label": label_for(mall_id),
        "order_id": order.get("order_id"),
        "order_date": order.get("order_date"),
        "payment_date": order.get("payment_date"),
        "order_status": order.get("order_status"),
        "payment_method_name": order.get("payment_method_name"),
        "buyer_name": (buyer.get("name") or order.get("buyer_name") or ""),
        "buyer_email": (buyer.get("email") or order.get("buyer_email") or ""),
        "buyer_cellphone": (buyer.get("cellphone") or order.get("buyer_cellphone") or ""),
        "items_count": items_count,
        "payment_amount": order.get("payment_amount"),
        "actual_payment_amount": order.get("actual_payment_amount"),
        "shipping_fee": order.get("shipping_fee"),
        "currency": order.get("currency") or "KRW",
        "canceled": str(order.get("order_status") or "").upper().startswith("C"),
        "refunded": bool(order.get("refund_status")),
    }


def fetch_mall_range(
    client: Cafe24Client,
    mall_id: str,
    start: str,
    end: str,
    raw_dir: Path,
) -> list[dict]:
    """한 mall의 [start, end] 범위 주문을 모두 가져와 flat rows 반환.

    raw_dir에 일자 단위로 idempotent 백업: raw/<mall>/<start>_to_<end>.json
    (재실행 시 동일 파일이 있어도 덮어쓰지 않고 skip)
    """
    raw_path = raw_dir / mall_id / f"orders_{start}_to_{end}.json"
    if raw_path.exists():
        try:
            cached = json.loads(raw_path.read_text(encoding="utf-8"))
            pages = cached if isinstance(cached, list) else []
            print(f"  [{mall_id}] cached raw 재사용 ({start}~{end})")
        except json.JSONDecodeError:
            pages = None
    else:
        pages = None

    if pages is None:
        all_pages: list[dict] = []
        for chunk_start, chunk_end in Cafe24Client.split_date_range(start, end, max_months=3):
            chunk = client.get_all_pages(
                mall_id,
                "/api/v2/admin/orders",
                params={
                    "start_date": chunk_start,
                    "end_date": chunk_end,
                    "date_type": "order_date",
                    "limit": str(ORDERS_LIMIT),
                    "embed": "items,buyer,receivers",
                },
                max_pages=50,  # ~50K orders/mall/run 안전 한도
            )
            all_pages.extend(chunk)
        pages = all_pages
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(
            json.dumps(pages, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    rows: list[dict] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        for order in page.get("orders") or []:
            rows.append(_flatten_order(mall_id, order))
    return rows


def append_csv(csv_path: Path, rows: list[dict]) -> int:
    """CSV append (BOM 포함, 한국어 헤더). 신규 파일이면 헤더 작성."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not csv_path.exists()
    mode = "a" if not is_new else "w"
    written = 0
    # BOM은 신규 파일에만 1회. encoding은 utf-8-sig가 자동 처리.
    with csv_path.open(mode, encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(CSV_HEADER_LABELS)
        for row in rows:
            writer.writerow([_cell(row.get(k)) for k in CSV_HEADER_KEYS])
            written += 1
    return written


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "Y" if v else "N"
    return v


def dedup_existing_dates(csv_path: Path, fresh_rows: list[dict]) -> list[dict]:
    """기존 CSV에 (mall_id, order_id) 가 이미 있으면 제외. idempotent."""
    if not csv_path.exists():
        return fresh_rows
    existing: set[tuple[str, str]] = set()
    try:
        with csv_path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for r in reader:
                key = (r.get("몰ID", ""), str(r.get("주문번호", "")))
                existing.add(key)
    except Exception as e:
        print(f"WARN: dedup read failed ({e}); proceeding without dedup", file=sys.stderr)
        return fresh_rows
    return [
        r for r in fresh_rows
        if (r["mall_id"], str(r["order_id"])) not in existing
    ]


def run(args: argparse.Namespace) -> int:
    try:
        mall_ids = resolve_brand(args.brand)
    except KeyError as e:
        print(f"ERROR: unknown brand: {e}", file=sys.stderr)
        return 1

    tokens = _load_tokens()
    available = [m for m in mall_ids if m in tokens]
    missing = [m for m in mall_ids if m not in tokens]
    if missing:
        print(f"WARN: tokens missing for {missing}", file=sys.stderr)
    if not available:
        print("ERROR: no tokens for requested brands", file=sys.stderr)
        return 1

    # base_dir 결정: 인자 > config.json last_base_dir > 에러
    base_dir_raw = args.base_dir
    if not base_dir_raw:
        cfg = load_user_config()
        base_dir_raw = cfg.get("last_base_dir")
        if not base_dir_raw:
            print(
                "ERROR: --base-dir 미지정 + config.json도 없음.\n"
                "  최초 실행 시 skill이 사용자에게 경로를 컨펌받은 후 --base-dir 로 전달해야 함.\n"
                "  예: --base-dir ~/Documents/cafe24-orders",
                file=sys.stderr,
            )
            return 1
        print(f"[incremental] config.json에서 last_base_dir 재사용: {base_dir_raw}")
    # base_dir 정규화: ~ 전개 + 절대경로
    base_dir = Path(os.path.expanduser(base_dir_raw)).resolve()
    ok, msg = validate_base_dir(base_dir)
    if not ok:
        print(f"ERROR: base-dir 검증 실패: {msg}", file=sys.stderr)
        print(f"  요청 경로: {base_dir}", file=sys.stderr)
        return 1
    print(f"[incremental] base-dir 확정: {base_dir}")
    state_path = base_dir / "state" / "last_fetched.json"
    data_dir = base_dir / "data"
    raw_dir = data_dir / "raw"
    csv_path = data_dir / "orders_all.csv"
    xlsx_path = data_dir / "orders_all.xlsx"
    log_dir = base_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    state = _load_state(state_path)
    since = args.since or DEFAULT_SINCE
    until = args.until or date.today().isoformat()

    client = Cafe24Client(tokens)
    print(f"[incremental] brands={available} since={since} until={until} base={base_dir}")

    failed_auth: list[str] = []
    sheets_to_write: dict[str, tuple] = {}
    total_new_rows = 0

    for mall_id in available:
        period = _decide_period(state, mall_id, since, until)
        if period is None:
            print(f"  [{mall_id}] up-to-date (last={state['last_fetched'].get(mall_id)})")
            continue
        start, end = period
        print(f"  [{mall_id}] fetching {start}~{end}")

        try:
            rows = fetch_mall_range(client, mall_id, start, end, raw_dir)
        except Cafe24TokenExpired as e:
            print(f"AUTH FAIL: mall={mall_id} status={e.status}", file=sys.stderr)
            failed_auth.append(mall_id)
            continue
        except Cafe24RateLimited as e:
            print(f"RATE LIMITED: mall={mall_id} remain={e.remain_seconds}s — abort",
                  file=sys.stderr)
            return 4

        # dedup (재실행 시 idempotent)
        rows = dedup_existing_dates(csv_path, rows)

        if rows:
            # CSV append
            n = append_csv(csv_path, rows)
            total_new_rows += n

            # xlsx 시트별 누적
            sheet_name = f"{label_for(mall_id)}({mall_id})"
            sheets_to_write[sheet_name] = (ORDERS_DETAIL_COLUMNS, rows)

            print(f"  [{mall_id}] +{n} 신규 주문 (CSV/xlsx append 대기)")
        else:
            print(f"  [{mall_id}] 신규 주문 없음 (dedup 후)")

        # 이 mall에 대한 state 갱신 (성공 시점)
        state.setdefault("last_fetched", {})[mall_id] = end

    # xlsx 배치 write (mall별 시트, append 모드)
    if sheets_to_write:
        try:
            counts = write_multi_sheet_xlsx(xlsx_path, sheets_to_write, append=True)
            print(f"  [xlsx] {xlsx_path.name} 시트 업데이트: {counts}")
        except RuntimeError as e:
            print(f"WARN: xlsx 작성 실패 (CSV는 정상): {e}", file=sys.stderr)

    # state 기록
    state.setdefault("runs", []).append({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "brands": available,
        "since": since,
        "until": until,
        "new_rows": total_new_rows,
        "auth_failures": failed_auth,
    })
    # runs는 최근 50개만 유지
    state["runs"] = state["runs"][-50:]
    _save_state(state_path, state)

    # log file
    log_path = log_dir / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    log_path.write_text(
        f"brands={available}\nsince={since}\nuntil={until}\nnew_rows={total_new_rows}\n"
        f"auth_failures={failed_auth}\n",
        encoding="utf-8",
    )

    if failed_auth:
        print(f"\n[incremental] AUTH FAIL: {failed_auth} → 토큰 재발급 후 재실행",
              file=sys.stderr)
        return 2

    # 성공 시 사용 경로 캐시 (다음 실행 시 같은 경로 자동 제안)
    save_user_config(base_dir)

    print(f"\n[incremental] 완료. 신규 주문 {total_new_rows}건. base={base_dir}")
    print(f"  CSV : {csv_path}")
    print(f"  xlsx: {xlsx_path}")
    print(f"  state: {state_path}")
    print(f"  config: {_config_path()} (다음 실행 시 같은 경로 자동 제안)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Cafe24 orders 증분 적재 (cafe24-orders-incremental skill)"
    )
    p.add_argument("--brand", default="all", help="brand alias or 'all' (default: all)")
    p.add_argument(
        "--since", default=DEFAULT_SINCE,
        help=f"최초 fetch 시작일 (default: {DEFAULT_SINCE}). 이미 state가 있으면 무시됨"
    )
    p.add_argument("--until", default=None, help="fetch 종료일 (default: today)")
    p.add_argument(
        "--base-dir",
        default=None,
        help="state/data/logs 저장 base 디렉토리. "
             "미지정 시 ~/.config/cafe24-orders-incremental/config.json 의 last_base_dir 사용. "
             "둘 다 없으면 에러 (skill이 사용자에게 컨펌 받은 후 명시적으로 전달해야 함).",
    )
    return p


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
