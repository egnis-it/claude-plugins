---
name: cafe24-orders-incremental
description: Cafe24 9개 자사몰의 주문 데이터를 증분(incremental)으로 로컬에 적재한다. last_fetched.json 상태 파일을 사용해 매 실행마다 마지막 적재일+1일~오늘 범위만 fetch하여 CSV(UTF-8 BOM) + xlsx에 누적 append한다. 최초 실행 시 2026-05-01 ~ 오늘 전체를 한 번에 적재한다. 사용자가 "주문 적재 계속", "증분 적재", "매일 주문 자동 적재", "orders incremental", "cafe24-orders-incremental"을 호출하거나, "주기적으로 주문 데이터 쌓고 싶어"라고 말하면 동작한다. /schedule 로 매일 자동 실행도 가능.
---

# Cafe24 주문 데이터 증분 적재 (incremental)

비개발자 대상 — 한 번 실행하면 2026-05-01부터 오늘까지 모든 브랜드 주문이 로컬에 엑셀로 쌓이고, 이후 같은 명령을 다시 실행하면 마지막 적재일 이후 신규 주문만 추가됩니다.

## 언제 쓰는가

- "주문 데이터를 로컬에 계속 쌓고 싶어"
- "매일 자동으로 주문 데이터 적재"
- "2026-05-01부터 현재까지 누적 적재"
- 슬래시 형식: `/cafe24-orders-incremental`
- 키워드: "주문 증분", "orders incremental", "주기적 적재"

## 기본값

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--brand` | `all` | 9개 몰 전부. alias 가능: 클룹, 랩노쉬 등 |
| `--since` | `2026-05-01` | **최초 실행** 시작일. state 있으면 무시됨 |
| `--until` | 오늘 | fetch 종료일 |
| `--base-dir` | **사용자 컨펌 후 결정** | state/data/logs 저장 위치 (아래 0단계 참조) |

## 0단계: 저장 경로 컨펌 (필수, 최초 실행 시)

스킬은 사용자에게 묻지 않고 임의 경로에 파일을 만들지 **않는다**. 다음 순서로 진행:

### (a) 사용자가 명시 지정한 경우
호출 메시지에 경로 키워드(예: "Documents 폴더에", "바탕화면에", "--base-dir=...")가 있으면 그 경로를 사용. 절대경로 변환 후 진행.

### (b) 이전 실행 기록이 있는 경우
`~/.config/cafe24-orders-incremental/config.json` (Linux/macOS) 또는
`%APPDATA%\cafe24-orders-incremental\config.json` (Windows) 에서 마지막 사용 경로를 읽음.

**확인 메시지** (간단한 yes/no):
```
이전에 적재한 경로가 있습니다:
  /Users/<user>/Documents/cafe24-orders

[1] 같은 경로에 계속 쌓기 (권장)
[2] 다른 경로 새로 지정
```

### (c) 최초 실행이거나 사용자가 새 경로 원함
다음 OS-aware 후보를 AskUserQuestion 으로 제시:

- **macOS**: `~/Documents/cafe24-orders/`, `~/Desktop/cafe24-orders/`, 현재 cwd, 직접 입력
- **Windows**: `%USERPROFILE%\Documents\cafe24-orders\`, `%USERPROFILE%\Desktop\cafe24-orders\`, 현재 cwd, 직접 입력
- **Linux**: `~/cafe24-orders/`, `~/Documents/cafe24-orders/`, 현재 cwd, 직접 입력

선택된 경로를 `~`/`%USERPROFILE%` 전개 후 절대경로 변환. 권한 검증 (`os.access(parent, os.W_OK)`) 후 진행.

### (d) 컨펌된 경로 캐시
스크립트가 종료 시 `config.json`에 `{"last_base_dir": "<absolute_path>", "history": [...]}` 저장. 다음 실행 시 (b) 단계에서 재사용.

## 산출물

```
<컨펌된 base-dir>/
├── state/
│   └── last_fetched.json          ← brand별 마지막 fetch 날짜 + 실행 로그
├── data/
│   ├── orders_all.csv              ← 전체 누적 CSV (UTF-8 BOM, Excel 더블클릭 가능)
│   ├── orders_all.xlsx             ← 몰별 시트로 분리된 xlsx (누적)
│   └── raw/<mall>/<period>.json    ← idempotent 원본 백업 (재실행 시 재사용)
└── logs/run_<timestamp>.log
```

**엑셀(.xlsx) 구조**: 시트 9개 — `클룹(cloop)`, `스프린트(sprint)`, ..., 헤더는 한국어. 각 시트는 frozen row 1.

**컬럼**: 몰ID / 브랜드 / 주문번호 / 주문일시 / 결제일시 / 주문상태 / 결제수단 / 상품수량 / 결제금액 / 실결제금액 / 배송비 / 통화 / 취소여부 / 환불여부

## ⚠️ PII 적재 금지 정책

본 스킬은 **회원 개인정보(PII)를 절대 적재하지 않습니다.**

- ❌ buyer_name (주문자명)
- ❌ buyer_email (이메일)
- ❌ buyer_cellphone (휴대폰)
- ❌ billing_*, receiver_*, member_id, address1/2, zipcode

매출/주문 집계에는 익명화된 `order_id`만 있으면 충분합니다. cafe24 API 호출 시 `embed=items`만 사용하고 buyer/receivers는 요청하지 않으며, 응답에 포함된 PII 키도 raw json 저장 전 `_sanitize_pages()`로 일괄 제거합니다. 향후 컬럼 추가 시에도 PII는 절대 추가 금지.

## 워크플로우

### 1단계: 토큰 발급 (0단계 경로 컨펌 완료 후)

대상 mall_id 9개에 대해 parallel 호출:

```
mcp__claude_ai_egnis-mcp__cafe24_get_access_token(mall_id=<mall_id>)
```

결과를 `CAFE24_TOKENS_JSON` 환경변수에 mall_id → {access_token, api_host, source_mall_id, shop_no} 형식으로 직렬화.

### 2단계: 증분 적재 실행

```bash
CAFE24_TOKENS_JSON='{"cloop":{...}, "sprint":{...}, ...}' \
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/fetch_orders_incremental.py" \
  --brand all \
  --since 2026-05-01 \
  --base-dir ./reports/cafe24/all/orders_incremental
```

스크립트가 수행하는 일:

1. `state/last_fetched.json` 읽기
2. 브랜드별로 시작일 결정:
   - state가 있으면: `last_fetched + 1일`
   - state가 없으면: `--since` (기본 2026-05-01)
3. `GET /api/v2/admin/orders` 페이지네이션 자동 (limit=1000)
4. 일자 범위 3개월 초과 시 자동 분할
5. 응답을 flat dict로 정규화 → CSV append + xlsx 시트 append
6. dedup: 기존 CSV에 동일 `(몰ID, 주문번호)` 있으면 skip (idempotent)
7. state 업데이트 후 종료

### 3단계: 401/403 자동 회복

- `Cafe24TokenExpired` → exit 2
- skill이 해당 mall_id에 대해 토큰 재발급 → 재실행
- 이미 처리된 brand의 raw JSON은 `raw/<mall>/` 캐시로 재사용 (이중 호출 방지)

### 4단계: 결과 안내

```
✅ 증분 적재 완료
신규 주문: 287건 (전체 9개 몰 합계)
📊 엑셀: ./reports/cafe24/all/orders_incremental/data/orders_all.xlsx
📁 CSV : ./reports/cafe24/all/orders_incremental/data/orders_all.csv
📅 last_fetched 업데이트: cloop=2026-05-19, sprint=2026-05-19, ...
```

## 주기적 자동 실행 (옵션)

매일 자동 실행하려면 Claude Code의 `/schedule` 명령 사용:

```
/schedule "cafe24-orders-incremental 스킬로 전체 브랜드 증분 적재" "매일 오전 9시"
```

또는 명시적 cron 표현식:

```
/schedule "cafe24-orders-incremental 실행" "0 9 * * *"
```

스케줄러는 Claude Code가 실행 중일 때만 동작합니다. 컴퓨터가 꺼져 있으면 다음 실행 시 누락분이 한 번에 적재됩니다 (last_fetched 기반이므로 안전).

## 의존성

- **openpyxl** (xlsx 출력용)
  - 자동 설치: 스크립트가 처음 실행 시 `pip install --user openpyxl` 자동 시도
  - 수동 설치 (자동 실패 시): `python3 -m pip install --user openpyxl`
  - 설치 실패해도 CSV는 정상 생성됨 (xlsx만 skip)

## 트러블슈팅

| 증상 | 원인 | 조치 |
|---|---|---|
| `AUTH FAIL: refresh tokens for [...]` | 일부 mall 토큰 만료 | 스킬이 자동 재발급 후 재시도 (이미 처리된 브랜드는 raw 캐시 재사용) |
| `RATE LIMITED` | X-Cafe24-Call-Usage 80%↑ | 자동 backoff. 그래도 실패 시 30분 후 재실행 |
| `xlsx 작성 실패 (CSV는 정상)` | openpyxl auto-install 실패 | `python3 -m pip install --user openpyxl` 수동 실행 |
| 동일 일자 재실행 시 데이터 중복 | n/a — dedup으로 자동 처리 | `(몰ID, 주문번호)` 기준 자동 dedup |
| state 파일 손상 | 디스크 이슈 | `state/last_fetched.json` 삭제 후 재실행 (전체 재적재) |

## state 초기화 (전체 재적재 필요 시)

```bash
rm -rf ./reports/cafe24/all/orders_incremental/state
rm -f ./reports/cafe24/all/orders_incremental/data/orders_all.csv
rm -f ./reports/cafe24/all/orders_incremental/data/orders_all.xlsx
```

이후 스킬 재실행하면 2026-05-01부터 전체 재적재.

## 호출하는 Cafe24 API

| Endpoint | 용도 |
|---|---|
| `GET /api/v2/admin/orders` | 주문 목록 (limit=1000, embed=items,buyer,receivers) |

3개월 초과 기간은 `Cafe24Client.split_date_range`로 자동 분할 호출.

## 다른 스킬과의 차이

| 스킬 | 용도 | 출력 | 누적 여부 |
|---|---|---|---|
| **cafe24-orders-incremental** | **주기적 누적 적재** | **CSV+xlsx (몰별 시트)** | **누적, dedup 자동** |
| cafe24-orders-export | 1회 기간 지정 추출 + 대시보드 | CSV + dashboard.html | 기간별 새 디렉토리 |
| cafe24-sales-dashboard | 매출 시각화 (hourlysales + members/sales) | dashboard.html | 기간별 새 디렉토리 |
