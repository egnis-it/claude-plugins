# egnis-it / claude-plugins

Egnis 내부 Claude Code plugin marketplace.

## 설치 (사용자)

### 1. 마켓플레이스 등록 (최초 1회)

Claude Code에서 다음 명령을 실행:

```
/plugin marketplace add egnis-it/claude-plugins
```

### 2. 플러그인 설치

```
/plugin install egnis-cafe24@egnis-tools
```

### 3. 업데이트 받기

플러그인 코드가 갱신되면 다음으로 최신 버전을 받음:

```
/plugin marketplace update
```

Claude Code를 새로 시작하면 백그라운드에서 자동 동기화도 됨.

특정 플러그인 비활성/제거는 `/plugin` 명령으로 가능.

## 플러그인 목록

| 이름 | 설명 |
|---|---|
| `egnis-cafe24` | Cafe24 자사몰 매출 대시보드 (`/cafe24-sales-dashboard`) |

### egnis-cafe24

9개 자사몰 (클룹, 스프린트, 한끼통살, 브레이, 오원, 그로서리서울, 엑쎄라피, 랩노쉬, 메디리즈) 의 매출 데이터를 일자별로 집계해 브라우저로 열어볼 수 있는 HTML 대시보드를 생성합니다.

**사용 예시**:

```
/cafe24-sales-dashboard                                        ← 전체몰 최근 7일
/cafe24-sales-dashboard --brand=labnosh --period=2026-05-12~2026-05-18
/cafe24-sales-dashboard --brand=클룹 --period=2026-05-15~2026-05-18
```

**산출물**:

```
./reports/cafe24/<brand>/<YYYY-MM-DD_to_YYYY-MM-DD>/
├── dashboard.html         ← 메인 결과물 (브랜드 셀렉터/차트/표 포함)
└── data/
    ├── sales_daily.csv    ← 일자별 집계
    ├── sales_by_mall.csv  ← 몰별 집계 (다몰일 때만)
    └── raw/               ← cafe24api 원본 JSON 백업
```

**필요 권한**:

- egnis-mcp 마켓플레이스 토큰 (Claude Code MCP server 등록 + Google OAuth `@egnis.kr`)
- `MCP_TOOL_ALLOW_LIST`에 사용자 이메일 등록 (관리자 요청)

## 개발자 (egnis IT)

이 repo에 push하면 사용자는 다음 `/plugin marketplace update` 호출 시 자동 반영됩니다.

```
plugins/egnis-cafe24/
├── .claude-plugin/plugin.json
├── skills/cafe24-sales-dashboard/SKILL.md
└── scripts/
    ├── fetch_sales.py
    ├── build_sales_dashboard.py
    └── templates/sales_dashboard.html
```

- `plugin.json`에 `version` 필드 없음 → git commit SHA가 버전이 됨 (활발한 개발용)
- 안정 릴리즈가 필요해지면 `version` 추가 + git tag로 전환

새 플러그인 추가는 `.claude-plugin/marketplace.json`의 `plugins` 배열에 항목 추가 + `plugins/<name>/` 디렉토리 생성.
