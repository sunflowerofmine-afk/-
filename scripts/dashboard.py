# scripts/dashboard.py
"""HTML 대시보드 생성 모듈 (GitHub Pages 배포용)

공개 API:
  generate_dashboard_html(report_data, output_path, latest_output_path) -> bool
  generate_index_html(reports_dir) -> bool
  build_dashboard_links(report_date, snapshot_time, base_url, latest_name) -> dict

내부 구현은 하위 모듈로 분리:
  _dashboard_css.py      — CSS
  _dashboard_nav.py      — 네비게이션 헬퍼
  _dashboard_sections.py — 섹션 렌더러 + 포맷 헬퍼

복구: dashboard_backup.py 에 원본 보관
"""

import json as _json
import logging
import re as _re
from pathlib import Path
from typing import Optional

from scripts._dashboard_css import _css
from scripts._dashboard_nav import _scan_report_entries, _date_map_from_entries, _nav_bar
from scripts._dashboard_sections import (
    _section_header,
    _section_env_and_signals,
    _section_limit_up,
    _section_stock_panel,
    _section_watch_candidates,
    _section_leading_sectors,
    _section_sector_calendar,
    _section_table_intersection,
    _section_rejected_summary,
    _section_table_gainers,
    _section_table_tv,
)

logger = logging.getLogger(__name__)


# ─── 공개 API ─────────────────────────────────────────────────────────────────

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
    pattern = _re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{4})\.html$")

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

        date_to_url = {}
        for date_str, entries in by_date.items():
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


# ─── 내부 HTML 조립 ───────────────────────────────────────────────────────────

def _build_html(data: dict, nav_entries: list | None = None, current_filename: str = "") -> str:
    meta = data.get("metadata", {})
    date     = meta.get("date", "-")
    snap     = meta.get("snapshot_time", "-")
    run_type = meta.get("run_type", "-")

    core      = data.get("core_candidates", [])
    rejected  = data.get("rejected_candidates", [])
    today_str = meta.get("date", "")
    date_map  = _date_map_from_entries(nav_entries) if nav_entries else {}

    body_parts = [
        _section_header(data),
        _section_env_and_signals(data),
        _section_limit_up(data.get("market_summary", {})),
        _section_stock_panel(core, rejected),
        _section_watch_candidates(rejected),
        _section_leading_sectors(data.get("leading_sectors", [])),
        _section_sector_calendar(data.get("sector_calendar", {}), today_str, date_map),
        _section_table_intersection(data.get("intersection_candidates", [])),
        _section_rejected_summary(rejected),
        _section_table_gainers(data.get("gainers_top20", [])),
        _section_table_tv(data.get("trading_value_top20", [])),
    ]
    body     = "\n".join(body_parts)
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
