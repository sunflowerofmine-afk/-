# scripts/_dashboard_nav.py
"""대시보드 네비게이션 헬퍼 — dashboard.py 내부용"""

import re as _re
from itertools import groupby
from pathlib import Path

_REPORT_PAT = _re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{4})\.html$")
_LABEL_MAP  = {"1450": "1차 (14:50)", "1750": "2차 (17:50)"}


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

    prev_btn = f'<a id="nav-prev" href="{prev_file}" class="nav-btn">&#9664; 이전</a>' if prev_file else '<span id="nav-prev" class="nav-btn disabled">&#9664; 이전</span>'
    next_btn = f'<a id="nav-next" href="{next_file}" class="nav-btn">다음 &#9654;</a>' if next_file else '<span id="nav-next" class="nav-btn disabled">다음 &#9654;</span>'

    opts = []
    for date_str, group in groupby(entries, key=lambda x: x[0]):
        opts.append(f'<optgroup label="{date_str}">')
        for (d, snap, label, fname) in group:
            sel = " selected" if fname == current_filename else ""
            opts.append(f'<option value="{fname}"{sel}>{d} {label}</option>')
        opts.append("</optgroup>")
    opts_html = "".join(opts)

    # report_list.js 로드 후 prev/next 동적 업데이트
    nav_js = """<script>
(function(){
  var s=document.createElement('script');
  s.src='report_list.js';
  s.onload=function(){
    if(!window.REPORT_LIST)return;
    var cur=location.pathname.split('/').pop()||location.href.split('/').pop();
    cur=cur.split('?')[0].split('#')[0];
    var list=REPORT_LIST,idx=list.indexOf(cur);
    if(idx<0)return;
    var prevF=idx+1<list.length?list[idx+1]:null;
    var nextF=idx>0?list[idx-1]:null;
    function _setBtn(id,file,label){
      var el=document.getElementById(id);if(!el)return;
      if(file){var a=document.createElement('a');a.id=id;a.href=file;a.className='nav-btn';a.innerHTML=label;el.parentNode.replaceChild(a,el);}
    }
    _setBtn('nav-prev',prevF,'&#9664; 이전');
    _setBtn('nav-next',nextF,'다음 &#9654;');
  };
  document.head.appendChild(s);
})();
</script>"""

    return f"""<div class="hist-nav">
  {prev_btn}
  <select class="hist-select" onchange="if(this.value) location.href=this.value">{opts_html}</select>
  {next_btn}
  <a href="../index.html" class="nav-btn">&#8801; 목록</a>
</div>
{nav_js}
"""
