# scripts/dashboard.py
"""HTML 대시보드 생성 모듈 (GitHub Pages 배포용)"""

import logging
import re as _re
from html import escape
from itertools import groupby
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_REPORT_PAT = _re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{4})\.html$")
_LABEL_MAP  = {"1450": "1차 (14:50)", "1750": "2차 (17:50)"}


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
        output_path = Path(output_path)
        current_filename = output_path.name
        nav_entries = _scan_report_entries(output_path.parent, current_filename)
        html = _build_html(report_data, nav_entries, current_filename)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html, encoding="utf-8")
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


def _scan_report_entries(reports_dir: Path, current_filename: str) -> list:
    """reports/ 내 YYYY-MM-DD_HHMM.html 목록 (현재 파일 포함) 최신순."""
    seen: set = set()
    entries: list = []

    if Path(reports_dir).exists():
        for f in Path(reports_dir).glob("*.html"):
            m = _REPORT_PAT.match(f.name)
            if m and f.name not in seen:
                seen.add(f.name)
                label = _LABEL_MAP.get(m.group(2), f"수동 {m.group(2)[:2]}:{m.group(2)[2:]}")
                entries.append((m.group(1), m.group(2), label, f.name))

    m = _REPORT_PAT.match(current_filename)
    if m and current_filename not in seen:
        label = _LABEL_MAP.get(m.group(2), f"수동 {m.group(2)[:2]}:{m.group(2)[2:]}")
        entries.append((m.group(1), m.group(2), label, current_filename))

    entries.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return entries


def _date_map_from_entries(entries: list) -> dict:
    """nav_entries → {date_str: best_filename} (1750 > 1450 > 수동 최신 우선)."""
    by_date: dict = {}
    for d, snap, label, fname in entries:
        by_date.setdefault(d, []).append((snap, fname))
    result = {}
    for d, snaps in by_date.items():
        best = None
        for snap, fname in sorted(snaps, reverse=True):
            if snap == "1750":
                best = fname; break
            if best is None or snap == "1450":
                best = fname
        result[d] = best or snaps[-1][1]
    return result


def _nav_bar(entries: list, current_filename: str) -> str:
    """날짜 히스토리 드롭다운 네비게이션 바."""
    if not entries:
        return ""

    filenames = [e[3] for e in entries]
    try:
        cur_idx = filenames.index(current_filename)
    except ValueError:
        cur_idx = -1

    prev_file = filenames[cur_idx + 1] if cur_idx >= 0 and cur_idx + 1 < len(filenames) else None
    next_file = filenames[cur_idx - 1] if cur_idx > 0 else None

    prev_btn = f'<a href="{prev_file}" class="nav-btn">&#9664; 이전</a>' if prev_file else '<span class="nav-btn disabled">&#9664; 이전</span>'
    next_btn = f'<a href="{next_file}" class="nav-btn">다음 &#9654;</a>' if next_file else '<span class="nav-btn disabled">다음 &#9654;</span>'

    opts = []
    for date_str, group in groupby(entries, key=lambda x: x[0]):
        opts.append(f'<optgroup label="{date_str}">')
        for (d, snap, label, fname) in group:
            sel = " selected" if fname == current_filename else ""
            opts.append(f'<option value="{fname}"{sel}>{d} {label}</option>')
        opts.append("</optgroup>")
    opts_html = "".join(opts)

    return f"""<div class="hist-nav">
  {prev_btn}
  <select class="hist-select" onchange="if(this.value) location.href=this.value">{opts_html}</select>
  {next_btn}
  <a href="../index.html" class="nav-btn">&#8801; 목록</a>
</div>
"""


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
.regime-bull {
    display: inline-block;
    background: var(--green-bg);
    color: var(--green);
    border: 1px solid var(--green);
    border-radius: 6px;
    padding: 4px 16px;
    font-size: 15px;
    font-weight: 700;
}
.regime-bear {
    display: inline-block;
    background: rgba(255,80,80,0.1);
    color: var(--red);
    border: 1px solid var(--red);
    border-radius: 6px;
    padding: 4px 16px;
    font-size: 15px;
    font-weight: 700;
}
.regime-neutral {
    display: inline-block;
    background: var(--bg2);
    color: var(--muted);
    border: 1px solid var(--border);
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
.cal-cell.has-report { cursor: pointer; }
.cal-cell.has-report:hover { background: var(--bg3); border-color: var(--blue); }
.cal-cell.has-report .cal-day { color: var(--blue); font-weight: 700; }
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

/* ── 오늘 환경 / 핵심 신호 박스 ── */
.top-boxes {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    margin-bottom: 16px;
}
.info-box {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 16px;
}
.info-box-title {
    font-size: 11px; font-weight: 700; color: var(--muted);
    text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 10px;
}
.env-row {
    display: flex; justify-content: space-between; align-items: center;
    padding: 5px 0; border-bottom: 1px solid var(--bg3); font-size: 13px;
}
.env-row:last-child { border-bottom: none; }
.env-label { color: var(--muted); font-size: 12px; }
.env-val   { font-weight: 600; }
.signal-row { padding: 7px 0; border-bottom: 1px solid var(--bg3); }
.signal-row:last-child { border-bottom: none; }
.signal-num { font-size: 20px; font-weight: 700; color: var(--blue); }
.signal-interp { font-size: 11px; color: var(--muted); margin-top: 1px; }

/* ── 종목 패널 (좌/우 레이아웃) ── */
.stock-layout {
    display: grid;
    grid-template-columns: 270px 1fr;
    gap: 12px;
    margin-bottom: 16px;
    align-items: start;
}
.stock-list {
    display: flex; flex-direction: column; gap: 6px;
    max-height: 680px; overflow-y: auto;
}
.list-card {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-left: 3px solid transparent;
    border-radius: 8px;
    padding: 10px 12px;
    cursor: pointer;
    transition: border-color 0.12s, background 0.12s;
}
.list-card:hover  { background: var(--bg3); }
.list-card.active { background: var(--bg3); border-left-color: var(--blue); border-color: var(--blue); }
.list-card.pat-break { border-left-color: var(--green); }
.list-card.pat-hold  { border-left-color: var(--blue); }
.list-card.pat-watch { border-left-color: var(--yellow); }
.lc-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 4px; }
.lc-name { font-weight: 700; font-size: 14px; }
.lc-code { color: var(--muted); font-size: 11px; margin-left: 4px; }
.priority-badge { font-size: 10px; font-weight: 700; border-radius: 3px; padding: 1px 6px; white-space: nowrap; }
.priority-first { background: var(--yellow-bg); color: var(--yellow); }
.priority-watch { background: var(--bg3); color: var(--muted); }
.lc-stats { font-size: 12px; color: var(--muted); margin-bottom: 3px; }
.lc-summary { font-size: 11px; color: var(--text); background: var(--bg3); border-radius: 3px; padding: 2px 6px; }

/* ── 우측 상세 패널 ── */
.stock-detail {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px 18px;
    min-height: 420px;
}
.detail-name  { font-size: 17px; font-weight: 700; margin-bottom: 4px; }
.detail-meta  { font-size: 12px; color: var(--muted); margin-bottom: 10px; }
.detail-meta span { margin-right: 10px; }
.detail-section { margin-bottom: 14px; }
.detail-section-title {
    font-size: 11px; font-weight: 700; color: var(--muted);
    text-transform: uppercase; letter-spacing: 0.5px;
    border-bottom: 1px solid var(--bg3);
    padding-bottom: 4px; margin-bottom: 7px;
}
.detail-row { display: flex; flex-wrap: wrap; gap: 14px; margin-bottom: 4px; }
.detail-kv .k { color: var(--muted); font-size: 11px; display: block; }
.detail-kv .v { font-weight: 600; font-size: 13px; }
.llm-box { background: var(--bg3); border-radius: 6px; padding: 8px 12px; font-size: 13px; margin-bottom: 12px; }
.str-item  { font-size: 13px; padding: 3px 0; color: var(--green); }
.weak-item { font-size: 13px; padding: 3px 0; color: var(--yellow); }
.chk-item  { font-size: 13px; padding: 3px 0; color: var(--muted); }
.detail-empty { text-align: center; color: var(--muted); padding: 60px 20px; font-size: 14px; }

/* ── 반응형 ── */
@media (max-width: 768px) {
    .top-boxes   { grid-template-columns: 1fr; }
    .stock-layout { grid-template-columns: 1fr; }
    .stock-list  { max-height: 300px; }
}
@media (max-width: 600px) {
    body { padding: 8px; font-size: 13px; }
    .candidate-grid { grid-template-columns: 1fr; }
    .summary-grid   { grid-template-columns: repeat(3, 1fr); }
    thead th, tbody td { padding: 6px 8px; }
}

/* ── 히스토리 네비게이션 바 ── */
.hist-nav {
    position: sticky;
    top: 0;
    z-index: 100;
    background: var(--bg);
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 6px 12px;
    margin: -12px -12px 16px;
}
.hist-select {
    background: var(--bg2);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 4px 8px;
    font-size: 13px;
    cursor: pointer;
    flex: 1;
    max-width: 300px;
}
.nav-btn {
    color: var(--blue);
    text-decoration: none;
    font-size: 12px;
    padding: 4px 10px;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--bg2);
    white-space: nowrap;
    display: inline-block;
}
.nav-btn:hover { background: var(--bg3); }
.nav-btn.disabled { color: var(--muted); pointer-events: none; cursor: default; border-color: var(--bg3); }
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
    regime = market.get("market_regime", "")
    _regime_cfg = {"강세": ("regime-bull", "🟢 강세"), "약세": ("regime-bear", "🔴 약세"), "중립": ("regime-neutral", "⚪ 중립")}
    rcls, rlabel = _regime_cfg.get(regime, ("regime-neutral", "⚪ 중립"))
    regime_badge = f'<span class="{rcls}" style="font-size:14px;padding:3px 12px;margin-left:10px;">{rlabel}</span>' if regime else ""

    return f"""
<div class="page-header">
  <h1>📈 종가베팅 스캔 리포트{regime_badge}</h1>
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


def _section_env_and_signals(data: dict) -> str:
    m        = data.get("market_summary", {})
    core     = data.get("core_candidates", [])
    rejected = data.get("rejected_candidates", [])

    regime       = m.get("market_regime", "")
    tv_1500      = m.get("tv_1500_count", 0)
    g_tv_1500    = m.get("gainers_tv_1500_count", 0)
    inter_n      = m.get("intersection_count", 0)
    core_n       = len(core)
    watch_n      = len([r for r in rejected if "패턴 없음" in r.get("reason", "")])

    _regime_cfg  = {"강세": ("regime-bull", "🟢 강세"), "약세": ("regime-bear", "🔴 약세"), "중립": ("regime-neutral", "⚪ 중립")}
    rcls, rlabel = _regime_cfg.get(regime, ("regime-neutral", "⚪ 중립"))
    regime_html  = f'<span class="{rcls}" style="font-size:13px;padding:2px 10px">{rlabel}</span>'

    inter_interp = "주도주 경쟁 있음" if inter_n > 0 else "주도주 부재"
    tv1500_interp = "자금 집중" if tv_1500 >= 5 else ("보통" if tv_1500 >= 3 else "자금 분산")
    g1500_interp  = "방향성 강함" if g_tv_1500 >= 3 else ("보통" if g_tv_1500 >= 1 else "방향성 약함")

    env_box = f"""<div class="info-box">
  <div class="info-box-title">오늘 환경</div>
  <div class="env-row"><span class="env-label">시장 상태</span><span class="env-val">{regime_html}</span></div>
  <div class="env-row"><span class="env-label">우선 확인</span><span class="env-val" style="color:var(--yellow)">{core_n}종목</span></div>
  <div class="env-row"><span class="env-label">관찰</span><span class="env-val" style="color:var(--muted)">{watch_n}종목</span></div>
</div>"""

    signal_box = f"""<div class="info-box">
  <div class="info-box-title">핵심 신호</div>
  <div class="signal-row">
    <span class="signal-num">{inter_n}개</span>
    <span style="color:var(--muted);font-size:12px;margin-left:8px">교집합 (상승률+거래대금 Top20)</span>
    <div class="signal-interp">→ {inter_interp}</div>
  </div>
  <div class="signal-row">
    <span class="signal-num">{tv_1500}개</span>
    <span style="color:var(--muted);font-size:12px;margin-left:8px">1500억↑ 종목</span>
    <div class="signal-interp">→ {tv1500_interp}</div>
  </div>
  <div class="signal-row">
    <span class="signal-num">{g_tv_1500}개</span>
    <span style="color:var(--muted);font-size:12px;margin-left:8px">상승Top20 중 1500억↑</span>
    <div class="signal-interp">→ {g1500_interp}</div>
  </div>
</div>"""

    return f'<div class="top-boxes">{env_box}{signal_box}</div>\n'


def _section_sector_calendar(calendar: dict, today_str: str, date_map: dict | None = None) -> str:
    if not calendar:
        return ""
    from datetime import date, timedelta
    try:
        today = date.fromisoformat(today_str)
    except ValueError:
        today = date.today()

    date_map = date_map or {}

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
            report_file = date_map.get(d_str)
            cell_cls = "cal-cell cal-today" if is_today else "cal-cell"
            if report_file:
                cell_cls += " has-report"
            onclick = f' onclick="location.href=\'{report_file}\'"' if report_file else ""
            tags = "".join(f'<span class="cal-sector">{_e(s)}</span>' for s in sectors[:4])
            cells.append(
                f'<td class="{cell_cls}"{onclick}>'
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


def _compute_priority(c: dict) -> str:
    pat_label = c.get("patterns", {}).get("pattern_type_label", "없음")
    if c.get("in_inter") or pat_label in ("당일돌파형", "고가횡보형"):
        return "우선확인"
    return "관찰우선"


def _compute_summary_short(c: dict) -> str:
    import re as _re
    news = c.get("news")
    if hasattr(news, "llm_summary") and news.llm_summary:
        s = news.llm_summary
        if s.startswith("재료:"):
            s = s[3:].strip()
        s = _re.sub(r'\s*\([^)]+\)\s*$', '', s).strip()
        return s[:25]
    pat_label = c.get("patterns", {}).get("pattern_type_label", "없음")
    return f"교집합 · {pat_label}" if c.get("in_inter") else pat_label


def _compute_strengths(c: dict) -> list:
    strengths = []
    pat = c.get("patterns", {})
    ind = c.get("indicators", {})
    sup = _supply_info(c.get("supply"))
    if c.get("in_inter"):              strengths.append("상승률·거래대금 교집합")
    if pat.get("new_high_60d"):        strengths.append("60일 신고가 돌파")
    elif pat.get("near_high_60d"):     strengths.append("60일 고점권 근접")
    tv_ratio = pat.get("tv_ratio")
    if tv_ratio is not None and tv_ratio >= 0.4:
        strengths.append(f"거래대금 유지 ratio {tv_ratio:.1f}")
    if ind.get("big_candle"):          strengths.append("장대양봉")
    if ind.get("ma_cluster"):          strengths.append("이평 밀집 후 이탈")
    inst = sup.get("institution_net")
    if inst is not None and inst > 0:  strengths.append(f"기관 순매수 {inst/1e8:+.0f}억")
    frgn = sup.get("foreign_net")
    if frgn is not None and frgn > 0:  strengths.append(f"외국인 순매수 {frgn/1e8:+.0f}억")
    return strengths[:3]


def _compute_weaknesses(c: dict) -> list:
    weaknesses = []
    pat = c.get("patterns", {})
    sup = _supply_info(c.get("supply"))
    tv_ratio = pat.get("tv_ratio")
    if tv_ratio is not None and tv_ratio < 0.4:
        weaknesses.append(f"거래대금 감소 ratio {tv_ratio:.1f}")
    if pat.get("overheated_3d_flag"):              weaknesses.append("기준봉고가 위 과확장")
    if pat.get("post_base_volume_decline_flag"):   weaknesses.append("기준봉 후 대금 감소")
    chg = float(c.get("change_pct", 0))
    if chg > 20:                                   weaknesses.append(f"당일 급등 과열 ({chg:.1f}%)")
    inst = sup.get("institution_net")
    if inst is not None and inst < 0:              weaknesses.append(f"기관 순매도 {inst/1e8:.0f}억")
    frgn = sup.get("foreign_net")
    if frgn is not None and frgn < 0:              weaknesses.append(f"외국인 순매도 {frgn/1e8:.0f}억")
    if sup.get("status") != "ok":                  weaknesses.append("수급 미확인")
    return weaknesses[:3]


def _compute_checkpoints(c: dict) -> list:
    pl = c.get("patterns", {}).get("pattern_type_label", "없음")
    if pl == "당일돌파형":
        return ["내일 거래대금 1500억 유지 여부", "시가 갭업 시 추격 주의", "재료 지속성 확인"]
    if pl == "고가횡보형":
        return ["기준봉 고가 돌파 여부", "거래대금 증가 동반 확인", "눌림 없이 횡보 유지"]
    if pl == "눌림관찰형":
        return ["추가 하락 시 -8% 이내 지지 확인", "거래량 감소 (눌림 정상 여부)", "기준봉 고가 재돌파 시 진입 검토"]
    return ["교집합 유지 여부 확인", "거래대금 1500억 이상 유지"]


def _section_stock_panel(candidates: list, rejected: list) -> str:
    import json as _json

    if not candidates:
        return (
            '<div class="section-title">🎯 핵심 후보</div>'
            '<div class="empty-msg">조건 충족 핵심 후보 없음</div>'
        )

    _PAT_CLS = {"당일돌파형": "pat-break", "고가횡보형": "pat-hold", "눌림관찰형": "pat-watch"}

    list_cards = []
    js_data    = []

    for idx, c in enumerate(candidates):
        pat      = c.get("patterns", {})
        sup      = _supply_info(c.get("supply"))
        raw_news = c.get("news")
        tv       = float(c.get("trading_value", 0))
        chg      = float(c.get("change_pct", 0))

        pat_label  = pat.get("pattern_type_label", "없음")
        offset_str = _OFFSET_LABEL.get(pat.get("base_candle_day_offset"), "-")
        pat_str    = f"{pat_label}({offset_str})" if pat_label != "없음" else "패턴없음"

        priority = _compute_priority(c)
        summary  = _compute_summary_short(c)

        chg_str  = _sign(chg)
        chg_cls  = "pos" if chg >= 0 else "neg"
        tv_str   = _tv_eok(tv)
        in_inter = c.get("in_inter", False)
        new_high = pat.get("new_high_60d", False)
        near_hi  = pat.get("near_high_60d", False)

        tags = []
        if in_inter:  tags.append("★교집합")
        if new_high:  tags.append("🔺신고가")
        elif near_hi: tags.append("📍고점권")
        tags_str = "  ".join(tags)

        pri_html  = (
            '<span class="priority-badge priority-first">우선확인</span>'
            if priority == "우선확인"
            else '<span class="priority-badge priority-watch">관찰우선</span>'
        )
        pat_cls   = _PAT_CLS.get(pat_label, "")
        active_cls = " active" if idx == 0 else ""

        list_cards.append(f"""<div class="list-card {pat_cls}{active_cls}" data-idx="{idx}" onclick="renderDetail({idx})">
  <div class="lc-head">
    <div><span class="lc-name">{_e(c.get('name',''))}</span><span class="lc-code">{_e(c.get('code',''))}</span></div>
    {pri_html}
  </div>
  <div class="lc-stats"><span class="{chg_cls}">{chg_str}</span> · {tv_str} · {_e(pat_str)}{'  ' + _e(tags_str) if tags_str else ''}</div>
  <span class="lc-summary">{_e(summary)}</span>
</div>""")

        llm_summary = ""
        if hasattr(raw_news, "llm_summary") and raw_news.llm_summary:
            llm_summary = raw_news.llm_summary

        high_tag = "🔺신고가" if new_high else ("📍고점권" if near_hi else "")
        sup_inst = sup.get("institution_net")
        sup_frgn = sup.get("foreign_net")

        js_data.append({
            "idx":         idx,
            "name":        c.get("name", ""),
            "code":        c.get("code", ""),
            "market":      c.get("market", ""),
            "chg_str":     chg_str,
            "chg_pos":     chg >= 0,
            "tv_str":      tv_str,
            "pat_str":     pat_str,
            "in_inter":    in_inter,
            "high_tag":    high_tag,
            "priority":    priority,
            "llm_summary": llm_summary,
            "score":       _score_val(c.get("score")),
            "tv_ratio":    f"{pat.get('tv_ratio'):.2f}" if pat.get("tv_ratio") is not None else "-",
            "inst_str":    f"{sup_inst/1e8:+.0f}억" if sup_inst is not None else "-",
            "frgn_str":    f"{sup_frgn/1e8:+.0f}억" if sup_frgn is not None else "-",
            "supply_ok":   sup.get("status") == "ok",
            "strengths":   _compute_strengths(c),
            "weaknesses":  _compute_weaknesses(c),
            "checkpoints": _compute_checkpoints(c),
        })

    cands_json = _json.dumps(js_data, ensure_ascii=False)
    list_html  = "\n".join(list_cards)

    js = f"""<script>
const CANDS = {cands_json};
function renderDetail(idx) {{
  const c = CANDS[idx];
  if (!c) return;
  document.querySelectorAll('.list-card').forEach(el => el.classList.remove('active'));
  const card = document.querySelector('.list-card[data-idx="' + idx + '"]');
  if (card) {{ card.classList.add('active'); card.scrollIntoView({{block:'nearest'}}); }}

  const chgCls  = c.chg_pos ? 'td-pos' : 'td-neg';
  const tagsHtml = (c.in_inter ? '<span class="badge inter">★교집합</span> ' : '') +
                   (c.high_tag ? '<span style="color:var(--yellow)">' + c.high_tag + '</span>' : '');
  const priHtml  = c.priority === '우선확인'
    ? '<span class="priority-badge priority-first">우선확인</span>'
    : '<span class="priority-badge priority-watch">관찰우선</span>';
  const llmHtml  = c.llm_summary ? '<div class="llm-box">' + c.llm_summary + '</div>' : '';
  const strHtml  = c.strengths.length
    ? c.strengths.map(s => '<div class="str-item">✅ ' + s + '</div>').join('')
    : '<div style="color:var(--muted);font-size:13px">해당 없음</div>';
  const wkHtml   = c.weaknesses.length
    ? c.weaknesses.map(w => '<div class="weak-item">⚠️ ' + w + '</div>').join('')
    : '<div style="color:var(--muted);font-size:13px">해당 없음</div>';
  const ckHtml   = c.checkpoints.map(p => '<div class="chk-item">□ ' + p + '</div>').join('');
  const supHtml  = c.supply_ok
    ? '<div class="detail-section"><div class="detail-section-title">수급</div><div style="font-size:13px">기관 <strong>' + c.inst_str + '</strong> &nbsp;/&nbsp; 외국인 <strong>' + c.frgn_str + '</strong></div></div>'
    : '';

  let h = '';
  h += '<div class="detail-name">' + c.name + ' <span style="color:var(--muted);font-size:13px;font-weight:400">(' + c.code + ') ' + c.market + '</span> ' + priHtml + '</div>';
  h += '<div class="detail-meta"><span class="' + chgCls + '">' + c.chg_str + '</span><span>' + c.tv_str + '</span><span>' + c.pat_str + '</span>' + tagsHtml + '</div>';
  h += llmHtml;
  h += '<div class="detail-section"><div class="detail-section-title">지표</div><div class="detail-row">';
  h += '<div class="detail-kv"><span class="k">등락률</span><span class="v ' + chgCls + '">' + c.chg_str + '</span></div>';
  h += '<div class="detail-kv"><span class="k">거래대금</span><span class="v">' + c.tv_str + '</span></div>';
  h += '<div class="detail-kv"><span class="k">패턴</span><span class="v">' + c.pat_str + '</span></div>';
  h += '<div class="detail-kv"><span class="k">점수</span><span class="v">' + c.score + '</span></div>';
  h += '<div class="detail-kv"><span class="k">대금ratio</span><span class="v">' + c.tv_ratio + '</span></div>';
  h += '</div></div>';
  h += '<div class="detail-section"><div class="detail-section-title">강점</div>' + strHtml + '</div>';
  h += '<div class="detail-section"><div class="detail-section-title">약점</div>' + wkHtml + '</div>';
  h += supHtml;
  h += '<div class="detail-section"><div class="detail-section-title">체크포인트</div>' + ckHtml + '</div>';
  document.getElementById('stock-detail').innerHTML = h;
}}
if (CANDS.length > 0) renderDetail(0);
</script>"""

    return (
        f'<div class="section-title">🎯 핵심 후보 {len(candidates)}개</div>\n'
        f'<div class="stock-layout">\n'
        f'  <div class="stock-list" id="stock-list">\n{list_html}\n  </div>\n'
        f'  <div class="stock-detail" id="stock-detail">'
        f'<div class="detail-empty">← 좌측 종목을 선택하세요</div></div>\n'
        f'</div>\n'
        f'{js}\n'
    )


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
    header = "<tr><th>#</th><th>종목명</th><th>섹터</th><th>코드</th><th>시장</th><th>등락률</th><th>거래대금</th></tr>"
    body_rows = []
    for i, r in enumerate(rows, 1):
        chg = float(r.get("등락률", 0))
        cls = "td-pos" if chg >= 0 else "td-neg"
        sector_str = _e(r["sector"]) if r.get("sector") else '<span style="color:var(--muted)">—</span>'
        body_rows.append(
            f"<tr><td>{i}</td>"
            f'<td class="td-name">{_e(r.get("종목명",""))}</td>'
            f'<td style="font-size:11px;color:var(--blue)">{sector_str}</td>'
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
    header = "<tr><th>#</th><th>종목명</th><th>섹터</th><th>코드</th><th>시장</th><th>거래대금</th><th>등락률</th></tr>"
    body_rows = []
    for i, r in enumerate(rows, 1):
        chg = float(r.get("등락률", 0))
        cls = "td-pos" if chg >= 0 else "td-neg"
        sector_str = _e(r["sector"]) if r.get("sector") else '<span style="color:var(--muted)">—</span>'
        body_rows.append(
            f"<tr><td>{i}</td>"
            f'<td class="td-name">{_e(r.get("종목명",""))}</td>'
            f'<td style="font-size:11px;color:var(--blue)">{sector_str}</td>'
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

def _build_html(data: dict, nav_entries: list | None = None, current_filename: str = "") -> str:
    meta = data.get("metadata", {})
    date     = _e(meta.get("date", "-"))
    snap     = _e(meta.get("snapshot_time", "-"))
    run_type = _e(meta.get("run_type", "-"))

    core     = data.get("core_candidates", [])
    rejected = data.get("rejected_candidates", [])
    today_str = meta.get("date", "")
    date_map = _date_map_from_entries(nav_entries) if nav_entries else {}
    body_parts = [
        _section_header(data),
        _section_env_and_signals(data),
        _section_stock_panel(core, rejected),
        _section_watch_candidates(rejected),
        _section_leading_sectors(data.get("leading_sectors", [])),
        _section_sector_calendar(data.get("sector_calendar", {}), today_str, date_map),
        _section_table_intersection(data.get("intersection_candidates", [])),
        _section_rejected_summary(rejected),
        _section_table_gainers(data.get("gainers_top20", [])),
        _section_table_tv(data.get("trading_value_top20", [])),
    ]
    body = "\n".join(body_parts)
    nav_html = _nav_bar(nav_entries, current_filename) if nav_entries else ""

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
{nav_html}
{body}
<div style="text-align:center;color:var(--muted);font-size:11px;margin-top:24px;padding:16px 0;">
  korea-close-betting-bot &middot; {date} {snap}
</div>
</div>
</body>
</html>"""
