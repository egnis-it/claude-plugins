---
name: cafe24-salesreport-export
description: Cafe24 Admin API 매출통계 5종(dailysales, monthlysales, hourlysales, productsales, salesvolume) 원본 데이터를 적재한다. egnis-mcp에서 cafe24_get_access_token으로 토큰을 발급받아 Python 스크립트가 cafe24api.com을 직접 호출하여 PG/시간/상품/품목 기준 통계를 CSV 5종으로 저장하고, 5탭 HTML 대시보드(reports/cafe24/<brand>/<period>/dashboard.html)를 생성한다. 기존 cafe24-sales-dashboard와 역할 분리 — sales-dashboard는 일자별 시각화 중심, 이 스킬은 드릴다운용 원본 CSV 적재 중심. "/cafe24-salesreport-export"를 호출하거나 --brand --period 옵션을 줄 때 동작한다.
---

# Cafe24 매출통계 5종 적재 (GET 전용)

> **신규 스킬 작성 가이드 (ADR-003 §2):** 본 스킬은 반드시 `scripts/lib/dashboard_template.py`의
> 함수형 진입점 `render_dashboard(...)`만 사용한다. **외부 HTML 템플릿 파일 추가 금지.**
> 기존 `scripts/templates/sales_dashboard.html`은 PR-B에서 흡수되므로 grandfathered.

## 기존 cafe24-sales-dashboard와의 역할 분리

- **`cafe24-sales-dashboard`**: 일자별 매출 종합 분석 + 시각화 중심 (단일 통합 뷰, hourlysales + members/sales 합산)
- **`cafe24-salesreport-export`**: PG/시간/상품/품목 기준 **원본 통계 데이터 적재** 중심 (드릴다운용 CSV 5종)

## 언제 쓰는가

- `/cafe24-salesreport-export` (옵션 없거나 `--brand=<id|전체>` `--period=YYYY-MM-DD~YYYY-MM-DD`)
- "매출통계 원본 적재", "상품별 판매통계", "시간대별 매출 CSV"
- 특정 PG/시간대 드릴다운 분석 요청

## 기본값

- `--brand` 미지정: **전체** (9개 몰)
- `--period` 미지정: **최근 7일**
- `--dry-run`: dailysales 1회만 호출

## 아키텍처

```
1. Claude (skill) → cafe24_get_access_token(mall_id) for each mall  ← MCP
2. Python (fetch_salesreport.py) → cafe24api.com 직접 호출 (5 endpoints)
3. Python (build_salesreport_dashboard.py) → CSV 5종 + 5탭 HTML
```

## 워크플로우

### 1단계: 인자 파싱 / 2단계: 토큰 발급 (MCP)

cafe24-orders-export와 동일 패턴. `mcp__claude_ai_egnis-mcp__cafe24_get_access_token(mall_id=<...>)` parallel 호출.

### 3단계: 데이터 수집

```bash
CAFE24_TOKENS_JSON='{...}' \
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/fetch_salesreport.py" \
  --brand cloop \
  --period-start 2026-05-01 --period-end 2026-05-31 \
  --out-dir ./reports/cafe24/cloop/2026-05-01_to_2026-05-31/data/raw \
  [--dry-run]
```

스크립트는 각 몰에 대해:
- `GET /admin/financials/dailysales` (일별 매출, PG 정보 함께)
- `GET /admin/financials/monthlysales` (월별 매출)
- `GET /admin/reports/hourlysales` (시간대별 매출, device_type 미지원)
- `GET /admin/reports/productsales` (상품별 판매)
- `GET /admin/reports/salesvolume` (판매수량)

라이트한 endpoint이므로 페이지네이션 영향 적음. 단 hourlysales는 limit=1000, productsales는 limit=1000 적용.

### 4단계: 집계 + 대시보드

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/build_salesreport_dashboard.py" \
  --raw-dir ./reports/cafe24/cloop/2026-05-01_to_2026-05-31/data/raw \
  --out-dir ./reports/cafe24/cloop/2026-05-01_to_2026-05-31 \
  --brand-label "클룹" \
  --period-start 2026-05-01 --period-end 2026-05-31
```

산출:
- `data/dailysales.csv`
- `data/monthlysales.csv`
- `data/hourlysales.csv`
- `data/productsales.csv`
- `data/salesvolume.csv`
- `dashboard.html` (5탭 = 5종 통계, brand 토글)

### 5단계: 결과 안내

```
✅ 매출통계 5종 적재 완료
📊 대시보드 (5탭): ./reports/cafe24/<brand>/<period>/dashboard.html
📁 CSV 5종: data/
```

## 토큰 갱신 정책

401/403 → exit 2 → skill이 토큰 재발급 → 재실행. 이미 수집된 raw JSON은 재사용 (idempotent).

## API 엔드포인트 참조

상세는 `docs/salesreport_api_reference.md` 참조.

| Endpoint | 출력 CSV |
|----------|----------|
| `GET /api/v2/admin/financials/dailysales` | `dailysales.csv` |
| `GET /api/v2/admin/financials/monthlysales` | `monthlysales.csv` |
| `GET /api/v2/admin/reports/hourlysales` | `hourlysales.csv` |
| `GET /api/v2/admin/reports/productsales` | `productsales.csv` |
| `GET /api/v2/admin/reports/salesvolume` | `salesvolume.csv` |

SCOPE: `mall.read_salesreport`

## 디렉토리 산출물

```
./reports/cafe24/<brand_or_all>/<period>/
├── dashboard.html
├── data/
│   ├── dailysales.csv
│   ├── monthlysales.csv
│   ├── hourlysales.csv
│   ├── productsales.csv
│   ├── salesvolume.csv
│   └── raw/
└── api_reference.md
```
