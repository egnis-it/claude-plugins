# Cafe24 Products API Reference (cafe24-products-export 스킬, Tier 2)

**API version:** `2026-03-01`
**Host:** `https://<source_mall_id>.cafe24api.com`
**SCOPE:** `mall.read_product`

---

## 1. Endpoints

| Endpoint | 본 스킬 사용 |
|----------|--------------|
| `GET /api/v2/admin/products/count` | dry-run 1회 |
| `GET /api/v2/admin/products` | 본 실행 (페이지네이션) |
| `GET /api/v2/admin/products/{no}/variants` | `--include-variants` 시만 |

---

## 2. `/api/v2/admin/products`

### 핵심 파라미터
| 파라미터 | 비고 |
|----------|------|
| `limit` | max 1000 (본 스킬 default) |
| `offset` | 페이지네이션 (links.next 자동 순회) |
| `display` | T (진열) / F (미진열) 필터 |
| `selling` | T (판매중) / F (판매중지) 필터 |
| `brand_code` | 브랜드별 필터 |
| `category_no` | 카테고리별 필터 |
| `price_min`, `price_max` | 가격 범위 필터 |
| `created_start_date`, `created_end_date` | 등록일 범위 |
| `updated_start_date`, `updated_end_date` | 수정일 범위 |
| `embed` | `variants,inventories` (옵션) |
| `fields` | `product_name,product_no,...` 특정 항목만 |

### 응답 핵심 필드
```jsonc
{
  "products": [
    {
      "shop_no": 1,
      "product_no": 101,
      "product_code": "P000XXX",
      "product_name": "샘플 상품",
      "price": "29900.00",
      "supply_price": "15000.00",
      "retail_price": "39900.00",
      "display": "T",
      "selling": "T",
      "description": "...",
      "category_no": [1, 5],
      "brand_code": "B000000A",
      "manufacturer_code": "M000000A",
      "supplier_code": "S000000A",
      "created_date": "2026-01-01T00:00:00+09:00",
      "updated_date": "2026-05-15T12:34:56+09:00"
    }
  ],
  "links": [{ "rel": "next", "href": "..." }]
}
```

---

## 3. `/api/v2/admin/products/count`

### 응답
```jsonc
{ "count": 1234 }
```

---

## 4. `/api/v2/admin/products/{no}/variants` (옵션)

### 핵심 응답
```jsonc
{
  "variants": [
    {
      "shop_no": 1,
      "variant_code": "V000000A",
      "options": [
        { "option_name": "Color", "option_value": "Red" },
        { "option_name": "Size",  "option_value": "M" }
      ],
      "additional_amount": "1000.00",
      "use_inventory": "T",
      "display": "T"
    }
  ]
}
```

---

## 5. Status Code 처리

| Code | 처리 |
|------|------|
| 200 | success |
| 401 / 403 | `Cafe24TokenExpired` → fetch_products.py exit 2 → skill 재발급 |
| 403 (특정) | 뉴상품 쇼핑몰이 아닌 경우 발생 가능 — SKILL.md에 안내 |
| 422 | 파라미터 오류 |
| 429 | `Cafe24RateLimited` → 자동 backoff |

---

## 6. 본 스킬의 가격대 분포 분류 (snapshot 분석)

```python
PRICE_BUCKETS = [
    (0, 10_000, "1만 미만"),
    (10_000, 30_000, "1-3만"),
    (30_000, 50_000, "3-5만"),
    (50_000, 100_000, "5-10만"),
    (100_000, 300_000, "10-30만"),
    (300_000, inf, "30만 이상"),
]
```

dashboard.html에 bar chart로 시각화 (Tier 2 snapshot, line chart 부재).

---

## 7. References

- 공식 문서: developers.cafe24.com (Products 섹션)
- Plan: `.omc/plans/cafe24-mcp-skills-v3.md` §7 Phase 4.A
- ADR-003: Template Strategy (snapshot layout)
- 공용 라이브러리: `scripts/lib/cafe24_client.py`, `scripts/lib/dashboard_template.py`
