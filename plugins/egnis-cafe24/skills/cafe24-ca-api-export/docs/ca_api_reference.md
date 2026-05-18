# Cafe24 CA API Reference (cafe24-ca-api-export 스킬, 베타)

⚠️ **베타 — 2026-05-18 검증.** Cafe24 기술지원 김승연 (sykim16) 안내:
> "Cafe24 Analytics의 정보는 카페24 CA API를 통해 제공되고 있습니다. CA API가 현재 베타 버전인 관계로 카페24 애널리틱스의 모든 데이터를 제공해 드리지 못하고 있는 점 양해 부탁드립니다. 현재로서는 API를 조합하여 가져와 주시는 방법을 권장 드립니다."

**Host:** `https://ca-api.cafe24data.com`
**Auth:** `Authorization: Bearer <access_token>` (Admin API와 동일 토큰 재사용 가능)
**Auth 추가:** `mall_id` 쿼리 파라미터 (`Cafe24Client.get_ca`가 자동 주입)

---

## 1. Endpoints (본 스킬 사용 3종)

| Endpoint | 본 스킬 출력 CSV | 베타 참고 사항 |
|----------|------------------|---------------|
| `GET /products/hits` | `product_hits.csv` | 상품별 노출수 (조회수) |
| `GET /carts/action` | `cart_actions.csv` | 장바구니 담김 액션 |
| `GET /products/sales` | `product_sales.csv` | 상품별 판매건수 |

슬랙 시나리오: 이 3종으로 **전환율 (판매/노출, 담김/노출)** 계산 → 상품 상세 페이지 개선 효과 측정.

---

## 2. `/products/hits` (상품별 노출수)

### 파라미터
| 파라미터 | 비고 |
|----------|------|
| `mall_id` | (자동) Cafe24Client.get_ca 자동 주입 |
| `start_date` | YYYY-MM-DD |
| `end_date` | YYYY-MM-DD |
| `shop_no` | 옵션 (멀티쇼핑몰) |
| `product_no` | 옵션 (특정 상품 필터) |

### 응답 (베타 스키마, 변경 가능)
```jsonc
// 응답 스키마는 베타로 인해 변동 가능. build_ca_api_dashboard._extract_rows()가
// "hits", "products", "items" 키 후보로 시도.
{
  "hits": [
    { "product_no": 101, "date": "2026-05-01", "hit_count": 250 },
    ...
  ]
}
```

---

## 3. `/carts/action` (장바구니 담김 액션)

### 파라미터
- `mall_id` (자동), `start_date`, `end_date`
- `action_type`: ADD / REMOVE / UPDATE (옵션, 본 스킬에선 ADD만 집계)
- `product_no` (옵션)

### 응답
```jsonc
{
  "cart_actions": [
    { "product_no": 101, "date": "2026-05-01", "add_count": 35 },
    ...
  ]
}
```

---

## 4. `/products/sales` (상품별 판매건수)

### 파라미터
- `mall_id` (자동), `start_date`, `end_date`
- `product_no` (옵션)

### 응답
```jsonc
{
  "product_sales": [
    { "product_no": 101, "date": "2026-05-01",
      "order_count": 8, "sales": 80000 },
    ...
  ]
}
```

---

## 5. 베타 변경 대응 (graceful degrade)

본 스킬은 베타 endpoint가 변경/장애로 응답 불가일 때:
- `fetch_ca_api.py`가 각 endpoint를 `try/except`로 감싸 graceful degrade
- raw JSON 파일에 `{"_beta_unavailable": true, "error": "..."}` 저장
- `build_ca_api_dashboard.py`가 이를 인식하여 해당 endpoint를 빈 데이터로 처리
- dashboard.html 제목에 "X endpoint 미사용" 배너 노출
- skill exit code 0 (graceful)

응답 스키마 변경 발견 시:
1. `_extract_rows()` 후보 키 리스트에 새 키 추가
2. CSV writer 컬럼 추출 로직 업데이트
3. 본 문서 (`ca_api_reference.md`) 갱신

---

## 6. Status Code 처리

| Code | 처리 |
|------|------|
| 200 | success |
| 401 / 403 | `Cafe24TokenExpired` (Admin API와 동일 토큰 재사용 확인 필요) |
| 404 | endpoint 변경 가능성 — graceful degrade |
| 429 | `Cafe24RateLimited` → 자동 backoff |
| 500 / 503 | 베타 서버 일시 장애 — graceful degrade |

---

## 7. 슬랙 시나리오 재현 명령

이그니스 인프라개발팀 요청 직접 대응:

```bash
# 개선 전 (4월)
/cafe24-ca-api-export --brand=cloop --period=2026-04-01~2026-04-30
# 산출: ./reports/cafe24/cloop/2026-04-01_to_2026-04-30/dashboard.html

# 개선 후 (5월)
/cafe24-ca-api-export --brand=cloop --period=2026-05-01~2026-05-31
# 산출: ./reports/cafe24/cloop/2026-05-01_to_2026-05-31/dashboard.html

# 비교:
# 두 dashboard.html의 전환율 매트릭스 비교
# 또는 두 product_hits.csv + product_sales.csv를 Excel/Claude Code에서 join하여 시계열 그래프
```

---

## 8. References

- 공식 문서: developers.cafe24.com/docs/ko/api/cafe24data (Cafe24 Analytics API)
- 슬랙 발췌: 본 스킬 SKILL.md 상단
- ADR-002: Tier 2 MCP fallback 명시적 금지 (.omc/plans/adr/ADR-002-tier-reclassification.md §6)
- ADR-003: Template Strategy
