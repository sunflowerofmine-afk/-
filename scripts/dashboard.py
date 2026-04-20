# scripts/dashboard.py
"""HTML 대시보드 생성 모듈 (GitHub Pages 배포용)"""

import logging
from html import escape
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ─── Public API ──────────────────────────────────────────────────────────────

def generate_dashboard_html(
    report_data: dict,
    output_path: Path,
    latest_output_path: Optional[Path] = None,
) -> bool:
    """
    report_data → HTML 파일 생성. 실패해도 예외 미발생.
    Returns True on success, False on failure.
    """
    try:
        html = _build_html(report_data)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(html, encoding="utf-8")
        logger.info(f"대시보드 생성: {output_path}")
        if latest_output_path:
            Path(latest_output_path).write_text(html, encoding="utf-8")
            logger.info(f"최신 대시보드: {latest_output_path}")
        return True
    except Exception as e:
        logger.error(f"대시보드 생성 실패: {e}", exc_info=True)
        return False


def generate_index_html(reports_dir: Path) -> bool:
    """
    reports/ 폴더의 YYYY-MM-DD_HHMM.html 파일을 스캔해
    날짜별 목록 index.html 생성.
    """
    import re
    pattern = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{4})\.html$")

    by_date: dict[str, list[tuple[str, str]]] = {}
    try:
        for f in sorted(reports_dir.glob("*.html"), reverse=True):
            m = pattern.match(f.name)
            if not m:
                continue
            date_str, time_str = m.group(1), m.group(2)
            label_map = {"1450": "1차 (14:50)", "1750": "2차 (17:50)"}
            label = label_map.get(time_str, f"수동 {time_str[:2]}:{time_str[2:]}")
            by_date.setdefault(date_str, []).append((label, f"reports/{f.name}"))

        rows = []
        for date_str in sorted(by_date.keys(), reverse=True):
            links_html = " &nbsp;·&nbsp; ".join(
                f'<a href="{href}">{lbl}</a>'
                for lbl, href in sorted(by_date[date_str])
            )
            rows.append(
                f'<tr><td class="idx-date">{date_str}</td>'
                f'<td class="idx-links">{links_html}</td></tr>'
            )

        rows_html = "\n".join(rows) if rows else "<tr><td colspan='2' style='color:var(--muted);text-align:center'>데이터 없음</td></tr>"

        # 날짜 picker용 JS 데이터 (날짜 → 가장 최신 리포트 URL)
        import json as _json
        date_to_url = {}
        for date_str, entries in by_date.items():
            # 1750 > 1450 > 수동 최신 순으로 우선순위
            best = None
            for lbl, href in sorted(entries, reverse=True):
                if "2차" in lbl:
                    best = href; break
                if best is None or "1차" in lbl:
                    best = href
            date_to_url[date_str] = best or entries[-1][1]
        date_map_js = _json.dumps(date_to_url)

        html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>종가베팅 대시보드</title>
<style>
{_css()}
a {{ color: var(--blue); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
.idx-date {{ font-weight: 600; white-space: nowrap; padding: 10px 16px; }}
.idx-links {{ padding: 10px 16px; }}
.date-picker-wrap {{
  display: flex; align-items: center; gap: 10px;
  padding: 12px 0 8px;
}}
#date-picker {{
  background: var(--bg2); color: var(--fg);
  border: 1px solid var(--border); border-radius: 6px;
  padding: 6px 10px; font-size: 14px; cursor: pointer;
}}
#date-go {{
  background: var(--blue); color: #000; font-weight: 700;
  border: none; border-radius: 6px;
  padding: 6px 14px; font-size: 14px; cursor: pointer;
}}
#date-go:hover {{ opacity: 0.85; }}
#date-msg {{ font-size: 12px; color: var(--red); }}
</style>
</head>
<body>
<div class="wrap">
  <div class="page-header">
    <h1>📈 종가베팅 대시보드</h1>
  </div>
  <div class="date-picker-wrap">
    <input type="date" id="date-picker">
    <button id="date-go">이동</button>
    <span id="date-msg"></span>
  </div>
  <div class="tbl-wrap">
    <table>
      <thead><tr><th>날짜</th><th>리포트</th></tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
  </div>
</div>
<script>
const DATE_MAP = {date_map_js};
const picker = document.getElementById('date-picker');
const msg    = document.getElementById('date-msg');
document.getElementById('date-go').addEventListener('click', () => {{
  const d = picker.value;
  if (!d) {{ msg.textContent = '날짜를 선택하세요'; return; }}
  const url = DATE_MAP[d];
  if (url) {{ location.href = url; }}
  else {{ msg.textContent = '해당 날짜 리포트 없음'; }}
}});
</script>
</body>
</html>"""

        index_path = reports_dir.parent / "index.html"
        index_path.write_text(html, encoding="utf-8")
        logger.info(f"인덱스 생성: {index_path}")
        return True
    except Exception as e:
        logger.error(f"인덱스 생성 실패: {e}", exc_info=True)
        return False


def build_dashboard_links(report_date: str, snapshot_time: str, base_url: str, latest_name: str | None = None) -> dict:
    """
    GitHub Pages 링크 생성.
    base_url 미설정 시 빈 dict 반환.
    """
    if not base_url:
        return {}
    base = base_url.rstrip("/")
    if latest_name is None:
        latest_name = f"latest_{snapshot_time}.html"
    return {
        "dated_url":  f"{base}/reports/{report_date}_{snapshot_time}.html",
        "latest_url": f"{base}/reports/{latest_name}",
    }


# ─── 포맷 헬퍼 ────────────────────────────────────────────────────────────────

def _e(s) -> str:
    return escape(str(s)) if s is not None else ""

def _tv_eok(won) -> str:
    try:
        v = float(won)
        return f"{v / 1e8:,.0f}억"
    except Exception:
        return "-"

def _sign(v) -> str:
    try:
        f = float(v)
        return f"+{f:.2f}%" if f >= 0 else f"{f:.2f}%"
    except Exception:
        return "-"

def _badge(v) -> str:
    if v is True:  return '<span class="badge ok">O</span>'
    if v is False: return '<span class="badge ng">X</span>'
    return '<span class="badge na">-</span>'

def _net_str(won) -> str:
    if won is None: return "-"
    try:
        return f"{float(won) / 1e8:+.0f}억"
    except Exception:
        return "-"

def _supply_info(supply) -> dict:
    """SupplyData 객체 or dict → plain dict"""
    empty = {"status": "failed", "institution_net": None, "foreign_net": None, "program_net": None}
    if supply is None:
        return empty
    if hasattr(supply, "status"):       # SupplyData dataclass
        return {
            "status":          supply.status,
            "institution_net": supply.institution_net,
            "foreign_net":     supply.foreign_net,
            "program_net":     supply.program_net,
        }
    if isinstance(supply, dict):
        return {**empty, **supply}
    return empty

def _news_titles(news) -> list:
    """NewsData 객체 or list → list[str] (최대 2개)"""
    if news is None:
        return []
    if hasattr(news, "titles"):         # NewsData dataclass
        return list(news.titles[:2])
    if isinstance(news, list):
        result = []
        for n in news[:2]:
            result.append(n.get("title", "") if isinstance(n, dict) else str(n))
        return result
    return []

def _score_val(score) -> str:
    if score is None: return "-"
    if hasattr(score, "total_score"): return str(score.total_score)
    if isinstance(score, dict): return str(score.get("total_score", "-"))
    return "-"


# ─── CSS ─────────────────────────────────────────────────────────────────────

def _css() -> str:
    return """
:root {
    --bg:        #0d1117;
    --bg2:       #161b22;
    --bg3:       #21262d;
    --border:    #30363d;
    --text:      #e6edf3;
    --muted:     #8b949e;
    --green:     #3fb950;
    --green-bg:  #0f2d13;
    --yellow:    #d29922;
    --yellow-bg: #2d2200;
    --red:       #f85149;
    --red-bg:    #2d0f0f;
    --blue:      #58a6ff;
    --purple:    #bc8cff;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    font-size: 14px;
    line-height: 1.6;
    padding: 12px;
}
.wrap { max-width: 1100px; margin: 0 auto; }

/* ── 헤더 ── */
.page-header {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px 20px;
    margin-bottom: 16px;
}
.page-header h1 { font-size: 18px; color: var(--blue); margin-bottom: 6px; }
.page-header .meta { color: var(--muted); font-size: 13px; }
.page-header .meta span { margin-right: 16px; }

/* ── 섹션 타이틀 ── */
.section-title {
    font-size: 15px;
    font-weight: 600;
    color: var(--text);
    border-left: 3px solid var(--blue);
    padding-left: 10px;
    margin: 20px 0 10px;
}

/* ── 요약 카드 그리드 ── */
.summary-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
    gap: 10px;
    margin-bottom: 16px;
}
.summary-card {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px 14px;
    text-align: center;
}
.summary-card .label { font-size: 11px; color: var(--muted); margin-bottom: 4px; }
.summary-card .value { font-size: 22px; font-weight: 700; color: var(--blue); }
.summary-card .sub   { font-size: 11px; color: var(--muted); margin-top: 2px; }

/* ── 핵심 후보 카드 ── */
.candidate-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
    gap: 14px;
    margin-bottom: 8px;
}
.candidate-card {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
}
.candidate-card.has-pattern { border-color: var(--green); }
.candidate-card.in-inter    { border-color: var(--yellow); }
.card-head {
    background: var(--bg3);
    padding: 10px 14px;
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 8px;
}
.card-head .name   { font-weight: 700; font-size: 15px; }
.card-head .code   { color: var(--muted); font-size: 12px; }
.card-head .market { font-size: 11px; color: var(--muted);
                     background: var(--bg); border-radius: 4px; padding: 2px 6px; }
.card-head .score-badge {
    background: var(--blue);
    color: #000;
    font-weight: 700;
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 13px;
    white-space: nowrap;
}
.card-body { padding: 12px 14px; }
.card-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 3px 0;
    border-bottom: 1px solid var(--bg3);
}
.card-row:last-child { border-bottom: none; }
.card-row .lbl { color: var(--muted); font-size: 12px; min-width: 90px; }
.card-row .val { font-size: 13px; text-align: right; }
.val.pos  { color: var(--green); }
.val.neg  { color: var(--red); }
.val.warn { color: var(--yellow); }
.pattern-tag {
    display: inline-block;
    background: var(--green-bg);
    color: var(--green);
    border: 1px solid var(--green);
    border-radius: 4px;
    padding: 1px 8px;
    font-size: 12px;
    font-weight: 600;
    margin-bottom: 8px;
}
.pattern-none {
    display: inline-block;
    background: var(--bg3);
    color: var(--muted);
    border-radius: 4px;
    padding: 1px 8px;
    font-size: 12px;
    margin-bottom: 8px;
}
.news-item {
    background: var(--bg3);
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 12px;
    color: var(--muted);
    margin-top: 4px;
    word-break: break-all;
}

/* ── 배지 ── */
.badge { border-radius: 4px; padding: 1px 7px; font-size: 12px; font-weight: 600; }
.badge.ok { background: var(--green-bg);  color: var(--green); }
.badge.ng { background: var(--red-bg);    color: var(--red); }
.badge.na { background: var(--bg3);       color: var(--muted); }
.badge.inter { background: var(--yellow-bg); color: var(--yellow); }

/* ── 테이블 ── */
.tbl-wrap { overflow-x: auto; margin-bottom: 8px; }
table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
    background: var(--bg2);
    border-radius: 8px;
    overflow: hidden;
}
thead th {
    background: var(--bg3);
    color: var(--muted);
    font-weight: 600;
    text-align: left;
    padding: 8px 12px;
    white-space: nowrap;
    border-bottom: 1px solid var(--border);
}
tbody td {
    padding: 7px 12px;
    border-bottom: 1px solid var(--bg3);
    vertical-align: middle;
}
tbody tr:last-child td { border-bottom: none; }
tbody tr:hover td { background: var(--bg3); }
.td-name { font-weight: 600; }
.td-code { color: var(--muted); font-size: 11px; }
.td-pos  { color: var(--green); }
.td-neg  { color: var(--red); }
.td-warn { color: var(--yellow); }

/* ── 섹터 ── */
.sector-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 10px;
    margin-bottom: 8px;
}
.sector-card {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
}
.sector-head {
    background: var(--bg3);
    padding: 8px 12px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 6px;
}
.sector-head .s-name { font-weight: 700; font-size: 14px; }
.sector-head .s-chg  { font-size: 12px; }
.sector-head .s-tv   { font-size: 11px; color: var(--muted); }
.sector-stocks { width: 100%; border-collapse: collapse; font-size: 12px; }
.sector-stocks td { padding: 4px 10px; border-bottom: 1px solid var(--bg3); }
.sector-stocks tr:last-child td { border-bottom: none; }
.sector-stocks .s-stock-name { font-weight: 600; }
.sector-stocks .s-stock-tv   { color: var(--muted); text-align: right; }
.sector-tag {
    display: inline-block;
    background: var(--bg3);
    color: var(--blue);
    border-radius: 3px;
    padding: 1px 5px;
    font-size: 11px;
    margin-left: 4px;
}

/* ── 탈락 영역 ── */
.rejected-list { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; }
.rejected-item {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 6px 12px;
    font-size: 12px;
}
.rejected-item .r-name { font-weight: 600; margin-bottom: 2px; }
.rejected-item .r-reason { color: var(--red); font-size: 11px; }

/* ── 시장 요약 ── */
.market-summary {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 16px;
    margin-bottom: 16px;
}
.ms-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 10px;
    margin-bottom: 12px;
}
.ms-item { text-align: center; }
.ms-label { font-size: 11px; color: var(--muted); margin-bottom: 3px; }
.ms-value { font-size: 20px; font-weight: 700; color: var(--text); }
.judgment-ok {
    display: inline-block;
    background: var(--green-bg);
    color: var(--green);
    border: 1px solid var(--green);
    border-radius: 6px;
    padding: 4px 16px;
    font-size: 15px;
    font-weight: 700;
}
.judgment-ng {
    display: inline-block;
    background: var(--yellow-bg);
    color: var(--yellow);
    border: 1px solid var(--yellow);
    border-radius: 6px;
    padding: 4px 16px;
    font-size: 15px;
    font-weight: 700;
}

/* ── 관찰 후보 ── */
.watch-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
    gap: 10px;
    margin-bottom: 8px;
}
.watch-card {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px 14px;
}
.watch-card .name { font-weight: 600; font-size: 14px; }
.watch-card .code { color: var(--muted); font-size: 11px; margin-left: 6px; }
.watch-body { margin-top: 6px; font-size: 13px; color: var(--muted); }
.watch-note { font-size: 11px; color: var(--yellow); font-weight: 400; margin-left: 8px; }

/* ── 탈락 요약 ── */
.rejected-summary {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px 16px;
    font-size: 13px;
    color: var(--muted);
    margin-bottom: 8px;
}
.rejected-summary span { margin-right: 16px; }

/* ── 섹터 캘린더 ── */
.cal-table {
    width: 100%;
    table-layout: fixed;
    border-collapse: collapse;
    background: var(--bg2);
    border-radius: 8px;
    overflow: hidden;
    font-size: 12px;
}
.cal-table thead th {
    text-align: center;
    padding: 6px;
    font-size: 12px;
}
.cal-cell {
    vertical-align: top;
    padding: 6px 8px;
    border: 1px solid var(--bg3);
    min-height: 56px;
    min-width: 0;
}
.cal-cell.cal-today { background: var(--bg3); border-color: var(--blue); }
.cal-day { font-size: 11px; color: var(--muted); margin-bottom: 3px; }
.cal-sector {
    display: block;
    font-size: 11px;
    color: var(--text);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    margin-bottom: 1px;
}

/* ── 없음 메시지 ── */
.empty-msg {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 20px;
    text-align: center;
    color: var(--muted);
    font-size: 13px;
    margin-bottom: 8px;
}

/* ── 반응형 ── */
@media (max-width: 600px) {
    body { padding: 8px; font-size: 13px; }
    .candidate-grid { grid-template-columns: 1fr; }
    .summary-grid   { grid-template-columns: repeat(3, 1fr); }
    thead th, tbody td { padding: 6px 8px; }
}
"""


# ─── 섹션 생성 함수 ───────────────────────────────────────────────────────────

def _section_header(data: dict) -> str:
    meta = data.get("metadata", {})
    market = data.get("market_summary", {})
    date         = _e(meta.get("date", "-"))
    snapshot     = _e(meta.get("snapshot_time", "-"))
    run_time_raw = _e(meta.get("run_time", "-"))
    run_type     = _e(meta.get("run_type", "-"))
    run_time_hm  = run_time_raw.split(" ")[-1] if " " in run_time_raw else run_time_raw
    base_map     = {"1차": "14:50", "2차": "17:50"}
    base_time    = base_map.get(meta.get("run_type", ""), run_time_hm)
    kospi_tv  = _tv_eok(market.get("kospi_tv_eok",  0) * 1e8)
    kosdaq_tv = _tv_eok(market.get("kosdaq_tv_eok", 0) * 1e8)
    return f"""
<div class="page-header">
  <h1>📈 종가베팅 스캔 리포트</h1>
  <div class="meta">
    <span>📅 {date}</span>
    <span>기준시각 {base_time}</span>
    <span>실행시각 {run_time_hm} KST</span>
    <span>분류 {run_type}</span>
  </div>
  <div class="meta" style="margin-top:4px;">
    <span>코스피 {kospi_tv}</span>
    <span>코스닥 {kosdaq_tv}</span>
  </div>
</div>
"""


def _section_market_summary(data: dict) -> str:
    m = data.get("market_summary", {})
    kospi_tv          = m.get("kospi_tv_eok", 0)
    kosdaq_tv         = m.get("kosdaq_tv_eok", 0)
    tv_1500           = m.get("tv_1500_count", 0)
    gainers_tv_1500   = m.get("gainers_tv_1500_count", 0)

    if gainers_tv_1500 >= 3:
        judgment_html = '<span class="judgment-ok">✅ 종베 가능</span>'
    else:
        judgment_html = '<span class="judgment-ng">⚠️ 종베 비우호</span>'

    return f"""
<div class="section-title">📊 시장 요약</div>
<div class="market-summary">
  <div class="ms-grid">
    <div class="ms-item"><div class="ms-label">코스피 거래대금</div><div class="ms-value">{kospi_tv:,.0f}억</div></div>
    <div class="ms-item"><div class="ms-label">코스닥 거래대금</div><div class="ms-value">{kosdaq_tv:,.0f}억</div></div>
    <div class="ms-item"><div class="ms-label">1500억↑ 종목 수</div><div class="ms-value">{tv_1500}개</div></div>
    <div class="ms-item"><div class="ms-label">상승Top20 중 1500억↑</div><div class="ms-value">{gainers_tv_1500}개</div></div>
  </div>
  {judgment_html}
</div>
"""


def _section_sector_calendar(calendar: dict, today_str: str) -> str:
    if not calendar:
        return ""
    from datetime import date, timedelta
    try:
        today = date.fromisoformat(today_str)
    except ValueError:
        today = date.today()

    # 이번주 월요일 기준으로 4주 전 월요일부터 표시
    this_monday = today - timedelta(days=today.weekday())
    start = this_monday - timedelta(weeks=3)

    header = "<tr><th>월</th><th>화</th><th>수</th><th>목</th><th>금</th></tr>"
    rows = []
    for week in range(4):
        cells = []
        for day in range(5):
            d = start + timedelta(weeks=week, days=day)
            d_str = d.isoformat()
            sectors = calendar.get(d_str, [])
            is_today = d_str == today_str
            cell_cls = "cal-cell cal-today" if is_today else "cal-cell"
            tags = "".join(f'<span class="cal-sector">{_e(s)}</span>' for s in sectors[:4])
            cells.append(
                f'<td class="{cell_cls}">'
                f'<div class="cal-day">{d.day}</div>'
                f"{tags}"
                f"</td>"
            )
        rows.append(f'<tr>{"".join(cells)}</tr>')

    return (
        '<div class="section-title">📅 최근 4주간 주도섹터</div>'
        '<div class="tbl-wrap"><table class="cal-table">'
        f'<thead>{header}</thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        "</table></div>"
    )


def _section_leading_sectors(sectors: list) -> str:
    if not sectors:
        return ""
    cards = []
    for sec in sectors:
        chg = float(sec.get("change_pct", 0))
        chg_cls = "pos" if chg >= 0 else "neg"
        stocks_html = ""
        for s in sec.get("top_stocks", [])[:4]:
            s_chg = float(s.get("등락률", 0))
            s_cls = "td-pos" if s_chg >= 0 else "td-neg"
            stocks_html += (
                f"<tr>"
                f'<td class="s-stock-name">{_e(s.get("종목명",""))}</td>'
                f'<td class="{s_cls}">{_sign(s_chg)}</td>'
                f'<td class="s-stock-tv">{_tv_eok(s.get("거래대금",0))}</td>'
                f"</tr>"
            )
        cards.append(
            f'<div class="sector-card">'
            f'<div class="sector-head">'
            f'<span class="s-name">{_e(sec["sector_name"])}</span>'
            f'<span class="s-chg {chg_cls}">{_sign(chg)}</span>'
            f'<span class="s-tv">{_tv_eok(sec.get("tv_eok",0)*1e8)}</span>'
            f"</div>"
            f'<table class="sector-stocks">{stocks_html}</table>'
            f"</div>"
        )
    return (
        '<div class="section-title">🏭 주도 섹터</div>'
        f'<div class="sector-grid">{"".join(cards)}</div>'
    )


def _section_watch_candidates(rejected: list) -> str:
    watches = sorted(
        [r for r in rejected if "패턴 없음" in r.get("reason", "")],
        key=lambda x: x.get("trading_value", 0),
        reverse=True,
    )[:3]
    if not watches:
        return ""

    cards = []
    for c in watches:
        tv  = c.get("trading_value", 0)
        chg = float(c.get("change_pct", 0))
        chg_cls = "pos" if chg >= 0 else "neg"
        cards.append(
            f'<div class="watch-card">'
            f'<div><span class="name">{_e(c.get("name",""))}</span>'
            f'<span class="code">{_e(c.get("code",""))}</span></div>'
            f'<div class="watch-body">'
            f'거래대금 <strong>{_tv_eok(tv)}</strong>'
            f' &nbsp;·&nbsp; 등락률 <strong class="{chg_cls}">{_sign(chg)}</strong>'
            f'</div></div>'
        )
    return (
        '<div class="section-title">👁 관찰 후보'
        '<span class="watch-note">매수 후보 아님 · 관찰용</span></div>'
        f'<div class="watch-grid">{"".join(cards)}</div>'
    )


def _section_rejected_summary(rejected: list) -> str:
    if not rejected:
        return ""
    counts: dict[str, int] = {}
    for r in rejected:
        reason = r.get("reason", "기타")
        if "거래대금 부족" in reason:
            key = "거래대금 부족"
        elif "패턴 없음" in reason:
            key = "패턴 없음 + 교집합 아님"
        else:
            key = reason
        counts[key] = counts.get(key, 0) + 1

    items = " ".join(f'<span>{_e(k)}: {v}개</span>' for k, v in counts.items())
    return f'<div class="section-title">🚫 탈락 요약</div><div class="rejected-summary">{items}</div>'


def _section_summary_cards(data: dict) -> str:
    m = data.get("market_summary", {})
    cards = [
        ("코스피 거래대금",  f"{m.get('kospi_tv_eok',  0):,.0f}억"),
        ("코스닥 거래대금",  f"{m.get('kosdaq_tv_eok', 0):,.0f}억"),
        ("상승률 Top",       str(m.get("gainers_count",      0)) + "종목"),
        ("거래대금 Top",     str(m.get("tv_count",           0)) + "종목"),
        ("교집합 후보",      str(m.get("intersection_count", 0)) + "개"),
        ("핵심 후보",        str(m.get("core_count",         0)) + "개"),
    ]
    items = "".join(
        f'<div class="summary-card"><div class="label">{_e(label)}</div>'
        f'<div class="value">{_e(val)}</div></div>'
        for label, val in cards
    )
    return f'<div class="summary-grid">{items}</div>'


_OFFSET_LABEL = {0: "당일", 1: "1일전", 2: "2일전", 3: "3일전"}
_PATTERN_TYPE_ORDER = ["당일돌파형", "고가횡보형", "눌림관찰형", "없음"]
_PATTERN_SECTION_TITLE = {
    "당일돌파형": "🚀 당일 돌파형",
    "고가횡보형": "📊 1~3일전 기준봉 후 고가횡보형",
    "눌림관찰형": "📉 눌림 관찰형",
    "없음":       "📌 기타 (교집합)",
}
_PATTERN_CARD_COLOR = {
    "당일돌파형": "#3fb950",  # green
    "고가횡보형": "#58a6ff",  # blue
    "눌림관찰형": "#d29922",  # yellow
    "없음":       "#8b949e",  # muted
}


def _candidate_card_html(c: dict) -> str:
    ind  = c.get("indicators", {})
    pat  = c.get("patterns",   {})
    sup  = _supply_info(c.get("supply"))
    news = _news_titles(c.get("news"))
    score = _score_val(c.get("score"))
    tv   = float(c.get("trading_value", 0))
    chg  = float(c.get("change_pct",    0))
    in_inter  = c.get("in_inter",    False)
    has_pat   = c.get("has_pattern", False)

    pat_label   = pat.get("pattern_type_label", "없음")
    offset      = pat.get("base_candle_day_offset")
    offset_str  = _OFFSET_LABEL.get(offset, "-") if offset is not None else "-"
    gap_pct     = pat.get("base_high_gap_pct")
    gap_str     = f"{gap_pct:+.1f}%" if gap_pct is not None else "-"
    gap_cls     = "pos" if gap_pct is not None and gap_pct >= 0 else "neg" if gap_pct is not None and gap_pct < -3 else "warn"
    vol_dec     = pat.get("post_base_volume_decline_flag", False)
    overheated  = pat.get("overheated_3d_flag", False)
    struct_ok   = not pat.get("structure_broken_flag", False)
    new_high_60d  = pat.get("new_high_60d", False)
    near_high_60d = pat.get("near_high_60d", False)

    card_color = _PATTERN_CARD_COLOR.get(pat_label, "#8b949e")
    card_cls   = "candidate-card"
    if in_inter:  card_cls += " in-inter"
    elif has_pat: card_cls += " has-pattern"

    inter_badge = '<span class="badge inter">★교집합</span> ' if in_inter else ""

    chg_cls  = "val pos" if chg >= 0 else "val neg"
    inst_str = _net_str(sup.get("institution_net"))
    frgn_str = _net_str(sup.get("foreign_net"))
    sup_ok   = sup.get("status") == "ok"

    news_html = "".join(f'<div class="news-item">📰 {_e(t)}</div>' for t in news)
    if not news_html:
        news_html = '<div class="news-item" style="color:var(--muted)">뉴스 없음</div>'

    raw_news = c.get("news")
    llm_html = ""
    if hasattr(raw_news, "llm_summary") and raw_news.llm_summary:
        llm_html = (
            f'<div style="margin-top:6px;font-size:12px;color:var(--fg);'
            f'padding:4px 8px;background:var(--bg3);border-radius:4px">'
            f'{_e(raw_news.llm_summary)}</div>'
        )

    tv_ratio    = pat.get("tv_ratio")
    tv_ratio_str = f"{tv_ratio:.2f}" if tv_ratio is not None else "-"
    tv_ratio_cls = "val pos" if tv_ratio is not None and tv_ratio >= 0.4 else "val warn" if tv_ratio is not None and tv_ratio >= 0.2 else "val neg"
    status_summary = pat.get("status_summary", "-")
    tv_3d_flow  = pat.get("tv_3d_flow", [])
    tv_3d_str   = " → ".join(_tv_eok(v) for v in tv_3d_flow) if tv_3d_flow else "-"

    # 기준봉 이후 경과일 상세
    post_base_days = pat.get("post_base_days", [])
    post_base_html = ""
    if post_base_days:
        _OFFSET_L = {1: "1일전", 2: "2일전", 3: "3일전"}
        rows_pb = ""
        for d in post_base_days:
            off = d.get("offset")
            chg = d.get("change_pct", 0)
            cvb = d.get("close_vs_base_high")
            tv_d = d.get("tv", 0)
            chg_cls = "val pos" if chg >= 0 else "val neg"
            cvb_str = f"{cvb:+.1f}%" if cvb is not None else "-"
            cvb_cls = "val pos" if cvb is not None and cvb >= -3 else "val warn" if cvb is not None and cvb >= -8 else "val neg"
            rows_pb += (
                f"<tr>"
                f'<td style="color:var(--muted);font-size:11px">{_OFFSET_L.get(off, f"{off}일전")}</td>'
                f'<td class="{chg_cls}">{_sign(chg)}</td>'
                f'<td class="{cvb_cls}" title="기준봉고가 대비">{cvb_str}</td>'
                f'<td style="color:var(--muted);font-size:11px">{_tv_eok(tv_d)}</td>'
                f"</tr>"
            )
        post_base_html = (
            f'<div class="card-row" style="flex-direction:column;align-items:flex-start">'
            f'<span class="lbl" style="margin-bottom:4px">기준봉 후 경과</span>'
            f'<table style="width:100%;font-size:12px;border-collapse:collapse">'
            f'<thead><tr style="color:var(--muted);font-size:10px">'
            f'<th style="text-align:left">일자</th><th>등락</th><th>고가比</th><th>거래대금</th>'
            f'</tr></thead><tbody>{rows_pb}</tbody></table></div>'
        )

    return f"""
<div class="{card_cls}" style="border-top: 3px solid {card_color}">
  <div class="card-head">
    <div>
      <div class="name">{inter_badge}{_e(c.get('name',''))}</div>
      <div class="code">{_e(c.get('code',''))} &middot; {_e(c.get('market',''))}</div>
    </div>
    <div class="score-badge">점수 {_e(score)}</div>
  </div>
  <div class="card-body">
    <div class="card-row"><span class="lbl">패턴</span>
      <span class="val" style="color:{card_color};font-weight:600">{_e(pat_label)}</span></div>
    <div class="card-row"><span class="lbl">기준봉 시점</span>
      <span class="val">{_e(offset_str)}</span></div>
    <div class="card-row"><span class="lbl">상태</span>
      <span class="val">{_e(status_summary)}</span></div>
    <div class="card-row"><span class="lbl">기준봉고가 대비</span>
      <span class="{gap_cls} val">{_e(gap_str)}</span></div>
    <div class="card-row"><span class="lbl">대금ratio (기준봉 대비)</span>
      <span class="{tv_ratio_cls}">{_e(tv_ratio_str)}</span></div>
    <div class="card-row"><span class="lbl">최근3일 대금흐름</span>
      <span class="val" style="font-size:11px">{_e(tv_3d_str)}</span></div>
    {post_base_html}
    <div class="card-row"><span class="lbl">60일 신고가</span>
      <span class="val">{"🔺 신고가" if new_high_60d else ("📍 고점권(97%)" if near_high_60d else "—")}</span></div>
    <div class="card-row"><span class="lbl">기준봉 후 대금감소</span>
      <span class="val">{_badge(vol_dec)}</span></div>
    <div class="card-row"><span class="lbl">상승률</span>
      <span class="{chg_cls}">{_sign(chg)}</span></div>
    <div class="card-row"><span class="lbl">거래대금</span>
      <span class="val">{_tv_eok(tv)}</span></div>
    <div class="card-row"><span class="lbl">장대/준장대/첫장대</span>
      <span class="val">{_badge(ind.get('big_candle'))} / {_badge(ind.get('loose_big_candle'))} / {_badge(ind.get('first_big_candle'))}</span></div>
    <div class="card-row"><span class="lbl">이평밀집</span>
      <span class="val">{_badge(ind.get('ma_cluster'))}</span></div>
    <div class="card-row"><span class="lbl">거래량/거래대금 60일최고</span>
      <span class="val">{_badge(ind.get('vol_peak'))} / {_badge(ind.get('tv_peak'))}</span></div>
    <div class="card-row"><span class="lbl">기관 순매수</span>
      <span class="val">{inst_str if sup_ok else '확인불가'}</span></div>
    <div class="card-row"><span class="lbl">외국인 순매수</span>
      <span class="val">{frgn_str if sup_ok else '확인불가'}</span></div>
    <div style="margin-top:8px;">{news_html}{llm_html}</div>
  </div>
</div>"""


def _section_core_candidates(candidates: list) -> str:
    parts = ['<div class="section-title">🎯 핵심 후보</div>']
    if not candidates:
        parts.append('<div class="empty-msg">조건 충족 핵심 후보 없음</div>')
        return "".join(parts)

    # 패턴 타입별 그룹화
    groups: dict[str, list] = {t: [] for t in _PATTERN_TYPE_ORDER}
    for c in candidates:
        label = c.get("patterns", {}).get("pattern_type_label", "없음")
        groups.setdefault(label, []).append(c)

    for label in _PATTERN_TYPE_ORDER:
        group = groups.get(label, [])
        if not group:
            continue
        title = _PATTERN_SECTION_TITLE.get(label, label)
        color = _PATTERN_CARD_COLOR.get(label, "#8b949e")
        parts.append(
            f'<div class="section-title" style="border-color:{color}">{title} '
            f'<span style="font-size:12px;font-weight:400;color:var(--muted)">({len(group)}개)</span></div>'
        )
        cards_html = "".join(_candidate_card_html(c) for c in group)
        parts.append(f'<div class="candidate-grid">{cards_html}</div>')

    return "".join(parts)


def _section_table_gainers(rows: list) -> str:
    if not rows:
        return '<div class="section-title">📊 상승률 Top20</div><div class="empty-msg">데이터 없음</div>'
    header = "<tr><th>#</th><th>종목명</th><th>코드</th><th>시장</th><th>등락률</th><th>거래대금</th></tr>"
    body_rows = []
    for i, r in enumerate(rows, 1):
        chg = float(r.get("등락률", 0))
        cls = "td-pos" if chg >= 0 else "td-neg"
        sector_tag = f'<span class="sector-tag">{_e(r["sector"])}</span>' if r.get("sector") else ""
        body_rows.append(
            f"<tr><td>{i}</td>"
            f'<td class="td-name">{_e(r.get("종목명",""))}{sector_tag}</td>'
            f'<td class="td-code">{_e(r.get("종목코드",""))}</td>'
            f'<td>{_e(r.get("시장",""))}</td>'
            f'<td class="{cls}">{_sign(chg)}</td>'
            f'<td>{_tv_eok(r.get("거래대금",0))}</td></tr>'
        )
    return (
        '<div class="section-title">📊 상승률 Top20</div>'
        '<div class="tbl-wrap"><table>'
        f'<thead>{header}</thead><tbody>{"".join(body_rows)}</tbody>'
        '</table></div>'
    )


def _section_table_tv(rows: list) -> str:
    if not rows:
        return '<div class="section-title">💰 거래대금 Top20</div><div class="empty-msg">데이터 없음</div>'
    header = "<tr><th>#</th><th>종목명</th><th>코드</th><th>시장</th><th>거래대금</th><th>등락률</th></tr>"
    body_rows = []
    for i, r in enumerate(rows, 1):
        chg = float(r.get("등락률", 0))
        cls = "td-pos" if chg >= 0 else "td-neg"
        sector_tag = f'<span class="sector-tag">{_e(r["sector"])}</span>' if r.get("sector") else ""
        body_rows.append(
            f"<tr><td>{i}</td>"
            f'<td class="td-name">{_e(r.get("종목명",""))}{sector_tag}</td>'
            f'<td class="td-code">{_e(r.get("종목코드",""))}</td>'
            f'<td>{_e(r.get("시장",""))}</td>'
            f'<td>{_tv_eok(r.get("거래대금",0))}</td>'
            f'<td class="{cls}">{_sign(chg)}</td></tr>'
        )
    return (
        '<div class="section-title">💰 거래대금 Top20</div>'
        '<div class="tbl-wrap"><table>'
        f'<thead>{header}</thead><tbody>{"".join(body_rows)}</tbody>'
        '</table></div>'
    )


def _section_table_intersection(rows: list) -> str:
    if not rows:
        return '<div class="section-title">🔀 교집합 후보</div><div class="empty-msg">교집합 없음</div>'
    header = "<tr><th>#</th><th>종목명</th><th>코드</th><th>등락률</th><th>거래대금</th></tr>"
    body_rows = []
    for i, r in enumerate(rows, 1):
        chg = float(r.get("등락률", 0))
        cls = "td-pos" if chg >= 0 else "td-neg"
        body_rows.append(
            f"<tr><td>{i}</td>"
            f'<td class="td-name">{_e(r.get("종목명",""))}</td>'
            f'<td class="td-code">{_e(r.get("종목코드",""))}</td>'
            f'<td class="{cls}">{_sign(chg)}</td>'
            f'<td>{_tv_eok(r.get("거래대금",0))}</td></tr>'
        )
    return (
        '<div class="section-title">🔀 교집합 (상승률Top20 ∩ 거래대금Top20)</div>'
        '<div class="tbl-wrap"><table>'
        f'<thead>{header}</thead><tbody>{"".join(body_rows)}</tbody>'
        '</table></div>'
    )


def _section_rejected(rejected: list) -> str:
    if not rejected:
        return ""
    parts = ['<div class="section-title">🚫 탈락 종목</div><div class="rejected-list">']
    for r in rejected:
        parts.append(
            f'<div class="rejected-item">'
            f'<div class="r-name">{_e(r.get("name",""))} ({_e(r.get("code",""))})</div>'
            f'<div class="r-reason">{_e(r.get("reason",""))}</div>'
            f'</div>'
        )
    parts.append("</div>")
    return "".join(parts)


# ─── 메인 HTML 조립 ──────────────────────────────────────────────────────────

def _build_html(data: dict) -> str:
    meta = data.get("metadata", {})
    date     = _e(meta.get("date", "-"))
    snap     = _e(meta.get("snapshot_time", "-"))
    run_type = _e(meta.get("run_type", "-"))

    core = data.get("core_candidates", [])
    rejected = data.get("rejected_candidates", [])
    today_str = meta.get("date", "")
    body_parts = [
        _section_header(data),
        _section_market_summary(data),
        _section_leading_sectors(data.get("leading_sectors", [])),
        _section_sector_calendar(data.get("sector_calendar", {}), today_str),
        _section_core_candidates(core),
    ]
    if not core:
        body_parts.append(_section_watch_candidates(rejected))
    body_parts += [
        _section_table_intersection(data.get("intersection_candidates", [])),
        _section_rejected_summary(rejected),
        _section_table_gainers(data.get("gainers_top20", [])),
        _section_table_tv(data.get("trading_value_top20", [])),
    ]
    body = "\n".join(body_parts)

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>종가베팅 스캔 {date} {snap} ({run_type})</title>
<style>{_css()}</style>
</head>
<body>
<div class="wrap">
{body}
<div style="text-align:center;color:var(--muted);font-size:11px;margin-top:24px;padding:16px 0;">
  korea-close-betting-bot &middot; {date} {snap}
</div>
</div>
</body>
</html>"""
