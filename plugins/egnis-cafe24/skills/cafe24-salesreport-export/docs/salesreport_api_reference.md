# Cafe24 Salesreport API Reference (cafe24-salesreport-export 스킬)

**API version:** `2026-03-01`
**Host:** `https://<source_mall_id>.cafe24api.com`
**SCOPE:** `mall.read_salesreport`

⚠️ **Note:** 일부 endpoint는 "특정 클라이언트만 사용할 수 있는 API"로 표기되어 있어 호출 시 403이 날 수 있다. dry-run에서 401/403 발생 시 SKILL.md의 graceful degrade 안내.

---

## 1. Endpoints (5종)

| Endpoint | 본 스킬 사용 | rate limit |
|----------|------------|------------|
| `GET /api/v2/admin/financials/dailysales` | 일별 매출 (PG 정보 함께). dry-run 단일 대상. | 1 호출/sec |
| `GET /api/v2/admin/financials/monthlysales` | 월별 매출 | 1 호출/sec |
| `GET /api/v2/admin/reports/hourlysales` | 시간대별 매출 (페이지네이션) | 40 호출/sec |
| `GET /api/v2/admin/reports/productsales` | 상품별 판매 (페이지네이션) | 40 호출/sec |
| `GET /api/v2/admin/reports/salesvolume` | 판매수량 (product_no 필수 옵션) | 40 호출/sec |

---

## 2. `/api/v2/admin/financials/dailysales`

### 핵심 파라미터
| 파라미터 | 본 스킬 default | 비고 |
|----------|----------------|------|
| `start_date` | (required) | YYYY-MM-DD |
| `end_date` | (required) | YYYY-MM-DD |
| `payment_gateway_name` | (옵션) | PG 이름 필터 |
| `partner_id` | (옵션) | PG 발급 가맹점 ID |
| `payment_method` | (옵션) | card / tcash / icash / point / cell |

### 응답 핵심 필드
```jsonc
{
  "dailysales": [
    { "shop_no": 1, "date": "2026-05-01",
      "payment_amount": "150000.00", "refund_amount": "50000.00",
      "sales_count": 5 }
  ]
}
```

---

## 3. `/api/v2/admin/financials/monthlysales`

### 파라미터
| 파라미터 | 비고 |
|----------|------|
| `start_month` | YYYY-MM (required) |
| `end_month` | YYYY-MM (required) |
| `payment_gateway_name`, `partner_id`, `payment_method` | 옵션 |

### 응답
```jsonc
{
  "monthlysales": [
    { "shop_no": 1, "month": "2026-05",
      "payment_amount": "270000.00", "refund_amount": "20000.00",
      "sales_count": 8 }
  ]
}
```

---

## 4. `/api/v2/admin/reports/hourlysales`

### 파라미터
- `start_date` (required), `end_date` (required)
- `collection_hour` (옵션, 00~23)
- `limit` (max 1000, default 744)
- `offset` (max 10000)

### 응답
```jsonc
{
  "hourlysales": [
    { "shop_no": 1, "collection_date": "2026-05-01", "collection_hour": "12",
      "order_count": 6, "item_count": 7,
      "order_price_amount": "53000.00", "shipping_fee": "40.00",
      "order_sale_price": "5050.00", "coupon_discount_price": "1000.00",
      "actual_order_amount": "46990.00", "refund_amount": "0.00",
      "sales": "46990.00", "used_points": "100.00", ... }
  ],
  "links": [{ "rel": "next", "href": "..." }]
}
```

⚠️ `device_type` 파라미터는 **미지원** (검증 완료). PC/모바일 분리가 필요하면 CA API 사용.

---

## 5. `/api/v2/admin/reports/productsales`

### 파라미터
- `start_date`, `end_date` (required)
- `collection_hour` (옵션)
- `limit` (max 1000, default 100)

### 응답
```jsonc
{
  "productsales": [
    { "shop_no": 1, "collection_date": "2026-05-01", "collection_hour": "16",
      "product_no": 25, "variants_code": "P000ZNEM000A",
      "product_price": "10000.00", "settle_count": 1, "refund_count": 0,
      "sale_count": 1, "exchange_product_count": 0, "cancel_product_count": 0,
      "return_product_count": 0, "total_sale_count": 1, "total_cancel_count": 0 }
  ]
}
```

---

## 6. `/api/v2/admin/reports/salesvolume`

### 필수 파라미터 (둘 중 하나)
- `product_no` (상품번호)
- `variants_code` (품목코드)

### 옵션 파라미터
- `category_no`, `mobile` (T/F), `delivery_type` (A 국내 / B 해외)
- `group_no`, `supplier_id`

### 응답
```jsonc
{
  "salesvolume": [
    { "shop_no": "1", "collection_date": "2026-05-01", "collection_hour": "12",
      "product_price": "10000.00", "product_option_price": "0.00",
      "settle_count": "2", "exchane_product_count": "0",
      "cancel_product_count": "0", "return_product_count": "0",
      "updated_date": "2026-05-01T14:51+09:00",
      "product_no": 16, "variants_code": "P0000BKE000A",
      "total_sales": "2" }
  ]
}
```

---

## 7. Status Code 처리

| Code | 처리 |
|------|------|
| 200 | success |
| 401 / 403 | `Cafe24TokenExpired` → fetch_salesreport.py exit 2 → skill 재발급 |
| 422 | 파라미터 오류 (특히 `start_month`/`end_month` 형식 YYYY-MM 확인) |
| 429 | `Cafe24RateLimited` → 자동 backoff. **dailysales/monthlysales는 1/sec 매우 엄격** |

---

## 8. References

- 공식 문서: developers.cafe24.com (Sales report 섹션)
- Plan: `.omc/plans/cafe24-mcp-skills-v3.md` §7 Phase 4
- ADR-001/003: `.omc/plans/adr/`
- 공용 클라이언트: `scripts/lib/cafe24_client.py`
