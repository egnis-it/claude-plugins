# Cafe24 Orders API Reference (cafe24-orders-export 스킬)

**API version:** `2026-03-01` (X-Cafe24-Api-Version 헤더)
**Auth:** `Authorization: Bearer <access_token>` (egnis-mcp의 `cafe24_get_access_token` 발급)
**Host:** `https://<source_mall_id>.cafe24api.com`
**SCOPE:** `mall.read_order` (필수)

---

## 1. Endpoints (본 스킬 사용)

| Endpoint | Method | 호출 조건 | rate limit |
|----------|--------|----------|------------|
| `/api/v2/admin/orders/count` | GET | dry-run 1회 + 본 실행 사전 검증 | 40 호출/sec/mall |
| `/api/v2/admin/orders` | GET | 본 실행 (페이지네이션 자동) | 40 호출/sec/mall |
| `/api/v2/admin/cancellation/{claim_code}` | GET | `--include-cancels` 옵션 시 | 40 호출/sec/mall |
| `/api/v2/admin/exchange/{claim_code}` | GET | `--include-exchanges` 옵션 시 | 40 호출/sec/mall |

---

## 2. `/api/v2/admin/orders` 상세

### 2.1 핵심 파라미터

| 파라미터 | 본 스킬 default | 비고 |
|----------|----------------|------|
| `start_date` | (required) | YYYY-MM-DD |
| `end_date` | (required) | YYYY-MM-DD. start와 함께 필수, 3개월 초과 시 자동 분할 (cafe24_client.split_date_range) |
| `date_type` | `order_date` | order_date / pay_date / shipbegin_date / cancel_date 등 |
| `limit` | `1000` | 최대 1000 |
| `offset` | (자동) | 페이지네이션 (links.next로 자동 순회) |
| `embed` | `items,buyer,receivers` | + `cancellation` (--include-cancels), `exchange` (--include-exchanges) |
| `shop_no` | (자동) | tokens_json의 shop_no 필드가 있으면 자동 주입 |

### 2.2 추가 검색 파라미터 (옵션)

| 파라미터 | 값 | 용도 |
|----------|------|------|
| `order_status` | N00/N10/N20/.../C40/R40/E40 | 주문 상태 필터 (콤마 구분 가능) |
| `member_type` | 2 (회원) / 3 (비회원) | 회원/비회원 분리 |
| `payment_method` | cash/card/cell/tcash/icash/... | 결제수단 필터 |
| `order_place_id` | cafe24/mobile/NCHECKOUT/coupang/... | 주문경로 필터 |
| `first_order` | T / F | 최초 주문 여부 |

### 2.3 핵심 응답 필드

```jsonc
{
  "orders": [
    {
      "shop_no": 1,
      "order_id": "20260511-0000001",
      "currency": "KRW",
      "member_id": "buyer123",
      "member_email": "buyer@example.com",
      "billing_name": "홍길동",
      "payment_method": ["card"],
      "paid": "T",          // T=결제 F=미결제 M=부분결제
      "canceled": "F",      // T=취소 F=미취소 M=부분취소
      "order_date": "2026-05-11T11:21:35+09:00",
      "payment_date": "2026-05-11T11:25:00+09:00",
      "cancel_date": null,
      "initial_order_amount": { "payment_amount": "30000.00", ... },
      "actual_order_amount":  { "payment_amount": "30000.00", ... },  // 본 스킬이 매출 계산에 사용
      "shipping_status": "T",
      "order_place_id": "cafe24",
      "order_place_name": "Cafe24",
      "items": [...],     // embed=items
      "buyer": {...},     // embed=buyer
      "receivers": [...], // embed=receivers
      "cancellation": {...}, // embed=cancellation (옵션)
      "exchange": {...},     // embed=exchange (옵션)
      "first_order": "T",
      "subscription": "F",
      "tax_detail": [...]
    }
  ],
  "links": [
    { "rel": "next", "href": "https://<mall>.cafe24api.com/api/v2/admin/orders?limit=1000&offset=1000" }
  ]
}
```

### 2.4 본 스킬의 매출 산출 로직

```python
# build_orders_dashboard._order_amount(order)
actual = order.get("actual_order_amount") or {}
amount = actual.get("payment_amount") or order.get("payment_amount") or 0
# 취소(canceled == "T")는 매출 합산에서 제외
```

---

## 3. `/api/v2/admin/orders/count`

### 3.1 사용 시점
- **dry-run**: 토큰 + scope 권한 검증용 (1회 호출). 응답 200이면 PASS, 401/403이면 토큰 갱신 필요
- **본 실행 직전**: 페이지네이션 안전 한도 사전 산정

### 3.2 응답
```jsonc
{ "count": 1234 }
```

---

## 4. `/api/v2/admin/cancellation/{claim_code}` (옵션, `--include-cancels`)

### 4.1 호출 조건
주문 응답의 `cancellation.claim_code`를 추출 후 해당 코드만 추가 호출. 본 스킬에선 기본 off (호출량 증가).

### 4.2 핵심 응답 필드
- `claim_reason_type`: A(고객변심)/B(배송지연)/E(상품불만족)/K(상품불량)/H(품절)/I(기타)
- `refund_methods`, `refund_amounts`
- `partner_discount_amount`, `coupon_discount_amount`
- `status`: canceled / canceling

---

## 5. `/api/v2/admin/exchange/{claim_code}` (옵션, `--include-exchanges`)

기본 off. 호출 시 `embed=exchange`로 받는 핵심 필드:
- `reason_type`, `reason`
- `exchange_items[]`
- `tracking_no`, `shipping_company_name`

---

## 6. Status Code 처리

| Code | 처리 |
|------|------|
| 200 | success |
| 401 / 403 | `Cafe24TokenExpired` → fetch_orders.py exit 2 → skill이 토큰 갱신 후 재실행 |
| 429 | `Cafe24RateLimited` → cafe24_client가 자동 backoff (200ms→500ms→1s) + 재시도 3회 |
| 422 | 파라미터 오류. start_date/end_date 형식 확인 |
| 500/503/504 | 일시적 — backoff 후 재시도 |

---

## 7. Rate Limit 정책 (Cafe24 leaky bucket)

| Header | 의미 | 본 클라이언트 대응 |
|--------|------|--------------------|
| `X-Api-Call-Limit` | `1/40` 형식. 현재/최대 | 로깅 |
| `X-Cafe24-Call-Usage` | 사용률 (%) | ≥80% 시 USAGE_BACKOFF_DELAY(300ms) 사전 대기 |
| `X-Cafe24-Call-Remain` | 호출 재개까지 남은 초 | 429 시 backoff 대기 시간 |
| `X-Cafe24-Time-Usage` | 처리 시간 사용률 | 로깅 |
| `X-Cafe24-Time-Remain` | 처리 시간 재개 초 | 로깅 |

---

## 8. 페이지네이션 처리

응답의 `links[].rel == "next"` 자동 추적 (cafe24_client.get_all_pages).
안전 한도: `max_pages=20` (chunk당 20 × 1000 = 20K orders).
3개월 초과 기간은 자동 분할 (cafe24_client.split_date_range).

---

## 9. 본 스킬의 호출 시퀀스 (요약)

```
1. mcp__claude_ai_egnis-mcp__cafe24_get_access_token(mall_id) for each mall (parallel)
2. CAFE24_TOKENS_JSON 환경변수에 토큰 dict 주입
3. python3 scripts/fetch_orders.py --brand=<alias> --period-start=... --period-end=...
   - GET /admin/orders/count   (dry-run이면 여기서 종료)
   - GET /admin/orders          (페이지네이션 자동, 3개월 분할 자동)
   - 401/403 → exit 2 → skill이 토큰 갱신 → fetch 재실행
4. python3 scripts/build_orders_dashboard.py --raw-dir=... --out-dir=...
   - aggregate_per_day → orders_summary.csv
   - render_dashboard(layout="timeseries") → dashboard.html
5. open <dashboard.html>
```

---

## 10. References

- Cafe24 Admin API 공식 문서: developers.cafe24.com (Orders 섹션)
- 본 플러그인 plan: `.omc/plans/cafe24-mcp-skills-v3.md` §7 Phase 3
- ADR-001: `.omc/plans/adr/ADR-001-fetch-sales-no-edit.md`
- ADR-003: `.omc/plans/adr/ADR-003-template-strategy.md` (Template Strategy)
- 공용 클라이언트: `scripts/lib/cafe24_client.py` (Cafe24Client)
- 공용 렌더러: `scripts/lib/dashboard_template.py` (render_dashboard)
- brand alias: `scripts/lib/brand_registry.py` (BRAND_ALIASES, 9몰)
