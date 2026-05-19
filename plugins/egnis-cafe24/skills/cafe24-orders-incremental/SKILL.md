---
name: cafe24-orders-incremental
description: Cafe24 9개 자사몰의 주문 데이터를 증분(incremental)으로 로컬에 적재한다. MCP cafe24_get 으로 페이지별 응답을 수집하여 CSV(UTF-8 BOM) + xlsx에 누적 append. 최초 실행 시 2026-05-01 ~ 오늘 전체 적재, 이후엔 마지막 적재일+1일 ~ 오늘만 fetch. 사용자가 "주문 적재 계속", "증분 적재", "매일 주문 자동 적재", "orders incremental", "cafe24-orders-incremental"을 호출하거나 "주기적으로 주문 데이터 쌓고 싶어"라고 말하면 동작한다. PII(개인정보) 적재 금지 정책 엄격 준수.
---

# Cafe24 주문 데이터 증분 적재 (v2 — MCP 직접 호출 기반)

비개발자 대상. 한 번 실행하면 2026-05-01 부터 오늘까지 모든 브랜드 주문이 로컬에 엑셀로 쌓이고, 이후 같은 명령을 다시 실행하면 마지막 적재일 이후 신규 주문만 추가됩니다.

## 언제 쓰는가

- "주문 데이터를 로컬에 계속 쌓고 싶어"
- "매일 자동으로 주문 데이터 적재"
- "2026-05-01부터 현재까지 누적 적재"
- 키워드: "주문 증분", "orders incremental", "주기적 적재"

## ⚠️ PII 적재 금지 정책 (필독)

본 스킬은 회원 개인정보(PII)를 **절대 적재하지 않는다**.

- ❌ 절대 적재 금지: buyer_name (주문자명), buyer_email, buyer_cellphone, billing_*, receiver_*, member_id/email, address1/2, zipcode
- ✅ 적재 대상: 몰ID/브랜드/주문번호/주문일시/결제일시/주문상태/결제수단/상품수량/결제금액/실결제금액/배송비/통화/취소여부/환불여부 (14개 컬럼만)

매출/주문 분석에는 익명화된 `order_id`만으로 충분. cafe24 API 호출 시 `embed=items`만 사용하고 buyer/receivers는 요청하지 않으며, 응답에 포함되더라도 build_orders_xlsx.py 가 raw json 저장 전 `_sanitize_pages()`로 일괄 제거. **신규 컬럼 추가 시에도 PII는 절대 금지.**

## 아키텍처 (v2, 2026-05-19 변경)

```
[Claude (이 SKILL.md)]
  ├ 0단계: 경로 컨펌 (AskUserQuestion)
  ├ 1단계: 9 mall × 페이지네이션 ─ mcp__claude_ai_egnis-mcp__cafe24_get 반복 호출
  │   ├ for mall in [cloop, sprint, labnosh, braye, oneday1ball,
  │   │              groceryseoul, exerapy, drlabnosh, medileeds]:
  │   │   ├ start_date = state.last_fetched[mall] + 1d 또는 2026-05-01
  │   │   ├ end_date = today
  │   │   ├ offset = 0
  │   │   ├ while True:
  │   │   │   ├ resp = cafe24_get(mall_id, "orders", query={start,end,limit=500,offset, embed=items}, max_pages=5)
  │   │   │   ├ pages.append(resp)
  │   │   │   ├ if not resp.paging.has_next: break
  │   │   │   └ offset = resp.paging.continuation.query.offset (또는 직접 +500)
  │   │   ├ pages 를 임시파일 /tmp/cafe24_pages_<mall>.json 에 저장 (JSON array)
  │   │   └ build_orders_xlsx.py --mall <mall> --input /tmp/cafe24_pages_<mall>.json \\
  │   │                          --since <start> --until <end> --base-dir <path> 실행
  │   └ 임시파일 정리 (rm -f /tmp/cafe24_pages_<mall>.json)
  └ 2단계: 결과 요약
```

**Claude 가 cafe24_get 을 직접 호출하는 이유:** Python 이 cafe24api 를 외부에서 직접 호출하면 MCP token broker 의 `:30 KST` 회전 윈도우에서 stale token 으로 401 발생. MCP 내부에서는 token swap 이 자동 처리되므로 cafe24_get 경유는 항상 안정.

## 기본값

| 옵션 | 기본값 | 설명 |
|---|---|---|
| brand | `all` | 9개 몰 전부 |
| since | `2026-05-01` | 최초 실행 시작일 (state 있으면 무시) |
| until | 오늘 | fetch 종료일 |
| base-dir | **사용자 컨펌** | state/data/logs 저장 위치 (0단계 참조) |

## 0단계: 저장 경로 컨펌 (필수, 최초 실행 시)

스킬은 사용자에게 묻지 않고 임의 경로에 파일을 만들지 **않는다**.

1. **사용자가 명시 지정** ("Documents 폴더에", "--base-dir=..."): 그 경로 사용
2. **이전 실행 기록**: `~/.config/cafe24-orders-incremental/config.json` (macOS/Linux) 또는 `%APPDATA%\cafe24-orders-incremental\config.json` (Windows) 의 `last_base_dir` 재사용
3. **최초 실행**: AskUserQuestion 으로 OS-aware 후보 제시
   - macOS: `~/Documents/cafe24-orders/`, `~/Desktop/cafe24-orders/`, 현재 cwd, 직접 입력
   - Windows: `%USERPROFILE%\Documents\cafe24-orders\`, ...
   - Linux: `~/cafe24-orders/`, ...

선택된 경로를 `~`/`%USERPROFILE%` 전개 후 절대경로 변환. build_orders_xlsx.py 가 자동으로 권한 검증.

## 1단계: 9 mall × 페이지네이션 (Claude 가 직접 수행)

각 mall 에 대해 다음 절차:

### 1.1 시작일 결정

`<base-dir>/state/last_fetched.json` 을 Read tool 로 읽고:
- `last_fetched[mall_id]` 존재: `start_date = last_fetched + 1일`
- 없거나 파일 부재: `start_date = 2026-05-01` (또는 사용자 지정 since)

`end_date = today` (system context currentDate 사용).

`start_date > end_date` 이면 해당 mall skip.

### 1.2 페이지 수집

```
pages = []
offset = 0
max_iterations = 1000  # safety cap (mall당 ~500K orders/run)

for i in range(max_iterations):
    resp = mcp__claude_ai_egnis-mcp__cafe24_get(
        mall_id=<mall>,
        path="orders",
        query={
            "start_date": <start>,
            "end_date": <end>,
            "date_type": "order_date",
            "limit": 500,
            "offset": offset,
            "embed": "items"   # PII 회피: buyer/receivers 제외
        },
        max_pages=5   # MCP 내부 auto-pagination 최대 5페이지 (= 2500건)
    )
    pages.append(resp)

    if not resp.get("paging", {}).get("has_next"):
        break

    # paging.continuation.query.offset 또는 직접 +500*max_pages
    next_offset = resp.get("paging", {}).get("continuation", {}).get("query", {}).get("offset")
    if next_offset is None:
        offset += 500 * 5  # max_pages=5, limit=500
    else:
        offset = int(next_offset)
```

### 1.3 파일 저장 + Python 호출

수집된 pages 배열을 `/tmp/cafe24_pages_<mall>.json` 에 JSON 으로 저장 (Write tool).

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/build_orders_xlsx.py" \
  --mall <mall> \
  --input /tmp/cafe24_pages_<mall>.json \
  --since <start> \
  --until <end> \
  --base-dir <path>
```

stdout 의 한 줄 JSON ({"ok": true, "new_rows": N, ...}) 을 파싱해 결과 누적.

### 1.4 임시파일 정리

`rm /tmp/cafe24_pages_<mall>.json`

## 2단계: 결과 요약

9 mall 모두 처리 후:

```
✅ 증분 적재 완료
신규 주문: <total>건 (전체 9개 몰 합계)
📊 엑셀: <base-dir>/data/orders_all.xlsx
📁 CSV : <base-dir>/data/orders_all.csv
📅 last_fetched 업데이트: cloop=2026-05-19, ...
```

가능하면 `open <xlsx>` 또는 OS별 동등 명령 실행.

## 산출물

```
<base-dir>/
├── state/
│   └── last_fetched.json          ← brand별 마지막 fetch 날짜 + 실행 로그
├── data/
│   ├── orders_all.csv              ← 전체 누적 CSV (UTF-8 BOM, Excel 더블클릭 가능)
│   ├── orders_all.xlsx             ← 몰별 시트로 분리된 xlsx
│   └── raw/<mall>/<period>.json    ← PII sanitize 된 원본 백업
└── logs/run_<timestamp>_<mall>.log
```

**엑셀 구조**: 시트 9개 (한 mall 당 하나), 헤더 한국어 14컬럼, frozen row 1.

## 호출하는 MCP 도구 / Cafe24 API

| 도구 | 용도 |
|---|---|
| `mcp__claude_ai_egnis-mcp__cafe24_get` | path=`orders`, paging 자동. PII 제외 (`embed=items`) |
| `mcp__claude_ai_egnis-mcp__list_cafe24_malls` | (선택) 9 mall 등록 확인 |

cafe24api 직접 호출 안 함. **`cafe24_get_access_token` 도구도 사용하지 않음** (v2 변경점).

## 트러블슈팅

| 증상 | 조치 |
|---|---|
| `cafe24_get` 응답에 `paging.has_next` 없음 | resp.paging 확인. 최신 응답에서는 항상 포함되어야 함. has_next=false 면 정상 종료 |
| 동일 일자 재실행 시 데이터 중복 | n/a — `(몰ID, 주문번호)` 기준 자동 dedup |
| xlsx 작성 실패 (CSV는 정상) | openpyxl auto-install 실패. `python3 -m pip install --user openpyxl` 수동 실행 |
| state 파일 손상 | `<base-dir>/state/last_fetched.json` 삭제 후 재실행 (전체 재적재) |

## 주기적 자동 실행 (옵션)

```
/schedule "cafe24-orders-incremental 스킬로 전체 브랜드 증분 적재" "매일 오전 9시"
```

state 기반이라 컴퓨터가 꺼져 있었어도 다음 실행 시 누락분 자동 적재.

## 의존성

- **openpyxl**: build_orders_xlsx.py 가 처음 호출 시 자동 `pip install --user` 시도
- 설치 실패해도 CSV는 정상 생성됨 (xlsx만 skip, build_orders_xlsx.py 가 WARN 로그)

## v1 (fetch_orders_incremental.py) 와의 차이

| 항목 | v1 (deprecated) | v2 (현재) |
|---|---|---|
| 데이터 수집 | Python urllib → cafe24api 직접 | Claude → mcp__cafe24_get |
| 토큰 | `cafe24_get_access_token` 발급 | 사용 안 함 (MCP 내부 처리) |
| :30 KST 윈도우 안정성 | ❌ stale 401 | ✅ 항상 안정 |
| max_pages | 500 | per-call 5, 외부 루프 무제한 |

v1 스크립트 (`fetch_orders_incremental.py`) 는 retain 하지만 본 스킬에서는 호출 안 함.
