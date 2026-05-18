# egnis-it / claude-plugins

Egnis 내부 Claude Code plugin marketplace.

## 설치 (사용자)

Claude Code에서 다음 두 명령으로 끝납니다.

### 1. 마켓플레이스 등록 (최초 1회)

```
/plugin marketplace add egnis-it/claude-plugins
```

Claude Code가 GitHub 레포(`https://github.com/egnis-it/claude-plugins`)를 clone 해 `~/.claude/plugins/marketplaces/` 아래에 캐시하고, 루트의 `.claude-plugin/marketplace.json`을 읽어 사용 가능한 플러그인 목록을 파싱합니다.

### 2. 플러그인 설치

```
/plugin install egnis-cafe24@egnis-tools
```

(또는 한 줄로: `/plugin install egnis-cafe24@egnis-it/claude-plugins`)

설치되면 5개 슬래시 커맨드가 활성화됩니다:

- `/cafe24-sales-dashboard` — Tier 1 시계열 매출 대시보드 (기존)
- `/cafe24-orders-export` — 주문 데이터 추출 + 시계열 대시보드
- `/cafe24-salesreport-export` — 일별/월별/시간대별/상품별 매출 리포트
- `/cafe24-ca-api-export` — Analytics API (장바구니 액션 / 상품 매출 분석)
- `/cafe24-products-export` — 상품 카탈로그 스냅샷

### 3. 업데이트 받기

```
/plugin marketplace update
```

Claude Code를 새로 시작하면 백그라운드 동기화도 동작합니다. 특정 플러그인 비활성/제거는 `/plugin` 명령으로 가능.

## 필요 권한

모든 스킬은 **egnis-mcp** 서버를 통해 Cafe24 토큰을 발급받습니다. 다음 조건이 충족되어야 합니다:

- egnis-mcp MCP server 등록 (Claude Code `/mcp` 명령으로 확인)
- Google OAuth `@egnis.kr` 계정 로그인
- 사용자 이메일이 `MCP_TOOL_ALLOW_LIST`에 등록 (관리자 요청)
- 사용 대상 mall이 egnis-mcp의 `list_cafe24_malls`에 등록되어 있어야 함

## 플러그인 목록

| 이름 | 설명 |
|---|---|
| `egnis-cafe24` | Cafe24 자사몰 데이터 수집 + 대시보드 (스킬 5종) |

### egnis-cafe24

9개 자사몰 (클룹, 스프린트, 한끼통살, 브레이, 오원, 그로서리서울, 엑쎄라피, 랩노쉬, 메디리즈) 의 매출/주문/상품 데이터를 수집해 CSV + HTML 대시보드로 생성합니다.

**사용 예시 (`cafe24-sales-dashboard`)**:

```
/cafe24-sales-dashboard                                        # 전체몰 최근 7일
/cafe24-sales-dashboard --brand=labnosh --period=2026-05-12~2026-05-18
/cafe24-sales-dashboard --brand=클룹 --period=2026-05-15~2026-05-18
```

**사용 예시 (신규 스킬 4종)**:

```
/cafe24-orders-export --brand=cloop --period=2026-05-01~2026-05-18
/cafe24-salesreport-export --brand=all --period=2026-04-01~2026-04-30
/cafe24-ca-api-export --brand=labnosh --period=2026-05-12~2026-05-18
/cafe24-products-export --brand=cloop --include-variants
```

**산출물 구조**:

```
./reports/cafe24/<brand>/<YYYY-MM-DD_to_YYYY-MM-DD>/
├── dashboard.html         ← 메인 결과물 (브랜드 셀렉터/차트/표 포함)
└── data/
    ├── *.csv              ← 도메인별 집계 (orders/salesreport/ca_api/products)
    └── raw/               ← cafe24api / ca-api 원본 JSON 백업
```

**호출하는 Cafe24 API**:

| 카테고리 | Endpoint |
|---|---|
| Admin API | `GET /api/v2/admin/orders` (+ `/count`, `/cancellation/{c}`, `/exchange/{c}`) |
| Admin API | `GET /api/v2/admin/financials/dailysales` (+ `/monthlysales`) |
| Admin API | `GET /api/v2/admin/reports/hourlysales` (+ `/productsales`, `/salesvolume`) |
| Admin API | `GET /api/v2/admin/products` (+ `/count`, `/{no}/variants`) |
| Analytics | `GET /carts/action`, `GET /products/sales`, `GET /members/sales` |

## 개발자 (egnis IT)

이 repo에 push하면 사용자는 다음 `/plugin marketplace update` 호출 시 자동 반영됩니다.

```
plugins/egnis-cafe24/
├── .claude-plugin/plugin.json
├── skills/
│   ├── cafe24-sales-dashboard/SKILL.md      # 기존
│   ├── cafe24-orders-export/SKILL.md
│   ├── cafe24-salesreport-export/SKILL.md
│   ├── cafe24-ca-api-export/SKILL.md
│   └── cafe24-products-export/SKILL.md
└── scripts/
    ├── lib/                                  # 공용 라이브러리
    │   ├── cafe24_client.py                  # urllib + token broker
    │   ├── brand_registry.py                 # 9몰 alias
    │   └── dashboard_template.py             # render_dashboard(layout=...)
    ├── fetch_orders.py + build_orders_dashboard.py
    ├── fetch_salesreport.py + build_salesreport_dashboard.py
    ├── fetch_ca_api.py + build_ca_api_dashboard.py
    ├── fetch_products.py + build_products_dashboard.py
    ├── fetch_sales.py + build_sales_dashboard.py    # 기존 (cafe24_client 사용)
    └── templates/sales_dashboard.html
```

- `plugin.json`에 `version` 필드 없음 → git commit SHA가 버전이 됨 (활발한 개발용)
- 안정 릴리즈가 필요해지면 `version` 추가 + git tag로 전환

새 플러그인 추가는 `.claude-plugin/marketplace.json`의 `plugins` 배열에 항목 추가 + `plugins/<name>/` 디렉토리 생성.

## 아키텍처 (egnis-cafe24)

- **urllib only** — 외부 의존성 0 (Cafe24 API 호출은 표준 라이브러리만 사용, ADR-003 §3.2)
- **공용 라이브러리** (`scripts/lib/cafe24_client.py`) — 모든 스킬이 동일 클라이언트 사용, 401/403 자동 처리, 429 backoff, `X-Cafe24-Call-Usage 80%↑` 사전 backoff
- **토큰 미저장** — egnis-mcp에서 in-memory로 받아 환경변수(`CAFE24_TOKENS_JSON`)로만 전달. 디스크 평문 저장 금지
- **테스트** — 52개 unit test PASS + live 검증 (cloop / drlabnosh / sprint 멀티몰 실주 완료)

자세한 플랜은 `.omc/plans/cafe24-mcp-skills-v3.md` 및 `.omc/plans/adr/ADR-001~003` 참조 (로컬 전용).
