"""
scripts/lib/_dashboard_template_test.py — Phase 1 G1.2 게이트
v3 plan §7 Phase 1: render_dashboard Tier 1/Tier 2 layout 각 호출 시 valid HTML 출력 검증.
v3 plan §6 (C3 패치): G_UNIFORM_1~4 정량 grep gate.

실행:
    cd plugins/egnis-cafe24 && python3 -m unittest scripts.lib._dashboard_template_test
"""
from __future__ import annotations

import re
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path

_THIS = Path(__file__).resolve()
_LIB_DIR = _THIS.parent
_SCRIPTS_DIR = _LIB_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from lib.dashboard_template import render_dashboard  # noqa: E402


# ---- 공통 테스트 페이로드 ----
def _sample_brands():
    return [
        {"mall_id": "cloop", "label": "클룹", "data": {"orders": 100}},
        {"mall_id": "drlabnosh", "label": "랩노쉬", "data": {"orders": 50}},
    ]


def _sample_kpis():
    return [
        {"label": "구매자수", "value": "1,234명", "delta": "+5.2%", "accent": "positive"},
        {"label": "구매건수", "value": "1,500건", "delta": "-1.0%", "accent": "negative"},
        {"label": "구매개수", "value": "2,800개", "delta": None, "accent": "neutral"},
        {"label": "매출액", "value": "₩12,345,000", "delta": "+3.1%", "accent": "positive"},
    ]


def _sample_meta():
    return {
        "generated_at": "2026-05-18T14:30:00+09:00",
        "source": "cafe24api",
        "period": "2026-05-11 ~ 2026-05-17",
        "version": "v3",
        "api_version": "2026-03-01",
    }


def _sample_table_timeseries():
    return {
        "columns": ["일시", "주문수", "취소수", "취소율(%)", "매출액"],
        "rows": [
            ["2026-05-11", 100, 5, 5.0, 1000000],
            ["2026-05-12", 120, 8, 6.67, 1200000],
            ["2026-05-13", 90, 3, 3.33, 900000],
        ],
        "csv_filename": "orders_summary.csv",
    }


def _sample_table_snapshot():
    return {
        "columns": ["상품번호", "상품명", "판매가", "재고"],
        "rows": [
            [101, "샘플 A", 19900, 50],
            [102, "샘플 B", 29900, 30],
        ],
        "csv_filename": "products_summary.csv",
    }


def _sample_chart_timeseries():
    return {
        "type": "line",
        "x_labels": ["2026-05-11", "2026-05-12", "2026-05-13"],
        "series": [{"label": "매출", "data": [1000000, 1200000, 900000]}],
        "options": {"responsive": True},
    }


def _sample_chart_snapshot():
    return {
        "type": "bar",
        "x_labels": ["1만 미만", "1-3만", "3만 이상"],
        "series": [{"label": "상품수", "data": [10, 25, 5]}],
    }


# ---- valid HTML 파싱 ----
class _HtmlValidator(HTMLParser):
    """HTML5 기본 유효성: 시작/종료 태그 균형 + script/style 안의 < > 무시."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.errors: list[str] = []
        self._void_elements = frozenset({
            "br", "img", "input", "meta", "link", "hr", "area", "base", "col",
            "embed", "source", "track", "wbr",
        })

    def handle_starttag(self, tag, attrs):
        if tag not in self._void_elements:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self._void_elements:
            return
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        elif tag in self.stack:
            # 닫는 태그 누락 발견
            while self.stack and self.stack[-1] != tag:
                self.errors.append(f"unclosed: {self.stack.pop()}")
            if self.stack:
                self.stack.pop()
        else:
            self.errors.append(f"extra close: {tag}")


# ---- G1.2 + G_UNIFORM_1 정량 grep gate 검증 ----
class RenderDashboardTimeseriesTests(unittest.TestCase):
    """Tier 1 timeseries layout 검증."""

    def setUp(self):
        self.html = render_dashboard(
            layout="timeseries",
            title="Cafe24 주문 데이터 (Tier 1 예제)",
            brands=_sample_brands(),
            kpis=_sample_kpis(),
            table=_sample_table_timeseries(),
            chart=_sample_chart_timeseries(),
            meta=_sample_meta(),
        )

    def test_valid_html5(self):
        """HTML5 구조 — doctype + html/head/body 존재."""
        self.assertTrue(self.html.lstrip().lower().startswith("<!doctype html>"))
        self.assertIn("<html ", self.html)
        self.assertIn("<head>", self.html)
        self.assertIn("</head>", self.html)
        self.assertIn("<body>", self.html)
        self.assertIn("</body>", self.html)
        self.assertIn("</html>", self.html)

    def test_html_parses_balanced(self):
        """모든 시작/종료 태그 균형."""
        validator = _HtmlValidator()
        validator.feed(self.html)
        self.assertEqual(validator.errors, [],
                         f"HTML parse errors: {validator.errors}")

    def test_canvas_chart_type_line_present(self):
        """G_UNIFORM_2: timeseries → data-chart-type='line' 출현."""
        self.assertIn('data-chart-type="line"', self.html)

    def test_g_uniform_1_brand_toggle(self):
        """G_UNIFORM_1: data-brand-toggle 1개 이상."""
        count = self.html.count("data-brand-toggle=")
        self.assertGreaterEqual(count, 1, "brand toggle missing")

    def test_g_uniform_1_table(self):
        """G_UNIFORM_1: <table> 1개 이상."""
        count = len(re.findall(r"<table\b", self.html))
        self.assertGreaterEqual(count, 1, "<table> missing")

    def test_g_uniform_1_csv_download(self):
        """G_UNIFORM_1: data-csv-download 1개 이상."""
        count = self.html.count("data-csv-download=")
        self.assertGreaterEqual(count, 1, "csv download button missing")

    def test_kpi_cards_count(self):
        """4개 KPI 카드 렌더."""
        count = self.html.count('class="kpi-card"')
        self.assertEqual(count, 4)

    def test_brand_data_inline_json(self):
        """brand_data가 인라인 JSON으로 임베드."""
        self.assertIn('id="brand-data-json"', self.html)
        # mall_id 2개 모두 인라인에 포함
        self.assertIn('"cloop"', self.html)
        self.assertIn('"drlabnosh"', self.html)

    def test_meta_fields_rendered(self):
        self.assertIn("2026-05-11 ~ 2026-05-17", self.html)
        self.assertIn("2026-05-18T14:30:00+09:00", self.html)
        self.assertIn("2026-03-01", self.html)


class RenderDashboardSnapshotTests(unittest.TestCase):
    """Tier 2 snapshot layout 검증."""

    def setUp(self):
        self.html = render_dashboard(
            layout="snapshot",
            title="Cafe24 상품 데이터 (Tier 2 예제)",
            brands=_sample_brands(),
            kpis=_sample_kpis()[:3],  # snapshot은 KPI 3개로 차별화
            table=_sample_table_snapshot(),
            chart=_sample_chart_snapshot(),
            meta=_sample_meta(),
        )

    def test_g_uniform_2_no_line_chart(self):
        """G_UNIFORM_2: snapshot → data-chart-type='line' 부재."""
        self.assertNotIn('data-chart-type="line"', self.html)

    def test_g_uniform_2_bar_or_donut_present(self):
        """G_UNIFORM_2: snapshot → bar/donut 차트 출현."""
        has_bar = 'data-chart-type="bar"' in self.html
        has_donut = 'data-chart-type="donut"' in self.html
        self.assertTrue(has_bar or has_donut)

    def test_brand_toggle_present(self):
        self.assertIn("data-brand-toggle=", self.html)

    def test_csv_download_present(self):
        self.assertIn("data-csv-download=", self.html)


class GUniform2BoundaryTests(unittest.TestCase):
    """snapshot layout이 line 차트를 거부하는지 확인 (방어적 분기)."""

    def test_snapshot_with_explicit_line_chart_falls_back_to_bar(self):
        """snapshot layout인데 chart.type='line'을 줘도 line 강제 변환 거부."""
        html = render_dashboard(
            layout="snapshot",
            title="snapshot test",
            brands=_sample_brands(),
            kpis=_sample_kpis()[:3],
            table=_sample_table_snapshot(),
            chart={"type": "line", "series": [], "x_labels": []},
            meta=_sample_meta(),
        )
        # G_UNIFORM_2: snapshot은 line 부재 보장
        self.assertNotIn('data-chart-type="line"', html)


class InputValidationTests(unittest.TestCase):
    """잘못된 입력 거부."""

    def test_invalid_layout_raises(self):
        with self.assertRaises(ValueError):
            render_dashboard(
                layout="invalid",
                title="t",
                brands=[],
                kpis=[],
                table={"columns": [], "rows": []},
                chart=None,
                meta={},
            )

    def test_missing_table_keys_raises(self):
        with self.assertRaises(ValueError):
            render_dashboard(
                layout="timeseries",
                title="t",
                brands=[],
                kpis=[],
                table={"columns": []},  # rows 누락
                chart=None,
                meta={},
            )

    def test_invalid_accent_raises(self):
        with self.assertRaises(ValueError):
            render_dashboard(
                layout="timeseries",
                title="t",
                brands=[],
                kpis=[{"label": "x", "value": "1", "delta": None, "accent": "purple"}],
                table={"columns": ["a"], "rows": []},
                chart=None,
                meta={},
            )


class EmptyDataTests(unittest.TestCase):
    """빈 데이터로 호출해도 valid HTML 반환."""

    def test_empty_brands_kpis_rows(self):
        html = render_dashboard(
            layout="timeseries",
            title="empty",
            brands=[],
            kpis=[],
            table={"columns": ["일시"], "rows": [], "csv_filename": "empty.csv"},
            chart=None,
            meta=_sample_meta(),
        )
        # 빈 데이터여도 valid HTML 구조 유지
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("</html>", html)
        # csv download 버튼은 여전히 존재 (G_UNIFORM_1)
        self.assertIn("data-csv-download=", html)


class HtmlEscapeTests(unittest.TestCase):
    """XSS 방어 — < > & 등 escape."""

    def test_title_with_html_special_chars(self):
        html = render_dashboard(
            layout="timeseries",
            title='<script>alert("xss")</script>',
            brands=[],
            kpis=[],
            table={"columns": ["a"], "rows": [], "csv_filename": "x.csv"},
            chart=None,
            meta=_sample_meta(),
        )
        # script 태그가 그대로 렌더링되지 않아야 함
        self.assertNotIn('<script>alert("xss")</script>', html)
        # escape된 형태로 들어가야 함
        self.assertIn("&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
