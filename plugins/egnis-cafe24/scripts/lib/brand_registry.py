"""
scripts/lib/brand_registry.py — 9몰 brand alias 매핑 (Phase 1)

Plan: .omc/plans/cafe24-mcp-skills-v3.md §7 Phase 1.4
ADR-003 §3.2: 외부 의존성 없음.

cafe24_client.py에 있던 BRAND_ALIASES/MALL_ORDER/MALL_LABELS를 별도 모듈로 분리.
이 모듈은 5개 신규 스킬과 (PR-B 머지 시점에) 기존 cafe24-sales-dashboard 모두 import한다.
"""
from __future__ import annotations

# 9개 자사몰 mall_id (안정적 순서, 대시보드 brand 토글 순서)
MALL_ORDER: list[str] = [
    "cloop",
    "sprint",
    "labnosh",
    "braye",
    "oneday1ball",
    "groceryseoul",
    "exerapy",
    "drlabnosh",
    "medileeds",
]

# 브랜드 라벨 (사용자에게 표시되는 이름)
MALL_LABELS: dict[str, str] = {
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

# alias → mall_id 매핑 (한글 + 영문 + 별칭 모두 self-map 포함)
BRAND_ALIASES: dict[str, str] = {
    # cloop
    "클룹": "cloop", "cloop": "cloop",
    # sprint
    "스프린트": "sprint", "에반게리온": "sprint", "sprint": "sprint",
    # labnosh
    "한끼통살": "labnosh", "에잇템": "labnosh", "labnosh": "labnosh",
    # braye
    "브레이": "braye", "braye": "braye",
    # oneday1ball
    "오원": "oneday1ball", "o1": "oneday1ball", "oneday1ball": "oneday1ball",
    # groceryseoul
    "그로서리서울": "groceryseoul", "groceryseoul": "groceryseoul",
    # exerapy
    "엑쎄라피": "exerapy", "exerapy": "exerapy",
    # drlabnosh
    "랩노쉬": "drlabnosh", "drlabnosh": "drlabnosh",
    # medileeds
    "메디리즈": "medileeds", "medileeds": "medileeds",
}

# 전체/all 키워드 (대소문자 무관, 공백 trim 후 비교)
ALL_KEYWORDS: frozenset[str] = frozenset({"", "all", "전체"})


def resolve_brand(input_str: str) -> list[str]:
    """alias → mall_id list.

    Args:
        input_str: 단일 alias ("랩노쉬"), 콤마 구분 ("클룹,랩노쉬"), 또는 ""/all/전체

    Returns:
        mall_id의 list. 전체일 경우 MALL_ORDER 그대로.

    Raises:
        KeyError: 알 수 없는 alias.
    """
    if input_str is None:
        return list(MALL_ORDER)
    cleaned = input_str.strip()
    if cleaned.lower() in ALL_KEYWORDS:
        return list(MALL_ORDER)

    # 단일 alias
    if cleaned in BRAND_ALIASES:
        return [BRAND_ALIASES[cleaned]]

    # comma-separated
    result: list[str] = []
    for token in cleaned.split(","):
        token = token.strip()
        if token in BRAND_ALIASES:
            mall = BRAND_ALIASES[token]
            if mall not in result:
                result.append(mall)
        else:
            raise KeyError(f"Unknown brand alias: {token!r}")
    return result


def label_for(mall_id: str) -> str:
    """mall_id → 표시용 라벨. 등록되지 않은 mall_id면 mall_id 자체 반환."""
    return MALL_LABELS.get(mall_id, mall_id)
