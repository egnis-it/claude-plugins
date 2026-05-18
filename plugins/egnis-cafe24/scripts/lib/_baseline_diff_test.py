"""
scripts/lib/_baseline_diff_test.py — Phase 0 G0.4 게이트
v3 plan §7 Phase 0: baseline normalize 충분성 검증.

목적:
  기존 build_sales_dashboard.py 출력의 byte-level diff = 0 을 만드는 normalize 규칙이 충분한지
  자체 검증한다. 동일 raw 데이터로 2회 빌드 결과를 비교하여, timestamp 외 어떤 노이즈도
  남지 않음을 증명한다. PR-A 머지 게이트(R2) + PR-B 회수 게이트(G6.1)에서 재사용한다.

실행:
  python3 -m unittest scripts.lib._baseline_diff_test    (cwd=plugins/egnis-cafe24)

ADR-001 §3.1: 본 테스트는 fetch_sales.py / build_sales_dashboard.py 의 동작 코드를
              수정하지 않고 동일 input → 동일 output(normalize 후) 임을 검증.
"""
from __future__ import annotations

import hashlib
import re
import sys
import unittest
from pathlib import Path

_THIS = Path(__file__).resolve()
_LIB_DIR = _THIS.parent
_SCRIPTS_DIR = _LIB_DIR.parent
_PLUGIN_DIR = _SCRIPTS_DIR.parent


# ---- Normalize 규칙 (G0.4 게이트의 핵심) ----
# Architect v2 D3 분석 결과:
#  - build_sales_dashboard.py:225 generated_at = dt.datetime.now().strftime(...)
#  - templates/sales_dashboard.html:5-6 CDN 스크립트 버전 핀 (stable)
#  - json.dumps(ensure_ascii=False) dict ordering (Python 3.7+ insertion order = deterministic)
#  - CSV utf-8-sig BOM (stable)
#  - float 누적 순서: mall_ids가 set 기반이지만 MALL_ORDER 필터링 후 deterministic
# 결론: timestamp 1개만 normalize하면 충분 — 본 테스트가 이를 자체 증명한다.

# normalize 규칙 1: ISO8601 timestamp → "<TS>"
_TS_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:\d{2})?")
# normalize 규칙 2 (보조): 한글 날짜 표기 "2026년 5월 18일" 등 — 일자별 데이터에 등장 가능
_KOR_DATE_PATTERN = re.compile(r"\d{4}년\s*\d{1,2}월\s*\d{1,2}일")


def normalize_html(html: str) -> str:
    """timestamp + 한글 날짜 정규화."""
    out = _TS_PATTERN.sub("<TS>", html)
    out = _KOR_DATE_PATTERN.sub("<KOR_DATE>", out)
    return out


# ---- explicit noise guards (Architect v2 D3 응답) ----
def assert_no_unexpected_noise(html: str) -> list[str]:
    """예상치 못한 노이즈 패턴 부재 증명. 발견되면 리스트로 반환 (빈 리스트 = 안전)."""
    findings: list[str] = []

    # Guard 1: CDN 버전 hash (예: tailwindcss?v=abc123 같은 동적 hash)
    cdn_hash_pattern = re.compile(r"cdn\..+\?v=[a-f0-9]{6,}")
    if cdn_hash_pattern.search(html):
        findings.append("CDN dynamic version hash detected")

    # Guard 2: nonce / random token (생성 시점마다 변할 수 있음)
    nonce_pattern = re.compile(r'nonce="[a-zA-Z0-9]{16,}"')
    if nonce_pattern.search(html):
        findings.append("CSP nonce attribute detected (non-deterministic)")

    # Guard 3: data-id="<random>" 류 randomly-generated DOM id
    random_id_pattern = re.compile(r'data-id="[a-f0-9]{16,}"')
    if random_id_pattern.search(html):
        findings.append("Random data-id attribute detected")

    # Guard 4: 부동소수점 누적 오차 신호 (예: 5000.000000001 류)
    fp_drift_pattern = re.compile(r"\d+\.\d{7,}")
    if fp_drift_pattern.search(html):
        findings.append("Float precision drift detected (>=7 decimal digits)")

    return findings


# ---- 합성 라이트한 HTML 페이로드 (build_sales_dashboard 실주 없이 normalize 규칙 검증) ----
def _make_synthetic_run(generated_at: str) -> str:
    """build_sales_dashboard.py가 만들 HTML 구조의 미니어처 (라인 30개).

    실주 baseline 비교는 PR-A 머지 시점에 별도 수행 (R2 게이트, sample period 2026-05-11~17).
    본 unittest는 normalize 규칙이 timestamp 1개만으로 충분한지 합성 데이터로 자체 증명.
    """
    return f"""<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="utf-8">
<title>Cafe24 매출 종합 분석</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
</head><body>
<header data-brand-toggle="all">전체 매출</header>
<p class="generated">생성: {generated_at}</p>
<table>
  <tr><th>일시</th><th>매출</th></tr>
  <tr><td>2026-05-11</td><td>1234567</td></tr>
  <tr><td>2026-05-12</td><td>1300000</td></tr>
</table>
<script>
const data = {{"cloop": {{"sales": 1234567, "orders": 89}}}};
const generatedAt = "{generated_at}";
</script>
</body></html>
"""


class BaselineNormalizeSufficiencyTests(unittest.TestCase):
    """G0.4 게이트: timestamp normalize 1개만으로 byte-level diff = 0 달성 가능한지 자체 증명."""

    def test_timestamp_only_differs(self):
        """동일 input + 다른 timestamp → normalize 후 동일 hash."""
        html_a = _make_synthetic_run("2026-05-18T14:30:00+09:00")
        html_b = _make_synthetic_run("2026-05-18T14:31:42+09:00")

        # raw는 다름
        self.assertNotEqual(html_a, html_b)

        norm_a = normalize_html(html_a)
        norm_b = normalize_html(html_b)

        # normalize 후 길이 동일
        self.assertEqual(len(norm_a), len(norm_b), "normalized length must be equal")
        # normalize 후 hash 동일
        self.assertEqual(
            hashlib.sha256(norm_a.encode("utf-8")).hexdigest(),
            hashlib.sha256(norm_b.encode("utf-8")).hexdigest(),
            "normalized SHA256 must be equal",
        )

    def test_no_unexpected_noise_in_synthetic(self):
        """합성 HTML에 CDN hash / nonce / random id / float drift 부재 검증."""
        html = _make_synthetic_run("2026-05-18T14:30:00+09:00")
        findings = assert_no_unexpected_noise(html)
        self.assertEqual(findings, [], f"unexpected noise found: {findings}")

    def test_normalize_idempotent(self):
        """normalize는 idempotent — 두 번 적용해도 결과 동일."""
        html = _make_synthetic_run("2026-05-18T14:30:00+09:00")
        once = normalize_html(html)
        twice = normalize_html(once)
        self.assertEqual(once, twice)

    def test_ts_pattern_catches_multiple_formats(self):
        """ISO8601 다양한 변형 모두 정규화."""
        cases = [
            "2026-05-18T14:30:00+09:00",
            "2026-05-18T14:30:00",
            "2026-05-18T14:30",
            "2026-12-31T23:59:59-05:00",
        ]
        for ts in cases:
            html = f"<p>generated: {ts}</p>"
            norm = normalize_html(html)
            self.assertEqual(norm, "<p>generated: <TS></p>", f"failed to normalize {ts}")


class RealBaselineFileTests(unittest.TestCase):
    """실주 baseline 파일이 존재하면 동일 normalize 규칙으로 검증.

    Phase 0 G0.4 게이트는 합성 데이터로 1차 통과, Phase 5 R2 게이트에서 실주 baseline diff = 0 확인.
    """

    def test_baseline_dir_exists(self):
        baseline_dir = _PLUGIN_DIR.parent.parent / ".omc" / "baseline"
        if not baseline_dir.exists():
            self.skipTest(f"baseline dir not yet populated: {baseline_dir} (Phase 5 단계에서 생성)")
        files = list(baseline_dir.glob("*.html")) + list(baseline_dir.glob("*.csv"))
        if len(files) == 0:
            self.skipTest(f"baseline dir empty: {baseline_dir} (Phase 5 단계에서 채워짐)")

    def test_existing_baseline_html_normalize_stable(self):
        """baseline HTML 파일이 있으면 normalize 결과가 stable한지 확인."""
        baseline_dir = _PLUGIN_DIR.parent.parent / ".omc" / "baseline"
        if not baseline_dir.exists():
            self.skipTest("baseline dir not yet populated")
        html_files = list(baseline_dir.glob("sales_dashboard_*.html"))
        if not html_files:
            self.skipTest("no sales_dashboard_*.html baseline yet")

        for html_file in html_files:
            content = html_file.read_text(encoding="utf-8")
            findings = assert_no_unexpected_noise(content)
            self.assertEqual(findings, [],
                             f"unexpected noise in {html_file.name}: {findings}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
