---
name: cafe24-ca-api-export
description: Cafe24 Analytics(CA) API 베타로 상품별 노출수(hits)/담김수(cart action)/판매건수(sales) 데이터를 적재하고 전환율(sales/hits)을 계산한다. 이그니스 인프라개발팀 슬랙 요청 "상품 상세 페이지 개선 전후 전환율 측정"에 직접 대응. egnis-mcp에서 cafe24_get_access_token으로 토큰을 발급받아 Python이 ca-api.cafe24data.com을 직접 호출하여 CSV 3종(product_hits.csv, cart_actions.csv, product_sales.csv)과 전환율 매트릭스 HTML 대시보드(reports/cafe24/<brand>/<period>/dashboard.html)를 저장한다. "/cafe24-ca-api-export", "전환율 측정", "CA API 적재"를 호출하거나 --brand --period 옵션을 줄 때 동작한다.
---

# Cafe24 CA API (베타) — 전환율 측정 (GET 전용)

> **신규 스킬 작성 가이드 (ADR-003 §2):** 본 스킬은 반드시 `scripts/lib/dashboard_template.py`의
> 함수형 진입점 `render_dashboard(...)`만 사용한다. **외부 HTML 템플릿 파일 추가 금지.**
> 기존 `scripts/templates/sales_dashboard.html`은 PR-B에서 흡수되므로 grandfathered.

## ⚠️ 베타 의존성

> **Cafe24 CA API는 베타 버전**입니다 (2026-05-18 검증 시점). endpoint/응답 스키마가 변경될 수 있습니다.
> 응답 스키마 변경 발견 시 `docs/ca_api_reference.md`를 우선 업데이트하고, `build_ca_api_dashboard.py`의 컬럼 추출 로직만 수정합니다.
> 만약 endpoint 자체가 변경되면 `scripts/lib/cafe24_client.get_ca(...)` 한 곳에서만 path를 수정합니다 (ADR-003 §3.2 단일 진입점).

## 슬랙 시나리오 (직접 대응)

이그니스 인프라개발팀의 슬랙 요청 (2026-05-18 발췌):
> "현재 필요한 데이터는 상품별 노출수, 담김수, 판매건수 등을 기반으로 한 전환율 관련 지표입니다.
> 해당 데이터를 활용하여 상품 상세 페이지 개선 작업의 전후 효과를 측정하고자 합니다.
> 개선 전후의 전환율 변화를 시계열로 확인하는 것이 주요 목적입니다."

본 스킬은 이 시나리오에 직접 대응:
- `/cafe24-ca-api-export --brand=cloop --period=2026-04-01~2026-04-30` (개선 전)
- `/cafe24-ca-api-export --brand=cloop --period=2026-05-01~2026-05-31` (개선 후)
- 두 dashboard.html의 전환율 매트릭스 비교 + CSV로 시계열 결합 (Excel/Claude Code)

## 언제 쓰는가

- `/cafe24-ca-api-export` (옵션 없거나 `--brand=<id|전체>` `--period=YYYY-MM-DD~YYYY-MM-DD`)
- "전환율 측정", "상품 상세 개선 효과", "CA API 적재"
- 상품 상세 페이지 A/B 테스트 전후 비교

## 기본값

- `--brand` 미지정: **전체** (9개 몰)
- `--period` 미지정: **최근 30일** (CA API는 일 단위 집계라 7일은 데이터 부족 가능)
- `--dry-run`: products/hits 1회만 호출

## 아키텍처

```
1. Claude (skill) → cafe24_get_access_token(mall_id) for each mall  ← MCP
2. Python (fetch_ca_api.py) → ca-api.cafe24data.com 직접 호출 (3 endpoints, get_ca)
3. Python (build_ca_api_dashboard.py) → CSV 3종 + 전환율 매트릭스 HTML
```

## 워크플로우

### 1~2단계: 인자 + 토큰 (cafe24-orders-export와 동일 패턴)

### 3단계: 데이터 수집

```bash
CAFE24_TOKENS_JSON='{...}' \
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/fetch_ca_api.py" \
  --brand cloop \
  --period-start 2026-04-01 --period-end 2026-04-30 \
  --out-dir ./reports/cafe24/cloop/2026-04-01_to_2026-04-30/data/raw \
  [--dry-run]
```

스크립트는 각 몰에 대해:
- `GET ca-api.cafe24data.com/products/hits` (상품별 노출수)
- `GET ca-api.cafe24data.com/carts/action` (장바구니 담김수)
- `GET ca-api.cafe24data.com/products/sales` (상품별 판매건수)

세 endpoint 모두 `mall_id` 쿼리 자동 주입 (`Cafe24Client.get_ca`).

### 4단계: 집계 + 대시보드

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/build_ca_api_dashboard.py" \
  --raw-dir ./reports/cafe24/cloop/2026-04-01_to_2026-04-30/data/raw \
  --out-dir ./reports/cafe24/cloop/2026-04-01_to_2026-04-30 \
  --brand-label "클룹" \
  --period-start 2026-04-01 --period-end 2026-04-30
```

산출:
- `data/product_hits.csv`
- `data/cart_actions.csv`
- `data/product_sales.csv`
- `dashboard.html` (베타 배너 + 전환율 매트릭스 + 추이 라인)

dashboard 핵심 KPI:
- **전체 노출수 / 전체 담김수 / 전체 판매건수**
- **장바구니 전환율 = 담김수 / 노출수**
- **결제 전환율 = 판매건수 / 노출수**

dashboard 표:
- 상품번호 × {노출수, 담김수, 판매건수, 장바구니 전환율, 결제 전환율}
- 노출수 내림차순 정렬 (기본)

dashboard 차트:
- 시계열 전환율 추이 라인 (개선 전후 비교에 직접 사용)

### 5단계: 결과 안내

```
✅ CA API 적재 완료 (베타)

📊 대시보드: ./reports/cafe24/<brand>/<period>/dashboard.html
📁 CSV 3종: data/
🔍 슬랙 시나리오: 같은 명령으로 다른 period 다시 실행하여 전후 비교 가능
```

## CA API 인증 (egnis-mcp 토큰 재사용)

- CA API는 `cafe24api.com`과 별개 host(`ca-api.cafe24data.com`)이나 동일 access_token 사용.
- `cafe24_client.get_ca(mall_id, path, params)` 함수가 `mall_id` 자동 주입 + Bearer 헤더.
- 401/403 시 Admin API와 동일하게 exit 2 → skill 재발급 → 재실행.

## Graceful Degrade (M3 응답)

- CA API endpoint가 베타 변경으로 깨지면 (예: 404, 500):
  - 해당 endpoint는 empty CSV 생성
  - dashboard.html 상단에 명시적 배너: `"CA API <endpoint> unavailable (베타 endpoint 변경 가능성)"`
  - skill exit code 0 (graceful), stderr WARN 로그
- ADR-002 §6에 따라 본 릴리즈에서 **MCP fallback은 명시적으로 금지**.

## API 엔드포인트 참조

상세는 `docs/ca_api_reference.md` 참조.

| Endpoint | 출력 CSV |
|----------|----------|
| `GET ca-api.cafe24data.com/products/hits` | `product_hits.csv` |
| `GET ca-api.cafe24data.com/carts/action` | `cart_actions.csv` |
| `GET ca-api.cafe24data.com/products/sales` | `product_sales.csv` |

## 디렉토리 산출물

```
./reports/cafe24/<brand>/<period>/
├── dashboard.html
├── data/
│   ├── product_hits.csv
│   ├── cart_actions.csv
│   ├── product_sales.csv
│   └── raw/
└── api_reference.md
```
