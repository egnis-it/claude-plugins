---
name: cafe24-products-export
description: Cafe24 자사몰 상품/품목/카테고리 데이터를 적재한다. egnis-mcp에서 cafe24_get_access_token으로 토큰을 발급받아 Python 스크립트가 cafe24api.com을 직접 호출하여 상품번호별 판매가/공급가/재고/표시여부와 카테고리 매핑을 집계하고, 단일 HTML 대시보드(reports/cafe24/<brand>/<period>/dashboard.html)와 CSV(products_summary.csv, optional variants.csv)로 저장한다. 사용자가 "상품 데이터", "상품 적재", "/cafe24-products-export"를 호출하거나 --brand 옵션을 줄 때 동작한다. Tier 2 snapshot layout (추이 라인 없음, 분포 차트).
---

# Cafe24 상품 데이터 적재 (Tier 2 snapshot, GET 전용)

> **신규 스킬 작성 가이드 (ADR-003 §2):** 본 스킬은 반드시 `scripts/lib/dashboard_template.py`의
> 함수형 진입점 `render_dashboard(...)`만 사용한다. **외부 HTML 템플릿 파일 추가 금지.**
> 기존 `scripts/templates/sales_dashboard.html`은 PR-B에서 흡수되므로 grandfathered.

## Tier 2 (snapshot)

상품 데이터는 본질적으로 **스냅샷 데이터**(현재 시점 가격/재고)이지 시계열이 아니다. v3 plan §6의 Tier 분류에 따라 본 스킬은 snapshot layout (추이 라인 없음, 분포 차트만)을 사용한다.

- `dashboard_template.render_dashboard(layout="snapshot", ...)` 호출
- 차트: 가격대 분포 (bar chart)
- 시계열 표 없음 (G_UNIFORM_2)

## 언제 쓰는가

- `/cafe24-products-export` (옵션 없거나 `--brand=<id|전체>`)
- "상품 데이터 뽑아줘", "재고 현황", "상품 카탈로그 적재"
- 가격대/카테고리/재고 분포 분석

## 기본값

- `--brand` 미지정: **전체** (9개 몰)
- 기간 개념 없음 (스냅샷). `--period-start/--period-end`는 옵션이며 빈 값이면 today.
- `--include-variants`: 품목 상세까지 적재 (호출량 크게 증가, 기본 off)
- `--dry-run`: products/count 1회만 호출

## 아키텍처

```
1. Claude (skill) → cafe24_get_access_token(mall_id)  ← MCP
2. Python (fetch_products.py) → cafe24api.com 직접 호출
3. Python (build_products_dashboard.py) → CSV + snapshot HTML
```

## 워크플로우

### 1~2단계: 인자 + 토큰 (cafe24-orders-export 동일 패턴)

### 3단계: 데이터 수집

```bash
CAFE24_TOKENS_JSON='{...}' \
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/fetch_products.py" \
  --brand cloop \
  --out-dir ./reports/cafe24/cloop/_snapshot/data/raw \
  [--include-variants] [--dry-run]
```

스크립트는:
- `GET /api/v2/admin/products/count` (dry-run 단일 호출 대상)
- `GET /api/v2/admin/products` (페이지네이션, limit=1000)
- `GET /api/v2/admin/products/{no}/variants` (옵션, `--include-variants` 시만)

### 4단계: 집계 + 대시보드 (snapshot)

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/build_products_dashboard.py" \
  --raw-dir ./reports/cafe24/cloop/_snapshot/data/raw \
  --out-dir ./reports/cafe24/cloop/_snapshot \
  --brand-label "클룹"
```

산출:
- `data/products_summary.csv` (상품번호별 가격/재고)
- `data/products_variants.csv` (옵션)
- `dashboard.html` (layout="snapshot", 가격대 분포 bar chart)

dashboard KPI 4종 (Tier 2):
- 총 상품 수
- 진열 상품 수 (display=T)
- 평균 판매가
- 평균 재고

## 토큰 갱신 정책

401/403 → exit 2 → skill 재발급 → 재실행.

## API 엔드포인트 참조

상세는 `docs/products_api_reference.md` 참조.

| Endpoint | 출력 |
|----------|------|
| `GET /api/v2/admin/products/count` | dry-run 검증 |
| `GET /api/v2/admin/products` | `products_summary.csv` 본체 |
| `GET /api/v2/admin/products/{no}/variants` | `products_variants.csv` (옵션) |

SCOPE: `mall.read_product`

## 디렉토리 산출물

```
./reports/cafe24/<brand_or_all>/_snapshot/
├── dashboard.html             ← snapshot layout
├── data/
│   ├── products_summary.csv
│   ├── products_variants.csv  (옵션)
│   └── raw/
└── api_reference.md
```
