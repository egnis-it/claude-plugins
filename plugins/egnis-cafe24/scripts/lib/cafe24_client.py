"""
scripts/lib/cafe24_client.py — Cafe24 Admin/CA API 공용 클라이언트 (Phase 1 본구현)

Plan: .omc/plans/cafe24-mcp-skills-v3.md §7 Phase 1
ADR-001: .omc/plans/adr/ADR-001-fetch-sales-no-edit.md (fetch_sales.py 무수정 + §3.1 DRY-DEBT)
ADR-003: .omc/plans/adr/ADR-003-template-strategy.md §3.2 (urllib only)

Phase 0 minimal stub → Phase 1 본구현 변화:
  - get_all_pages: links.next 자동 순회 (페이지네이션) 구현
  - 429 응답 시 자동 backoff (200ms → 500ms → 1s) + 재시도 3회
  - X-Cafe24-Call-Usage 80%↑ 시 적응적 backoff
  - BRAND_ALIASES / MALL_ORDER / MALL_LABELS → brand_registry.py로 분리 (역호환 re-export)

시그니처는 frozen-on Phase 0 (cafe24_client_signature_v1.md). 변경 시 ADR 필요.
"""
from __future__ import annotations

import datetime as _dt
import json as _json
import time as _time
import urllib.error as _urllib_error
import urllib.parse as _urllib_parse
import urllib.request as _urllib_request

# Phase 1: brand registry는 별도 모듈로. 역호환 re-export.
from .brand_registry import (  # noqa: F401
    BRAND_ALIASES,
    MALL_LABELS,
    MALL_ORDER,
    label_for,
    resolve_brand as _resolve_brand_impl,
)

# ---- frozen 상수 (ADR-001 §3.1 DRY-DEBT 회수 대상 — PR-B에서 fetch_sales.py:38과 통합) ----
CAFE24_API_VERSION = "2026-03-01"
ANALYTICS_HOST = "https://ca-api.cafe24data.com"
TIMEOUT_SECONDS = 30

# 429 backoff 정책 (Phase 1 신규)
RETRY_BACKOFF_SECONDS: tuple[float, ...] = (0.2, 0.5, 1.0)
MAX_RETRIES = 3

# X-Cafe24-Call-Usage 임계치 (80% 초과 시 사전 backoff)
USAGE_BACKOFF_THRESHOLD = 80.0
USAGE_BACKOFF_DELAY = 0.3  # 300ms


# ---- 예외 (ADR-001 §3.1 DRY-DEBT 회수 대상 — PR-B에서 fetch_sales.py:44-48의 AuthError와 통합) ----
class Cafe24TokenExpired(Exception):
    """401/403 응답 시 발생. 호출자는 mcp__claude_ai_egnis-mcp__cafe24_get_access_token 재호출 후 재시도."""

    def __init__(self, mall_id: str, status: int, body: str = ""):
        self.mall_id = mall_id
        self.status = status
        self.body = body
        super().__init__(f"Cafe24 token expired/invalid: mall={mall_id} status={status}")


class Cafe24RateLimited(Exception):
    """429 응답 + 3회 재시도 모두 실패 시 발생."""

    def __init__(self, mall_id: str, remain_seconds: float):
        self.mall_id = mall_id
        self.remain_seconds = remain_seconds
        super().__init__(f"Cafe24 rate-limited: mall={mall_id} retry-after={remain_seconds}s")


# ---- 핵심 클라이언트 (frozen signature, ADR-003 §4) ----
class Cafe24Client:
    """Cafe24 Admin/CA API 호출 클라이언트.

    Phase 1 본구현:
      - get(...) + get_ca(...) — 401/403/429 자동 처리
      - get_all_pages(...) — links.next 자동 순회 (Generator로 반환)
      - split_date_range(...) — 3개월 초과 분할
      - resolve_brand(...) — alias → mall_id list (brand_registry 위임)
      - 429 자동 backoff (200ms→500ms→1s) + 재시도 3회
      - X-Cafe24-Call-Usage 80%↑ 시 적응적 backoff

    tokens_json 예:
        {
            "cloop": {
                "access_token": "...",
                "api_host": "https://cloop.cafe24api.com",
                "source_mall_id": "cloop",
                "shop_no": None,
            },
            ...
        }
    """

    def __init__(self, tokens_json: dict[str, dict], version: str = CAFE24_API_VERSION,
                 sleep_fn=None):
        """sleep_fn: backoff용 sleep 함수 (테스트에서 mock 가능, 기본 time.sleep)."""
        self._tokens = tokens_json
        self._version = version
        self._sleep = sleep_fn or _time.sleep

    # ---- Admin API ----
    def get(self, mall_id: str, path: str, params: dict | None = None) -> dict:
        """Admin API GET. path는 '/api/v2/admin/...' 또는 상대.

        401/403 → Cafe24TokenExpired raise.
        429 → 자동 backoff + 재시도 (최대 3회). 모두 실패하면 Cafe24RateLimited raise.

        반환: {"status": int, "body": dict | str, "headers": dict}
        """
        info = self._tokens[mall_id]
        host = info["api_host"].rstrip("/")
        # path normalization
        if not path.startswith("/"):
            path = "/" + path
        if not path.startswith("/api/"):
            path = "/api/v2/admin" + path if not path.startswith("/admin") else "/api/v2" + path

        full_params = dict(params or {})
        if info.get("shop_no") is not None and "shop_no" not in full_params:
            full_params["shop_no"] = str(info["shop_no"])

        url = host + path
        if full_params:
            url += "?" + _urllib_parse.urlencode(full_params)

        headers = self._build_headers(info["access_token"])
        return self._http_get_json_with_retry(mall_id, url, headers)

    # ---- CA Analytics API ----
    def get_ca(self, mall_id: str, path: str, params: dict | None = None) -> dict:
        """CA Analytics API GET (ca-api.cafe24data.com). mall_id 쿼리 자동 주입."""
        info = self._tokens[mall_id]
        if not path.startswith("/"):
            path = "/" + path

        full_params = dict(params or {})
        full_params.setdefault("mall_id", info.get("source_mall_id", mall_id))

        url = ANALYTICS_HOST + path + "?" + _urllib_parse.urlencode(full_params)
        headers = self._build_headers(info["access_token"])
        return self._http_get_json_with_retry(mall_id, url, headers)

    # ---- Pagination (Phase 1 신규) ----
    def get_all_pages(
        self,
        mall_id: str,
        path: str,
        params: dict | None = None,
        max_pages: int | None = None,
    ) -> list[dict]:
        """links[].rel == 'next' 자동 순회. 모든 페이지의 body dict를 리스트로 반환.

        max_pages: None이면 무제한. 안전을 위해 호출자가 지정 권장.

        반환: [page1_body, page2_body, ...] (각 페이지의 body dict 그대로)
        """
        pages: list[dict] = []
        next_url: str | None = None

        # 첫 페이지
        first = self.get(mall_id, path, params)
        pages.append(first["body"] if isinstance(first["body"], dict) else {"raw": first["body"]})

        # 다음 페이지 링크 추출
        next_url = _extract_next_url(first["body"])

        # 페이지 순회
        info = self._tokens[mall_id]
        headers = self._build_headers(info["access_token"])

        page_count = 1
        while next_url:
            if max_pages is not None and page_count >= max_pages:
                break
            result = self._http_get_json_with_retry(mall_id, next_url, headers)
            body = result["body"] if isinstance(result["body"], dict) else {"raw": result["body"]}
            pages.append(body)
            next_url = _extract_next_url(body)
            page_count += 1

        return pages

    # ---- internal ----
    def _build_headers(self, access_token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-Cafe24-Api-Version": self._version,
        }

    def _http_get_json_with_retry(self, mall_id: str, url: str, headers: dict[str, str]) -> dict:
        """HTTP GET → JSON parse. 401/403 → Cafe24TokenExpired. 429 → backoff 재시도 최대 3회.

        성공 응답 시 X-Cafe24-Call-Usage 헤더가 80% 초과면 다음 호출 전 backoff 적용 (이번 응답은 반환).
        """
        last_remain = 1.0
        for attempt in range(MAX_RETRIES + 1):
            try:
                result = self._http_get_json_once(mall_id, url, headers)
                # 성공 시 사용량 체크 후 후속 호출에 적응적 backoff 적용은 호출자 측 책임이 아니라
                # 본 클라이언트가 self._sleep로 사전 적용
                self._maybe_apply_usage_backoff(result.get("headers", {}))
                return result
            except Cafe24RateLimited as e:
                last_remain = e.remain_seconds
                if attempt >= MAX_RETRIES:
                    raise
                # backoff: 200ms → 500ms → 1s (RETRY_BACKOFF_SECONDS)
                delay = RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)]
                # X-Cafe24-Call-Remain이 있으면 그것도 고려 (더 긴 쪽 채택)
                delay = max(delay, last_remain)
                self._sleep(delay)
                continue
        # 도달 불가
        raise Cafe24RateLimited(mall_id, last_remain)

    def _http_get_json_once(self, mall_id: str, url: str, headers: dict[str, str]) -> dict:
        """단 1회 HTTP GET. 401/403/429는 raise."""
        req = _urllib_request.Request(url, headers=headers, method="GET")
        try:
            with _urllib_request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                body_bytes = resp.read()
                body_text = body_bytes.decode("utf-8") if body_bytes else ""
                body_json = _json.loads(body_text) if body_text else {}
                return {
                    "status": resp.status,
                    "body": body_json,
                    "headers": dict(resp.headers),
                }
        except _urllib_error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            if e.code in (401, 403):
                raise Cafe24TokenExpired(mall_id, e.code, err_body) from e
            if e.code == 429:
                remain = _safe_float(_headers_get(e.headers, "X-Cafe24-Call-Remain"), default=1.0)
                raise Cafe24RateLimited(mall_id, remain) from e
            return {
                "status": e.code,
                "body": err_body,
                "headers": dict(e.headers) if e.headers else {},
            }

    def _maybe_apply_usage_backoff(self, headers: dict) -> None:
        """X-Cafe24-Call-Usage 80%↑ 시 USAGE_BACKOFF_DELAY 만큼 대기."""
        usage = _safe_float(headers.get("X-Cafe24-Call-Usage"), default=0.0)
        if usage >= USAGE_BACKOFF_THRESHOLD:
            self._sleep(USAGE_BACKOFF_DELAY)

    # ---- 정적 메서드 ----
    @staticmethod
    def split_date_range(start: str, end: str, max_months: int = 3) -> list[tuple[str, str]]:
        """3개월 초과 기간 자동 분할.

        반환: [(start1, end1), (start2, end2), ...] 모두 'YYYY-MM-DD' 문자열
        """
        s = _dt.date.fromisoformat(start)
        e = _dt.date.fromisoformat(end)
        if e < s:
            raise ValueError(f"end({end}) before start({start})")

        chunks: list[tuple[str, str]] = []
        cur = s
        while cur <= e:
            year = cur.year + (cur.month - 1 + max_months) // 12
            month = (cur.month - 1 + max_months) % 12 + 1
            day = min(cur.day, 28)
            try:
                next_start = _dt.date(year, month, day)
            except ValueError:
                next_start = _dt.date(year, month, 28)
            chunk_end = min(next_start - _dt.timedelta(days=1), e)
            chunks.append((cur.isoformat(), chunk_end.isoformat()))
            cur = chunk_end + _dt.timedelta(days=1)
        return chunks

    @staticmethod
    def resolve_brand(input_str: str) -> list[str]:
        """alias → mall_id list. brand_registry.resolve_brand로 위임."""
        return _resolve_brand_impl(input_str)


# ---- helper functions ----
def _extract_next_url(body) -> str | None:
    """Cafe24 응답에서 links.next URL 추출. 없으면 None."""
    if not isinstance(body, dict):
        return None
    links = body.get("links")
    if not isinstance(links, list):
        return None
    for link in links:
        if isinstance(link, dict) and link.get("rel") == "next":
            href = link.get("href")
            if isinstance(href, str) and href:
                return href
    return None


def _safe_float(value, default: float) -> float:
    """value를 float로 변환. 실패 시 default 반환."""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _headers_get(headers, name: str):
    """HTTPError.headers는 dict-like or Message-like. .get() 안전 호출."""
    if headers is None:
        return None
    if hasattr(headers, "get"):
        return headers.get(name)
    return None


# ---- self-test entry (Phase 0 G0.3, Phase 1 G1.1) ----
def _self_test_live(mall_id: str) -> int:
    """라이브 토큰으로 1회 호출 (수동 실행용).

    필요 env: CAFE24_TOKENS_JSON (JSON 문자열)
    """
    import os
    import sys

    tokens_raw = os.environ.get("CAFE24_TOKENS_JSON")
    if not tokens_raw:
        print("ERROR: CAFE24_TOKENS_JSON env var not set", file=sys.stderr)
        return 1
    try:
        tokens = _json.loads(tokens_raw)
    except _json.JSONDecodeError as e:
        print(f"ERROR: CAFE24_TOKENS_JSON invalid JSON: {e}", file=sys.stderr)
        return 1
    if mall_id not in tokens:
        print(f"ERROR: mall_id={mall_id} not in tokens", file=sys.stderr)
        return 1

    client = Cafe24Client(tokens)
    try:
        result = client.get(mall_id, "/api/v2/admin/store")
    except Cafe24TokenExpired as e:
        print(f"ERROR: token expired for {mall_id}: {e}", file=sys.stderr)
        return 2
    except Cafe24RateLimited as e:
        print(f"ERROR: rate limited for {mall_id}: {e}", file=sys.stderr)
        return 3
    print(f"OK: mall={mall_id} status={result['status']}")
    interesting = [k for k in result["headers"] if "Cafe24" in k or "Api" in k]
    print(f"Usage headers: {interesting}")
    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="cafe24_client self-test (live)")
    parser.add_argument("--self-test", action="store_true", help="run live self-test")
    parser.add_argument("--mall-id", default="cloop", help="mall_id for self-test (default: cloop)")
    args = parser.parse_args()

    if args.self_test:
        raise SystemExit(_self_test_live(args.mall_id))
    parser.print_help()
