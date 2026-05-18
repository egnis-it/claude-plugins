---
name: cafe24-orders-export
description: Cafe24 자사몰 주문/취소/반품/교환 데이터를 적재한다. egnis-mcp에서 cafe24_get_access_token으로 토큰을 발급받아 Python 스크립트가 cafe24api.com을 직접 호출하여 일자별 주문수/취소수/취소율/매출액과 주문 상세를 집계하고, 단일 HTML 대시보드(reports/cafe24/<brand>/<period>/dashboard.html)와 CSV(orders_summary.csv, orders_detail.csv)로 저장한다. 사용자가 "주문 데이터", "주문 적재", "/cafe24-orders-export"를 호출하거나 --brand --period 옵션을 줄 때 동작한다.
---

# Cafe24 주문 데이터 적재 (GET 전용)

> **신규 스킬 작성 가이드 (ADR-003 §2):** 본 스킬은 반드시 `scripts/lib/dashboard_template.py`의
> 함수형 진입점 `render_dashboard(...)`만 사용한다. **외부 HTML 템플릿 파일 추가 금지.**
> 기존 `scripts/templates/sales_dashboard.html`은 PR-B에서 흡수되므로 grandfathered.

## 언제 쓰는가

사용자가 다음 중 하나를 요청하면 즉시 invoke:
- `/cafe24-orders-export` (옵션 없거나 `--brand=<id|전체>` `--period=YYYY-MM-DD~YYYY-MM-DD`)
- "주문 데이터 뽑아줘", "주문 적재", "취소율 보고서"
- 특정 브랜드의 주문 상세 시각화 요청

## 기본값

- `--brand` 미지정: **전체** (9개 몰 모두). brand id: `cloop, sprint, labnosh, braye, oneday1ball, groceryseoul, exerapy, drlabnosh, medileeds`
- `--period` 미지정: **최근 7일** (오늘 - 6일 ~ 오늘). 오늘 날짜는 system context의 `currentDate`를 사용.
- 옵션: `--include-cancels` (취소 상세), `--include-exchanges` (교환 상세) — 기본 off (호출량 증가)
- 옵션: `--dry-run` — 토큰 발급 + `/orders/count` 1회만 호출 후 종료

## 아키텍처

ADR-001 + ADR-003 준수:
- **MCP는 토큰 발급에만** 사용. 실제 데이터 호출은 Python이 `cafe24api.com` 직접 호출.
- 5스킬 모두 `scripts/lib/cafe24_client.Cafe24Client` 단일 클라이언트 import.
- 대시보드 HTML은 `scripts/lib/dashboard_template.render_dashboard(layout="timeseries", ...)` 함수형 진입점만 사용.

```
1. Claude (skill) → cafe24_get_access_token(mall_id)  ← MCP 1회/몰
2. Python (fetch_orders.py) → cafe24api.com 직접 GET   ← 페이지네이션 자동 순회
3. Python (build_orders_dashboard.py) → CSV + HTML 생성
```

## 워크플로우

### 1단계: 인자 파싱

`--brand=...` `--period=YYYY-MM-DD~YYYY-MM-DD` 형태 파싱.
- brand가 alias(예: 랩노쉬)면 mall_id로 변환 (`drlabnosh`).
- brand=전체/all/생략 시 9개 mall_id 모두.
- period 미지정 시 currentDate 기준 최근 7일.

### 2단계: 토큰 발급 (MCP)

대상 mall_id 각각에 대해 parallel 호출:

```
mcp__claude_ai_egnis-mcp__cafe24_get_access_token(mall_id=<mall_id>)
```

응답에서 `access_token`, `source_mall_id`, `shop_no`, `api_host`를 사용.

**중요**: 토큰은 짧은 수명. 401/403 발생 시 이 tool 재호출. Cafe24 OAuth는 매시 :30 KST에 자동 리프레시.

### 3단계: 데이터 수집 (Python)

`scripts/fetch_orders.py` 실행. 토큰을 환경변수로 전달:

```bash
CAFE24_TOKENS_JSON='{"cloop": {"access_token":"...", "api_host":"https://cloop.cafe24api.com", "source_mall_id":"cloop", "shop_no":null}, ...}' \
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/fetch_orders.py" \
  --brand cloop \
  --period-start 2026-05-11 --period-end 2026-05-17 \
  --out-dir ./reports/cafe24/<brand_or_all>/<period>/data/raw \
  [--include-cancels] [--include-exchanges] [--dry-run]
```

스크립트는:
- 각 몰 × 기간에 대해 `GET /api/v2/admin/orders` 자동 페이지네이션 (limit=1000)
- 각 몰에 대해 `GET /api/v2/admin/orders/count` 사전 검증
- 옵션: `GET /admin/cancellation/{claim_code}`, `GET /admin/exchange/{claim_code}` (--include-cancels/exchanges)
- 응답을 raw 디렉토리에 mall별 파일로 저장
  - `orders_<mall>.json`, `orders_count_<mall>.json`
- 3개월 초과 기간 자동 분할 (cafe24_client.split_date_range)
- 401/403 시 exit 2 → skill이 토큰 재발급 → 재실행

### 4단계: 집계 + 대시보드 생성

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/build_orders_dashboard.py" \
  --raw-dir ./reports/cafe24/<brand_or_all>/<period>/data/raw \
  --out-dir ./reports/cafe24/<brand_or_all>/<period> \
  --brand-label "<표시용 라벨>" \
  --period-start YYYY-MM-DD \
  --period-end YYYY-MM-DD
```

스크립트는:
- raw JSON 읽어 일자별 집계 (주문수, 취소수, 취소율, 매출액)
- `data/orders_summary.csv` 저장 (일자별)
- 옵션: `data/orders_detail.csv` (주문 단건 raw flatten)
- `dashboard.html` 생성 — `render_dashboard(layout="timeseries", ...)` 호출
  - brand 토글 (전체 + 각 몰)
  - KPI 카드 5개 (기간 주문 건수 / 기간 GMV / 결제 건수 / 평균 주문 단가 / 환불률)
  - 일자별 표 (sortable)
  - 추이 라인 차트 (취소율 추이)
  - CSV 다운로드 버튼

### 5단계: 결과 안내

```
✅ 주문 데이터 적재 완료

📊 대시보드: ./reports/cafe24/<brand>/<period>/dashboard.html
📁 CSV: ./reports/cafe24/<brand>/<period>/data/orders_summary.csv

요약 (기간 합계):
- 주문 건수: X건
- 결제 건수: Y건
- 취소 건수: Z건
- 취소율: W%
- 매출액: ₩V
```

가능하면 `open <path>` 명령으로 브라우저 띄움.

## 토큰 갱신 정책 (반드시 준수)

- Python이 cafe24api 호출 중 **401 또는 403** 응답 수신 시:
  1. fetch_orders.py는 즉시 해당 몰의 진행을 멈추고 stderr에 mall_id 표시 후 exit 2
  2. skill은 `cafe24_get_access_token(mall_id=<실패한 몰>)`을 다시 호출해 신규 토큰 받음
  3. `CAFE24_TOKENS_JSON` 업데이트 후 fetch_orders.py 재실행 (이미 수집된 raw JSON은 재사용)

## API 엔드포인트 참조

상세는 `docs/orders_api_reference.md` 참조.

| 데이터 | 엔드포인트 | 비고 |
|---|---|---|
| 주문 목록 | `GET /api/v2/admin/orders` | limit=1000, embed=items,buyer,receivers |
| 주문 건수 | `GET /api/v2/admin/orders/count` | dry-run 단일 호출 대상 |
| 취소 상세 | `GET /api/v2/admin/cancellation/{claim_code}` | --include-cancels 시만 |
| 교환 상세 | `GET /api/v2/admin/exchange/{claim_code}` | --include-exchanges 시만 |

## 디렉토리 산출물

```
./reports/cafe24/<brand_or_all>/<YYYY-MM-DD_to_YYYY-MM-DD>/
├── dashboard.html                     ← 메인 결과물 (brand 토글)
├── data/
│   ├── orders_summary.csv             ← 일자별 집계
│   ├── orders_detail.csv              ← (옵션) 주문 단건
│   └── raw/                           ← cafe24api 원본 JSON
└── api_reference.md                   ← 호출에 사용한 endpoint 요약 (재현성)
```

## 브랜드 alias 매핑

`scripts/lib/brand_registry.BRAND_ALIASES` 참조. 9몰 + 한글/별칭 매핑.
