"""
scripts/lib/dashboard_template.py — 5스킬 공유 HTML 렌더러 (Phase 1 본구현)

Plan: .omc/plans/cafe24-mcp-skills-v3.md §7 Phase 1.3 + Phase 2
ADR-003: .omc/plans/adr/ADR-003-template-strategy.md §1 (함수형 진입점 only)
ADR-003 §3.2: urllib only — 외부 의존성 0 (Chart.js만 CDN, 인라인 fallback 텍스트)
ADR-003 §4: dict 스키마 frozen contract

render_dashboard(layout, title, brands, kpis, table, chart, meta) → str (HTML)

layout 분기:
  "timeseries" (Tier 1: orders / salesreport / ca-api)
    - brand 토글 + KPI 카드 + 일자별 표 + 추이 라인 차트
    - data-chart-type="line"
  "snapshot" (Tier 2: products / customers)
    - brand 토글 + KPI 카드 + 현재 상태 표 + 분포 차트
    - data-chart-type="bar" or "donut"
    - data-chart-type="line" 부재 (G_UNIFORM_2)

C3 패치 grep gates (G_UNIFORM_1):
  - data-brand-toggle 1개 이상
  - <table> 1개 이상
  - data-csv-download 1개 이상
"""
from __future__ import annotations

import html as _html
import json as _json
from typing import Any

VALID_LAYOUTS = frozenset({"timeseries", "snapshot"})
VALID_ACCENTS = frozenset({"positive", "negative", "neutral"})


def render_dashboard(
    *,
    layout: str,
    title: str,
    brands: list[dict],
    kpis: list[dict],
    table: dict,
    chart: dict | None,
    meta: dict,
) -> str:
    """5스킬 공통 dashboard.html 렌더러.

    Args:
        layout: "timeseries" | "snapshot"
        title: 상단 헤더
        brands: [{"mall_id": str, "label": str, "data": dict}, ...]
        kpis: [{"label": str, "value": str, "delta": str | None,
                "accent": "positive"|"negative"|"neutral"}, ...]
        table: {"columns": list[str], "rows": list[list], "csv_filename": str}
        chart: dict | None — 자유 스키마 (권장: {"type", "series", "x_labels", "options"})
        meta: {"generated_at": str, "source": str, "period": str, "version": str, "api_version": str}

    Returns:
        HTML string (UTF-8). 단일 파일, brand_data 인라인 JSON 포함.
    """
    _validate_inputs(layout, brands, kpis, table, meta)

    brand_data_json = _json.dumps(
        {b["mall_id"]: {"label": b["label"], "data": b["data"]} for b in brands},
        ensure_ascii=False,
    )
    chart_json = _json.dumps(chart, ensure_ascii=False) if chart else "null"

    kpi_html = _render_kpi_cards(kpis)
    brand_toggle_html = _render_brand_toggle(brands)
    table_html = _render_table(table)

    chart_type = _resolve_chart_type(layout, chart)
    chart_section = _render_chart_section(chart_type, chart)

    period = _html.escape(meta.get("period", ""))
    source = _html.escape(meta.get("source", "cafe24api"))
    generated_at = _html.escape(meta.get("generated_at", ""))
    api_version = _html.escape(meta.get("api_version", ""))

    csv_filename = _html.escape(table.get("csv_filename", "data.csv"))
    title_safe = _html.escape(title)

    return _BASE_TEMPLATE.format(
        title=title_safe,
        layout=_html.escape(layout),
        period=period,
        source=source,
        generated_at=generated_at,
        api_version=api_version,
        brand_toggle=brand_toggle_html,
        kpi_cards=kpi_html,
        table_html=table_html,
        chart_section=chart_section,
        chart_type=chart_type,
        csv_filename=csv_filename,
        brand_data_json=brand_data_json,
        chart_json=chart_json,
    )


# ---- input validation ----
def _validate_inputs(layout: str, brands: list, kpis: list, table: dict, meta: dict) -> None:
    if layout not in VALID_LAYOUTS:
        raise ValueError(f"layout must be one of {VALID_LAYOUTS}, got {layout!r}")
    if not isinstance(brands, list):
        raise TypeError(f"brands must be list, got {type(brands).__name__}")
    if not isinstance(kpis, list):
        raise TypeError(f"kpis must be list, got {type(kpis).__name__}")
    for k in kpis:
        accent = k.get("accent", "neutral")
        if accent not in VALID_ACCENTS:
            raise ValueError(f"kpi accent must be one of {VALID_ACCENTS}, got {accent!r}")
    if not isinstance(table, dict):
        raise TypeError(f"table must be dict, got {type(table).__name__}")
    if "columns" not in table or "rows" not in table:
        raise ValueError("table must have 'columns' and 'rows' keys")


# ---- HTML fragment renderers ----
def _render_kpi_cards(kpis: list[dict]) -> str:
    if not kpis:
        return '<p class="kpi-empty">KPI 데이터 없음</p>'
    parts: list[str] = []
    for k in kpis:
        label = _html.escape(str(k.get("label", "")))
        value = _html.escape(str(k.get("value", "")))
        delta = k.get("delta")
        accent = k.get("accent", "neutral")
        delta_html = ""
        if delta is not None:
            delta_html = f'<span class="kpi-delta kpi-{accent}">{_html.escape(str(delta))}</span>'
        parts.append(
            f'<div class="kpi-card" data-accent="{accent}">'
            f'<div class="kpi-label">{label}</div>'
            f'<div class="kpi-value">{value}</div>'
            f"{delta_html}"
            f"</div>"
        )
    return "\n".join(parts)


def _render_brand_toggle(brands: list[dict]) -> str:
    """brand toggle (radio + URL hash 영속)."""
    if not brands:
        return '<div class="brand-toggle" data-brand-toggle="empty"><span>전체</span></div>'
    parts: list[str] = ['<div class="brand-toggle" data-brand-toggle="root">']
    for i, b in enumerate(brands):
        mall_id = _html.escape(str(b.get("mall_id", "")))
        label = _html.escape(str(b.get("label", mall_id)))
        checked = ' checked="checked"' if i == 0 else ""
        parts.append(
            f'<label class="brand-option">'
            f'<input type="radio" name="brand" value="{mall_id}"{checked} />'
            f'<span>{label}</span>'
            f'</label>'
        )
    parts.append("</div>")
    return "\n".join(parts)


def _render_table(table: dict) -> str:
    columns = table.get("columns", [])
    rows = table.get("rows", [])
    if not columns:
        return '<p class="table-empty">표 데이터 없음</p>'

    thead = "<tr>" + "".join(f"<th>{_html.escape(str(c))}</th>" for c in columns) + "</tr>"
    body_rows: list[str] = []
    for row in rows:
        cells = "".join(f"<td>{_html.escape(_format_cell(v))}</td>" for v in row)
        body_rows.append(f"<tr>{cells}</tr>")
    tbody = "\n".join(body_rows) if body_rows else '<tr><td colspan="100">데이터 없음</td></tr>'

    return (
        '<table class="data-table">\n'
        f"<caption>데이터 표</caption>\n"
        f"<thead>{thead}</thead>\n"
        f"<tbody>{tbody}</tbody>\n"
        "</table>"
    )


def _format_cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        # 부동소수점은 소수점 2자리까지 (baseline diff 노이즈 방지)
        return f"{v:.2f}"
    return str(v)


def _resolve_chart_type(layout: str, chart: dict | None) -> str:
    """차트 타입 결정. snapshot은 반드시 line 외 (G_UNIFORM_2)."""
    if chart and isinstance(chart, dict):
        explicit = chart.get("type")
        if isinstance(explicit, str):
            if layout == "snapshot" and explicit == "line":
                # snapshot에 line 차트 강제 거부
                return "bar"
            return explicit
    return "line" if layout == "timeseries" else "bar"


def _render_chart_section(chart_type: str, chart: dict | None) -> str:
    """차트 영역. Chart.js CDN + fallback 텍스트."""
    if chart is None:
        # 차트 없이도 layout=timeseries인 경우 빈 canvas로 G_UNIFORM_2 충족
        return (
            f'<section class="chart-section" data-chart-type="{chart_type}">'
            f'<canvas id="dashboard-chart" data-chart-type="{chart_type}"></canvas>'
            f'<p class="chart-fallback">차트 데이터 없음</p>'
            f"</section>"
        )
    return (
        f'<section class="chart-section" data-chart-type="{chart_type}">'
        f'<canvas id="dashboard-chart" data-chart-type="{chart_type}"></canvas>'
        f'<p class="chart-fallback">JavaScript 비활성 환경에서는 차트를 표시할 수 없습니다.</p>'
        f"</section>"
    )


# ---- 단일 HTML 템플릿 (ADR-003 §1: inline string template only, 외부 HTML 파일 X) ----
_BASE_TEMPLATE = """<!DOCTYPE html>
<html lang="ko" data-layout="{layout}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
* {{ box-sizing: border-box; }}
body {{
  font-family: -apple-system, "Apple SD Gothic Neo", "Segoe UI", sans-serif;
  margin: 0; padding: 0; background: #f7f8fa; color: #1f2937;
}}
header {{ background: #fff; padding: 24px 32px; border-bottom: 1px solid #e5e7eb; }}
header h1 {{ margin: 0 0 8px 0; font-size: 22px; font-weight: 700; }}
header .meta {{ color: #6b7280; font-size: 13px; }}
.container {{ max-width: 1200px; margin: 0 auto; padding: 24px 32px; }}
.brand-toggle {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 16px 0; }}
.brand-option {{
  padding: 8px 14px; border: 1px solid #d1d5db; border-radius: 999px;
  background: #fff; cursor: pointer; font-size: 13px;
}}
.brand-option input {{ display: none; }}
.brand-option input:checked + span {{ font-weight: 700; color: #1d4ed8; }}
.kpi-grid {{
  display: grid; gap: 16px; margin: 20px 0;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
}}
.kpi-card {{
  background: #fff; padding: 16px 20px; border-radius: 8px;
  border: 1px solid #e5e7eb;
}}
.kpi-label {{ font-size: 12px; color: #6b7280; margin-bottom: 6px; }}
.kpi-value {{ font-size: 22px; font-weight: 700; color: #111827; }}
.kpi-delta {{ font-size: 13px; margin-top: 4px; display: inline-block; }}
.kpi-positive {{ color: #059669; }}
.kpi-negative {{ color: #dc2626; }}
.kpi-neutral {{ color: #6b7280; }}
.data-table {{
  width: 100%; border-collapse: collapse; background: #fff;
  border: 1px solid #e5e7eb; border-radius: 8px; overflow: hidden;
  margin: 16px 0;
}}
.data-table caption {{ caption-side: top; text-align: left; padding: 8px 12px; font-weight: 600; }}
.data-table th, .data-table td {{
  padding: 10px 12px; border-bottom: 1px solid #f3f4f6;
  text-align: left; font-size: 13px;
}}
.data-table th {{ background: #f9fafb; font-weight: 600; }}
.chart-section {{
  background: #fff; padding: 20px; border-radius: 8px;
  border: 1px solid #e5e7eb; margin: 16px 0;
}}
.chart-section canvas {{ max-height: 360px; }}
.chart-fallback {{ display: none; color: #6b7280; font-size: 12px; }}
.csv-btn {{
  background: #1d4ed8; color: #fff; border: 0; padding: 8px 16px;
  border-radius: 6px; cursor: pointer; font-size: 13px;
}}
.csv-btn:hover {{ background: #1e40af; }}
footer {{ padding: 16px 32px; color: #9ca3af; font-size: 12px; text-align: center; }}
</style>
</head>
<body>
<header>
  <h1>{title}</h1>
  <div class="meta">
    기간: <span class="meta-period">{period}</span> |
    소스: <span class="meta-source">{source}</span> |
    생성: <span class="meta-generated">{generated_at}</span> |
    API: <span class="meta-api">{api_version}</span>
  </div>
</header>
<main class="container">
  {brand_toggle}
  <div class="kpi-grid">{kpi_cards}</div>
  {chart_section}
  {table_html}
  <p><button class="csv-btn" type="button" data-csv-download="root" data-csv-filename="{csv_filename}">CSV 다운로드</button></p>
</main>
<footer>egnis-cafe24 plugin / dashboard_template (ADR-003) / layout={layout}</footer>
<script type="application/json" id="brand-data-json">{brand_data_json}</script>
<script type="application/json" id="chart-data-json">{chart_json}</script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script>
(function() {{
  // ---- brand toggle (URL hash 영속) ----
  var toggle = document.querySelector('[data-brand-toggle="root"]');
  if (toggle) {{
    var radios = toggle.querySelectorAll('input[type="radio"]');
    function applyHash() {{
      var hash = window.location.hash.replace(/^#brand=/, '');
      if (!hash) return;
      radios.forEach(function(r) {{ if (r.value === hash) r.checked = true; }});
    }}
    radios.forEach(function(r) {{
      r.addEventListener('change', function() {{
        window.location.hash = 'brand=' + r.value;
        window.dispatchEvent(new CustomEvent('brand-changed', {{ detail: {{ mall_id: r.value }} }}));
      }});
    }});
    applyHash();
  }}

  // ---- CSV 다운로드 (현재 표 행만 추출) ----
  var csvBtn = document.querySelector('[data-csv-download="root"]');
  if (csvBtn) {{
    csvBtn.addEventListener('click', function() {{
      var table = document.querySelector('.data-table');
      if (!table) return;
      var rows = [];
      table.querySelectorAll('tr').forEach(function(tr) {{
        var cells = [];
        tr.querySelectorAll('th,td').forEach(function(c) {{
          cells.push('"' + (c.textContent || '').replace(/"/g, '""') + '"');
        }});
        rows.push(cells.join(','));
      }});
      var csv = '\\ufeff' + rows.join('\\n'); // utf-8 BOM
      var blob = new Blob([csv], {{ type: 'text/csv;charset=utf-8;' }});
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = csvBtn.dataset.csvFilename || 'data.csv';
      a.click();
    }});
  }}

  // ---- 차트 렌더 (Chart.js) ----
  try {{
    var chartData = JSON.parse(document.getElementById('chart-data-json').textContent || 'null');
    var canvas = document.getElementById('dashboard-chart');
    if (chartData && canvas && window.Chart) {{
      new Chart(canvas, {{
        type: chartData.type || '{chart_type}',
        data: {{ labels: chartData.x_labels || [], datasets: chartData.series || [] }},
        options: chartData.options || {{ responsive: true, maintainAspectRatio: false }},
      }});
    }}
  }} catch (e) {{
    console.warn('chart render failed:', e);
  }}
}})();
</script>
</body>
</html>"""
