---
name: cafe24-sales-dashboard
description: Cafe24 자사몰 매출 종합 분석 대시보드를 생성한다. egnis-mcp에서 cafe24_get_access_token으로 토큰을 발급받아 Python 스크립트가 cafe24api.com을 직접 호출하여 일자별 매출/구매자수/구매건수/구매개수와 전일 대비 증감을 집계하고, 단일 HTML 대시보드(reports/cafe24/<brand>/<period>/dashboard.html)와 CSV(sales_daily.csv)로 저장한다. 사용자가 "매출 대시보드", "매출 종합 분석", "/cafe24-sales-dashboard"를 호출하거나 --brand --period 옵션을 줄 때 동작한다.
---

# Cafe24 매출 종합 분석 대시보드

## 언제 쓰는가

사용자가 다음 중 하나를 요청하면 즉시 invoke:
- `/cafe24-sales-dashboard` (옵션 없거나 `--brand=<id|전체>` `--period=YYYY-MM-DD~YYYY-MM-DD`)
- "매출 대시보드 만들어줘", "매출 종합 분석", "주간 매출 리포트"
- 특정 브랜드의 매출 추이 시각화 요청

## 기본값

- `--brand` 미지정: **전체** (9개 몰 모두). brand id: `cloop, sprint, labnosh, braye, oneday1ball, groceryseoul, exerapy, drlabnosh, medileeds`
- `--period` 미지정: **최근 7일** (오늘 포함, 오늘 - 6일 ~ 오늘). 오늘 날짜는 system context의 `currentDate`를 사용.

## 아키텍처 (중요)

이 skill은 **MCP를 토큰 발급에만 사용**하고, 실제 데이터 호출은 **Python 스크립트가 cafe24api.com을 직접** 친다. 이유: 9몰 × 8일 × 2 endpoint = 144 호출을 모두 MCP 통해 받으면 토큰 한도(131K chars)를 초과하고 컨텍스트를 폭발시킴. 직접 호출하면 ~5초 안에 완료.

```
1. Claude (skill) → cafe24_get_access_token(mall_id)         ← MCP 1회/몰 = 1~9회
2. Python (fetch_sales.py) → cafe24api.com 직접 GET           ← 토큰으로 직접
3. Python (build_sales_dashboard.py) → CSV + HTML 생성
```

## 워크플로우 (이대로 따른다)

### 1단계: 인자 파싱

`--brand=...` `--period=YYYY-MM-DD~YYYY-MM-DD` 형태 파싱.
- brand가 alias(예: 랩노쉬)면 mall_id로 변환 (`drlabnosh`).
- brand=전체/all/생략 시 9개 mall_id 모두.
- period 미지정 시 currentDate 기준 최근 7일.
- **전일 비교를 위해** period_start - 1일을 fetch 범위에 포함시킨다.

### 2단계: 토큰 발급 (MCP)

대상 mall_id 각각에 대해:

```
mcp__claude_ai_egnis-mcp__cafe24_get_access_token(mall_id=<mall_id>)
```

응답에서 `access_token`, `source_mall_id`, `shop_no`, `api_host`를 사용. 모든 몰의 토큰을 한 메시지에 parallel 호출.

**중요**: 토큰은 짧은 수명. 401/403 발생 시 이 tool 재호출. Cafe24 OAuth는 매시 :30 KST에 자동 리프레시 (cafe24-token-refresh cron).

### 3단계: 데이터 수집 (Python)

`scripts/fetch_sales.py` 실행. 토큰을 환경변수로 전달:

```bash
CAFE24_TOKENS_JSON='{"cloop": {"access_token":"...", "api_host":"https://cloop.cafe24api.com", "source_mall_id":"cloop", "shop_no":null}, ...}' \
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/fetch_sales.py" \
  --out-dir ./reports/cafe24/<brand_or_all>/<period>/data/raw \
  --period-start YYYY-MM-DD \
  --period-end YYYY-MM-DD \
  --include-prev-day
```

`${CLAUDE_PLUGIN_ROOT}`는 Claude Code가 plugin 실행 시 자동으로 set하는 env (plugin 설치 경로). skill 호출 시 이 변수를 사용해 스크립트 경로 구성.

스크립트는:
- 각 몰 × 각 일자에 대해 `GET /api/v2/admin/reports/hourlysales?start_date=D&end_date=D&fields=collection_date,order_count,item_count,sales` 호출
- 각 몰 × 각 일자에 대해 `GET https://ca-api.cafe24data.com/members/sales?mall_id=X&start_date=D&end_date=D` 호출
- 응답을 raw 디렉토리에 mall별 1개 파일로 저장 (`hourlysales_<mall>.json`, `members_sales_<mall>_<date>.json`)
- Token Bucket rate limit 준수: 동일 URL 초당 최대 ~10회 (병렬 9몰까지 안전)

### 4단계: 집계 + 대시보드 생성

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/build_sales_dashboard.py" \
  --raw-dir ./reports/cafe24/<brand_or_all>/<period>/data/raw \
  --out-dir ./reports/cafe24/<brand_or_all>/<period> \
  --brand-label "<표시용 라벨>" \
  --period-start YYYY-MM-DD \
  --period-end YYYY-MM-DD
```

스크립트는:
- raw JSON 읽어 일자별 집계
- 전일 비교값/증감 계산
- `data/sales_daily.csv` 저장
- 다일·다몰일 경우 `data/sales_by_mall.csv` 추가
- `dashboard.html` 생성 (브랜드 셀렉터, 추이 차트, 일자별 표, 몰별 표 포함)

### 5단계: 결과 안내

```
✅ 매출 대시보드 생성 완료

📊 대시보드: ./reports/cafe24/<brand>/<period>/dashboard.html
📁 CSV: ./reports/cafe24/<brand>/<period>/data/sales_daily.csv

요약 (기간 합계):
- 구매자수: X명
- 구매건수: Y건  
- 구매개수: Z개
- 매출액: ₩W
```

가능하면 `open <path>` 명령으로 브라우저 띄움.

## 토큰 갱신 정책 (반드시 준수)

- Python이 cafe24api 호출 중 **401 또는 403** 응답 수신 시:
  1. Python 스크립트는 즉시 해당 몰의 진행을 멈추고 stderr에 어떤 몰이 실패했는지 표시한 뒤 exit code 2로 종료
  2. skill은 `cafe24_get_access_token(mall_id=<실패한 몰>)`을 다시 호출해 신규 토큰 받음
  3. `CAFE24_TOKENS_JSON` 업데이트 후 fetch_sales.py 재실행 (이미 수집된 raw JSON은 재사용)
- Cafe24 OAuth는 외부 cron으로 매시 :30 KST에 리프레시되므로 신규 발급은 항상 유효한 토큰을 반환.

## 페이지네이션 규칙

- `reports/hourlysales`: 1일치 응답 24행, 1페이지로 충분. 만약 `paging.has_next == true`이면 `paging.continuation.query`로 재호출.
- `members/sales`: 1일치 1행 (회원/비회원 합산), 페이지네이션 불필요.
- 모든 경우 응답에 `paging` 존재하면 `has_next == false`까지 반복.

## 디렉토리 산출물

```
./reports/cafe24/<brand_or_all>/<YYYY-MM-DD_to_YYYY-MM-DD>/
├── dashboard.html                     ← 메인 결과물 (브랜드 필터 포함)
├── data/
│   ├── sales_daily.csv                ← 일자별 집계
│   ├── sales_by_mall.csv              ← 몰별 일자별 집계 (다몰일 때만)
│   └── raw/                           ← cafe24api 원본 JSON
```

## 브랜드 alias 매핑

| 입력 | mall_id |
|---|---|
| 클룹, cloop | cloop |
| 스프린트, sprint, 에반게리온 | sprint |
| 한끼통살, labnosh, 에잇템 | labnosh |
| 브레이, braye | braye |
| 오원, oneday1ball, o1 | oneday1ball |
| 그로서리서울, groceryseoul | groceryseoul |
| 엑쎄라피, exerapy | exerapy |
| 랩노쉬, drlabnosh | drlabnosh |
| 메디리즈, medileeds | medileeds |
| 전체, all | (9개 모두) |

## API 엔드포인트 참조

| 데이터 | 엔드포인트 | host |
|---|---|---|
| 일자별 매출/주문건수/구매개수 | `GET /api/v2/admin/reports/hourlysales?start_date=D&end_date=D` | `<api_host>` (`source_mall_id` 기준) |
| 회원/비회원 구매자수 | `GET https://ca-api.cafe24data.com/members/sales?mall_id=X&start_date=D&end_date=D` | analytics 전용 host |

필수 헤더:
- `Authorization: Bearer <access_token>`
- `X-Cafe24-Api-Version: 2026-03-01`
- (sub-mall의 경우) Admin API host는 `source_mall_id` 기반 (`https://cloop.cafe24api.com`), shop_no가 있으면 쿼리에 `shop_no=<n>` 추가.
