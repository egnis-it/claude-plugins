"""
scripts/_spike_import_test.py — Phase 0 G0.1/G0.2 게이트
v3 plan §7 Phase 0: 4 호출 시나리오 검증 (A/B/C/D)

호출 시나리오:
  A: python3 /abs/path/scripts/_spike_import_test.py     (cwd 임의)
  B: cd plugins/egnis-cafe24/scripts && python3 _spike_import_test.py
  C: cd plugins/egnis-cafe24 && python3 -m scripts._spike_import_test
  D: cd plugins/egnis-cafe24/scripts && python3 -m _spike_import_test

게이트 (v3 plan §7 Phase 0):
  - G0.1: A/B 시나리오 모두 PASS  ← 필수 (Option A 채택 조건)
  - G0.2: C 시나리오 결과로 Option A vs A' 분기 결정
    - A/B PASS && C PASS → Option A (`scripts/lib/cafe24_client.py`) 유지
    - A/B PASS && C FAIL → Option A' (`scripts/cafe24_client.py` 평면 배치) fallback
  - D는 정보 수집용 (대안 호출 패턴)

ADR-003 §3.2: urllib.request 표준 라이브러리만 사용 (외부 의존성 0).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 스크립트 위치 출력 (디버깅용)
_THIS = Path(__file__).resolve()
_SCRIPTS_DIR = _THIS.parent
_PLUGIN_DIR = _SCRIPTS_DIR.parent


def _detect_scenario() -> str:
    """현재 호출 시나리오 추정."""
    is_module = __name__ != "__main__" or "-m" in sys.argv or sys.argv[0].endswith(__file__) is False
    cwd = Path.cwd()
    if "__main__" in sys.modules and sys.modules["__main__"].__file__ == str(_THIS):
        # 직접 실행 (A 또는 B)
        if cwd == _SCRIPTS_DIR:
            return "B"
        return "A"
    # -m 으로 실행
    if cwd == _PLUGIN_DIR:
        return "C"
    if cwd == _SCRIPTS_DIR:
        return "D"
    return "UNKNOWN"


def check_import_lib_cafe24_client() -> tuple[bool, str]:
    """`from lib.cafe24_client import Cafe24Client` 시도."""
    try:
        # 동적 import — sys.path 의존성 검증
        if str(_SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(_SCRIPTS_DIR))
        from lib.cafe24_client import Cafe24Client  # noqa: F401
        return True, "from lib.cafe24_client import Cafe24Client → OK"
    except Exception as e:
        return False, f"from lib.cafe24_client import Cafe24Client → FAIL: {type(e).__name__}: {e}"


def check_import_relative() -> tuple[bool, str]:
    """`from .lib.cafe24_client import Cafe24Client` 시도 (package context 필요)."""
    if __package__ is None or __package__ == "":
        return False, "from .lib.cafe24_client import → SKIP (not in package context)"
    try:
        # 패키지 컨텍스트에서만 동작
        from .lib.cafe24_client import Cafe24Client  # type: ignore  # noqa: F401
        return True, "from .lib.cafe24_client import → OK (package context)"
    except Exception as e:
        return False, f"from .lib.cafe24_client import → FAIL: {type(e).__name__}: {e}"


def check_minimal_stub_callable() -> tuple[bool, str]:
    """Minimal stub의 split_date_range / resolve_brand 정적 메서드가 동작하는가?"""
    try:
        if str(_SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(_SCRIPTS_DIR))
        from lib.cafe24_client import Cafe24Client
        chunks = Cafe24Client.split_date_range("2026-05-11", "2026-05-17")
        if chunks != [("2026-05-11", "2026-05-17")]:
            return False, f"split_date_range output unexpected: {chunks}"
        brands = Cafe24Client.resolve_brand("cloop")
        if brands != ["cloop"]:
            return False, f"resolve_brand('cloop') unexpected: {brands}"
        return True, "minimal stub callable (split_date_range + resolve_brand OK)"
    except Exception as e:
        return False, f"minimal stub call FAIL: {type(e).__name__}: {e}"


def main() -> int:
    scenario = _detect_scenario()
    print(f"=== _spike_import_test.py ===")
    print(f"detected scenario: {scenario}")
    print(f"__name__: {__name__}")
    print(f"__package__: {__package__!r}")
    print(f"sys.argv[0]: {sys.argv[0]}")
    print(f"cwd: {Path.cwd()}")
    print(f"script abs path: {_THIS}")
    print(f"scripts dir: {_SCRIPTS_DIR}")
    print(f"sys.path[0]: {sys.path[0]}")
    print()

    results: list[tuple[str, bool, str]] = []

    ok, msg = check_import_lib_cafe24_client()
    results.append(("from lib.cafe24_client import", ok, msg))

    ok, msg = check_import_relative()
    results.append(("from .lib.cafe24_client import", ok, msg))

    ok, msg = check_minimal_stub_callable()
    results.append(("minimal stub callable", ok, msg))

    print("=== Results ===")
    all_critical_ok = True
    for name, ok, msg in results:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}")
        print(f"         {msg}")
        if name == "from lib.cafe24_client import" and not ok:
            all_critical_ok = False
        if name == "minimal stub callable" and not ok:
            all_critical_ok = False

    print()
    print(f"=== Scenario {scenario} verdict ===")
    if scenario in ("A", "B"):
        if all_critical_ok:
            print(f"  Scenario {scenario}: PASS (G0.1 satisfied for this scenario)")
            return 0
        else:
            print(f"  Scenario {scenario}: FAIL (G0.1 NOT satisfied)")
            return 1
    elif scenario == "C":
        if all_critical_ok:
            print(f"  Scenario C: PASS → Option A 채택 가능 (G0.2 → Option A)")
        else:
            print(f"  Scenario C: FAIL → Option A' fallback 발동 (G0.2 → Option A')")
        return 0 if all_critical_ok else 0  # C는 정보 수집, exit 0
    elif scenario == "D":
        if all_critical_ok:
            print(f"  Scenario D: PASS (정보용)")
        else:
            print(f"  Scenario D: FAIL (정보용, 차단 사유 아님)")
        return 0
    else:
        print(f"  UNKNOWN scenario detected. Manual review needed.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
