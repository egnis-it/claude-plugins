"""
scripts/lib/_cafe24_client_test.py — Phase 0 G0.3 게이트
v3 plan §7 Phase 0: minimal viable stub의 200/401/429 mock 검증 + split_date_range/resolve_brand 단위.

실행:
    python3 -m unittest scripts.lib._cafe24_client_test  # cwd=plugins/egnis-cafe24
    또는
    cd plugins/egnis-cafe24/scripts/lib && python3 _cafe24_client_test.py

ADR-003 §3.2: urllib.request 표준 라이브러리만 사용 (unittest.mock도 표준).
"""
from __future__ import annotations

import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

# import path 자동 해결 (scripts/lib에서 직접 실행되거나 scripts에서 module로 호출되거나)
_THIS = Path(__file__).resolve()
_LIB_DIR = _THIS.parent
_SCRIPTS_DIR = _LIB_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from lib.cafe24_client import (  # noqa: E402
    BRAND_ALIASES,
    CAFE24_API_VERSION,
    MALL_ORDER,
    Cafe24Client,
    Cafe24RateLimited,
    Cafe24TokenExpired,
)


_SAMPLE_TOKENS = {
    "cloop": {
        "access_token": "tok-cloop-xxxx",
        "api_host": "https://cloop.cafe24api.com",
        "source_mall_id": "cloop",
        "shop_no": None,
    },
    "drlabnosh": {
        "access_token": "tok-drlabnosh-yyyy",
        "api_host": "https://drlabnosh.cafe24api.com",
        "source_mall_id": "drlabnosh",
        "shop_no": 1,
    },
}


def _mock_response(status: int, body: dict | str, headers: dict | None = None):
    """urllib.request.urlopen 컨텍스트 매니저 mock."""
    mock_resp = MagicMock()
    mock_resp.status = status
    if isinstance(body, dict):
        body_bytes = json.dumps(body).encode("utf-8")
    else:
        body_bytes = body.encode("utf-8")
    mock_resp.read.return_value = body_bytes
    mock_resp.headers = headers or {}
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


class Cafe24ClientGetTests(unittest.TestCase):
    """G0.3: Admin API get() 200/401 mock 검증."""

    def setUp(self):
        self.client = Cafe24Client(_SAMPLE_TOKENS)

    @patch("urllib.request.urlopen")
    def test_get_200_returns_body_dict(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(
            200,
            {"store": {"shop_no": 1, "name": "Cloop"}},
            headers={"X-Api-Call-Limit": "1/40"},
        )
        result = self.client.get("cloop", "/api/v2/admin/store")
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["body"]["store"]["name"], "Cloop")
        self.assertIn("X-Api-Call-Limit", result["headers"])

        # URL 구성 검증
        called_url = mock_urlopen.call_args[0][0].full_url
        self.assertIn("https://cloop.cafe24api.com/api/v2/admin/store", called_url)

    @patch("urllib.request.urlopen")
    def test_get_401_raises_token_expired(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://cloop.cafe24api.com/api/v2/admin/orders",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=io.BytesIO(b'{"error":{"code":401,"message":"invalid_token"}}'),
        )
        with self.assertRaises(Cafe24TokenExpired) as ctx:
            self.client.get("cloop", "/api/v2/admin/orders")
        self.assertEqual(ctx.exception.mall_id, "cloop")
        self.assertEqual(ctx.exception.status, 401)

    @patch("urllib.request.urlopen")
    def test_get_403_raises_token_expired(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://drlabnosh.cafe24api.com/api/v2/admin/customers",
            code=403,
            msg="Forbidden",
            hdrs={},
            fp=io.BytesIO(b'{"error":{"code":403,"message":"insufficient_scope"}}'),
        )
        with self.assertRaises(Cafe24TokenExpired) as ctx:
            self.client.get("drlabnosh", "/api/v2/admin/customers")
        self.assertEqual(ctx.exception.status, 403)

    @patch("urllib.request.urlopen")
    def test_get_429_raises_rate_limited_after_retries(self, mock_urlopen):
        """Phase 1: 429 응답 시 자동 backoff + 3회 재시도 후 최종 raise."""
        # MagicMock으로 headers를 모방
        err_headers = MagicMock()
        err_headers.get = MagicMock(return_value="32")  # X-Cafe24-Call-Remain: 32

        # 4번 연속 429 (initial + 3 retries 모두 실패)
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://cloop.cafe24api.com/api/v2/admin/orders",
            code=429,
            msg="Too Many Requests",
            hdrs=err_headers,
            fp=io.BytesIO(b"rate limit exceeded"),
        )

        # sleep mock으로 실제 대기 없이 빠르게 검증
        sleep_calls: list[float] = []
        client = Cafe24Client(_SAMPLE_TOKENS, sleep_fn=lambda s: sleep_calls.append(s))
        with self.assertRaises(Cafe24RateLimited) as ctx:
            client.get("cloop", "/api/v2/admin/orders")
        self.assertEqual(ctx.exception.mall_id, "cloop")
        # 3 retries (backoff 200ms→500ms→1s, 단 X-Cafe24-Call-Remain=32가 더 크므로 32초로 채택)
        self.assertEqual(len(sleep_calls), 3)
        for s in sleep_calls:
            self.assertGreaterEqual(s, 32.0)

    @patch("urllib.request.urlopen")
    def test_get_shop_no_auto_injection(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(200, {"products": []})
        self.client.get("drlabnosh", "/api/v2/admin/products")
        called_url = mock_urlopen.call_args[0][0].full_url
        # drlabnosh는 shop_no=1 이므로 자동 주입돼야 함
        self.assertIn("shop_no=1", called_url)


class Cafe24ClientGetCATests(unittest.TestCase):
    """CA Analytics API get_ca() — mall_id 자동 주입 + analytics host 사용."""

    def setUp(self):
        self.client = Cafe24Client(_SAMPLE_TOKENS)

    @patch("urllib.request.urlopen")
    def test_get_ca_uses_analytics_host(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(200, {"products": []})
        self.client.get_ca("cloop", "/products/hits", params={"start_date": "2026-05-01"})
        called_url = mock_urlopen.call_args[0][0].full_url
        self.assertIn("https://ca-api.cafe24data.com/products/hits", called_url)
        self.assertIn("mall_id=cloop", called_url)
        self.assertIn("start_date=2026-05-01", called_url)

    @patch("urllib.request.urlopen")
    def test_get_ca_401_raises_token_expired(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://ca-api.cafe24data.com/products/hits",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=io.BytesIO(b"unauthorized"),
        )
        with self.assertRaises(Cafe24TokenExpired):
            self.client.get_ca("cloop", "/products/hits")


class SplitDateRangeTests(unittest.TestCase):
    """split_date_range 정적 메서드 — 3개월 초과 자동 분할."""

    def test_same_day(self):
        result = Cafe24Client.split_date_range("2026-05-17", "2026-05-17")
        self.assertEqual(result, [("2026-05-17", "2026-05-17")])

    def test_one_week_no_split(self):
        result = Cafe24Client.split_date_range("2026-05-11", "2026-05-17")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], ("2026-05-11", "2026-05-17"))

    def test_six_months_splits_into_two(self):
        result = Cafe24Client.split_date_range("2026-01-01", "2026-06-30")
        self.assertEqual(len(result), 2)
        # 첫 청크는 2026-01-01부터 시작
        self.assertEqual(result[0][0], "2026-01-01")
        # 마지막 청크의 end는 2026-06-30
        self.assertEqual(result[-1][1], "2026-06-30")
        # 각 청크는 연속 (이전 end 다음날 = 다음 start)
        import datetime as dt
        for i in range(len(result) - 1):
            end_i = dt.date.fromisoformat(result[i][1])
            start_next = dt.date.fromisoformat(result[i + 1][0])
            self.assertEqual(start_next - end_i, dt.timedelta(days=1))

    def test_end_before_start_raises(self):
        with self.assertRaises(ValueError):
            Cafe24Client.split_date_range("2026-05-17", "2026-05-01")


class ResolveBrandTests(unittest.TestCase):
    """resolve_brand 정적 메서드 — alias → mall_id."""

    def test_all_returns_nine_malls(self):
        self.assertEqual(set(Cafe24Client.resolve_brand("all")), set(MALL_ORDER))
        self.assertEqual(set(Cafe24Client.resolve_brand("전체")), set(MALL_ORDER))
        self.assertEqual(set(Cafe24Client.resolve_brand("")), set(MALL_ORDER))

    def test_single_alias(self):
        self.assertEqual(Cafe24Client.resolve_brand("랩노쉬"), ["drlabnosh"])
        self.assertEqual(Cafe24Client.resolve_brand("클룹"), ["cloop"])
        self.assertEqual(Cafe24Client.resolve_brand("cloop"), ["cloop"])

    def test_comma_separated(self):
        result = Cafe24Client.resolve_brand("클룹,랩노쉬,brae".replace("brae", "braye"))
        self.assertEqual(result, ["cloop", "drlabnosh", "braye"])

    def test_unknown_raises(self):
        with self.assertRaises(KeyError):
            Cafe24Client.resolve_brand("unknown_brand_xyz")

    def test_nine_aliases_complete(self):
        """모든 9몰 mall_id가 BRAND_ALIASES에서 self-map되어 있어야 함."""
        for mall in MALL_ORDER:
            self.assertIn(mall, BRAND_ALIASES, f"{mall} not in BRAND_ALIASES")
            self.assertEqual(BRAND_ALIASES[mall], mall)


class Cafe24Client429RetryTests(unittest.TestCase):
    """Phase 1: 429 자동 backoff + 재시도 (200ms→500ms→1s)."""

    @patch("urllib.request.urlopen")
    def test_429_then_200_succeeds_after_retry(self, mock_urlopen):
        """첫 호출 429 → backoff 후 재시도 → 200 PASS."""
        err_headers = MagicMock()
        err_headers.get = MagicMock(return_value="0.1")

        err_429 = urllib.error.HTTPError(
            url="https://cloop.cafe24api.com/api/v2/admin/orders",
            code=429,
            msg="Too Many Requests",
            hdrs=err_headers,
            fp=io.BytesIO(b"rate limit"),
        )
        # 첫 호출은 429, 두 번째는 200
        mock_urlopen.side_effect = [
            err_429,
            _mock_response(200, {"orders": []}),
        ]

        sleep_calls: list[float] = []
        client = Cafe24Client(_SAMPLE_TOKENS, sleep_fn=lambda s: sleep_calls.append(s))
        result = client.get("cloop", "/api/v2/admin/orders")
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["body"]["orders"], [])
        # 1회 backoff 발생
        self.assertEqual(len(sleep_calls), 1)
        # 200ms 이상 (RETRY_BACKOFF_SECONDS[0])
        self.assertGreaterEqual(sleep_calls[0], 0.2)


class Cafe24ClientUsageBackoffTests(unittest.TestCase):
    """Phase 1: X-Cafe24-Call-Usage 80%↑ 시 적응적 backoff."""

    @patch("urllib.request.urlopen")
    def test_high_usage_triggers_backoff(self, mock_urlopen):
        """200 응답이지만 X-Cafe24-Call-Usage=85% → 다음 호출 전 backoff."""
        mock_urlopen.return_value = _mock_response(
            200,
            {"store": {}},
            headers={"X-Cafe24-Call-Usage": "85.0"},
        )
        sleep_calls: list[float] = []
        client = Cafe24Client(_SAMPLE_TOKENS, sleep_fn=lambda s: sleep_calls.append(s))
        result = client.get("cloop", "/api/v2/admin/store")
        self.assertEqual(result["status"], 200)
        # usage 80%↑이므로 USAGE_BACKOFF_DELAY 적용
        self.assertEqual(len(sleep_calls), 1)
        self.assertAlmostEqual(sleep_calls[0], 0.3)

    @patch("urllib.request.urlopen")
    def test_low_usage_no_backoff(self, mock_urlopen):
        """Usage 79%면 backoff 없음."""
        mock_urlopen.return_value = _mock_response(
            200,
            {"store": {}},
            headers={"X-Cafe24-Call-Usage": "79.0"},
        )
        sleep_calls: list[float] = []
        client = Cafe24Client(_SAMPLE_TOKENS, sleep_fn=lambda s: sleep_calls.append(s))
        client.get("cloop", "/api/v2/admin/store")
        self.assertEqual(sleep_calls, [])


class GetAllPagesTests(unittest.TestCase):
    """Phase 1: get_all_pages — links.next 자동 순회."""

    @patch("urllib.request.urlopen")
    def test_single_page_no_next(self, mock_urlopen):
        """links 없으면 단일 페이지만 반환."""
        mock_urlopen.return_value = _mock_response(200, {"orders": [{"id": "1"}]})
        client = Cafe24Client(_SAMPLE_TOKENS)
        pages = client.get_all_pages("cloop", "/api/v2/admin/orders")
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["orders"], [{"id": "1"}])

    @patch("urllib.request.urlopen")
    def test_three_pages_follow_next(self, mock_urlopen):
        """3 페이지 자동 순회."""
        mock_urlopen.side_effect = [
            _mock_response(200, {
                "orders": [{"id": "1"}],
                "links": [{"rel": "next", "href": "https://cloop.cafe24api.com/api/v2/admin/orders?offset=10"}],
            }),
            _mock_response(200, {
                "orders": [{"id": "2"}],
                "links": [{"rel": "next", "href": "https://cloop.cafe24api.com/api/v2/admin/orders?offset=20"}],
            }),
            _mock_response(200, {"orders": [{"id": "3"}]}),  # links 없음 = 종료
        ]
        client = Cafe24Client(_SAMPLE_TOKENS)
        pages = client.get_all_pages("cloop", "/api/v2/admin/orders")
        self.assertEqual(len(pages), 3)
        self.assertEqual([p["orders"][0]["id"] for p in pages], ["1", "2", "3"])

    @patch("urllib.request.urlopen")
    def test_max_pages_caps(self, mock_urlopen):
        """max_pages=2면 3페이지 데이터가 있어도 2페이지에서 중단."""
        mock_urlopen.side_effect = [
            _mock_response(200, {
                "orders": [{"id": "1"}],
                "links": [{"rel": "next", "href": "https://cloop.cafe24api.com/api/v2/admin/orders?offset=10"}],
            }),
            _mock_response(200, {
                "orders": [{"id": "2"}],
                "links": [{"rel": "next", "href": "https://cloop.cafe24api.com/api/v2/admin/orders?offset=20"}],
            }),
            _mock_response(200, {"orders": [{"id": "3"}]}),
        ]
        client = Cafe24Client(_SAMPLE_TOKENS)
        pages = client.get_all_pages("cloop", "/api/v2/admin/orders", max_pages=2)
        self.assertEqual(len(pages), 2)


class BrandRegistryTests(unittest.TestCase):
    """Phase 1: brand_registry 분리 후 역호환 + 9몰 alias 매핑."""

    def test_re_export_from_cafe24_client(self):
        """cafe24_client에서 BRAND_ALIASES/MALL_ORDER 역호환 import 가능."""
        from lib.cafe24_client import BRAND_ALIASES as A, MALL_ORDER as O
        from lib.brand_registry import BRAND_ALIASES as A2, MALL_ORDER as O2
        self.assertIs(A, A2)
        self.assertIs(O, O2)

    def test_nine_malls_self_map(self):
        """9개 mall_id가 BRAND_ALIASES에서 self-map."""
        for mall in MALL_ORDER:
            self.assertIn(mall, BRAND_ALIASES)
            self.assertEqual(BRAND_ALIASES[mall], mall)

    def test_label_for_known_and_unknown(self):
        from lib.brand_registry import label_for
        self.assertEqual(label_for("drlabnosh"), "랩노쉬")
        self.assertEqual(label_for("unknown_xyz"), "unknown_xyz")


class ApiVersionConstantTests(unittest.TestCase):
    """PR-B 마이그레이션 검증: fetch_sales.py가 cafe24_client.CAFE24_API_VERSION을 사용 (자체 상수 없음).

    PR-A 시점에는 fetch_sales.py:38에 CAFE24_API_VERSION 자체 정의가 있었으나,
    PR-B에서 라이브러리 호출로 교체되며 fetch_sales.py에서 제거됐다.
    이제 fetch_sales.py는 cafe24_client을 import하므로 자체 상수가 없는 것이 정상.
    """

    def test_fetch_sales_uses_cafe24_client(self):
        """fetch_sales.py가 lib.cafe24_client를 import 하는지 확인."""
        legacy_path = _SCRIPTS_DIR / "fetch_sales.py"
        self.assertTrue(legacy_path.exists(), f"legacy script not found: {legacy_path}")
        content = legacy_path.read_text(encoding="utf-8")
        self.assertIn("from lib.cafe24_client import", content,
                      "fetch_sales.py (PR-B 마이그레이션 후) cafe24_client import 필수")
        # 자체 상수 정의는 더 이상 없어야 함 (DRY-DEBT supersede)
        self.assertNotIn('CAFE24_API_VERSION = "2026-03-01"', content,
                         "PR-B 후 fetch_sales.py에 자체 CAFE24_API_VERSION 정의 없어야 함")

    def test_api_version_constant_value(self):
        """라이브러리 상수 값 자체 검증."""
        self.assertEqual(CAFE24_API_VERSION, "2026-03-01")


if __name__ == "__main__":
    unittest.main(verbosity=2)
