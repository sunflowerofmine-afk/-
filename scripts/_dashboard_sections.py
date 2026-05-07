# scripts/_dashboard_sections.py
"""대시보드 섹션 렌더러 + 포맷 헬퍼 — dashboard.py 내부용"""

import json as _json
import re as _re
from datetime import date as _date, timedelta
from html import escape


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
    empty = {
        "status": "failed",
        "institution_net": None, "foreign_net": None, "program_net": None,
        "institution_net_5d": None, "foreign_net_5d": None, "supply_label": "",
    }
    if supply is None:
        return empty
    if hasattr(supply, "status"):       # SupplyData dataclass
        return {
            "status":             supply.status,
            "institution_net":    supply.institution_net,
            "foreign_net":        supply.foreign_net,
            "program_net":        supply.program_net,
            "institution_net_5d": supply.institution_net_5d,
            "foreign_net_5d":     supply.foreign_net_5d,
            "supply_label":       getattr(supply, "supply_label", "") or "",
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


# ─── 상수 ─────────────────────────────────────────────────────────────────────

_OFFSET_LABEL = {0: "당일", 1: "1일전", 2: "2일전", 3: "3일전"}
_PATTERN_TYPE_ORDER = ["당일돌파형", "고가수축형", "고가횡보형", "눌림관찰형", "없음"]
_PATTERN_SECTION_TITLE = {
    "당일돌파형": "🚀 당일 돌파형",
    "고가수축형": "🔶 고가수축형 (거래대금 수축 대기)",
    "고가횡보형": "📊 1~3일전 기준봉 후 고가횡보형",
    "눌림관찰형": "📉 눌림 관찰형",
    "없음":       "📌 기타 (교집합)",
}
_PATTERN_CARD_COLOR = {
    "당일돌파형": "#3fb950",
    "고가수축형": "#e3b341",
    "고가횡보형": "#58a6ff",
    "눌림관찰형": "#d29922",
    "없음":       "#8b949e",
}


# ─── compute 헬퍼 ─────────────────────────────────────────────────────────────

def _compute_priority(c: dict) -> str:
    pat_label = c.get("patterns", {}).get("pattern_type_label", "없음")
    if c.get("in_inter") or pat_label in ("당일돌파형", "고가수축형", "고가횡보형"):
        return "우선확인"
    return "관찰우선"


def _compute_summary_short(c: dict) -> str:
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
    if pl == "고가수축형":
        return ["거래대금 재폭발 동반 돌파 확인", "재점화 조짐(⚡) 여부 확인", "구조붕괴(-8%) 없이 고가권 유지 확인"]
    if pl == "고가횡보형":
        return ["기준봉 고가 돌파 여부", "거래대금 증가 동반 확인", "눌림 없이 횡보 유지"]
    if pl == "눌림관찰형":
        return ["추가 하락 시 -8% 이내 지지 확인", "거래량 감소 (눌림 정상 여부)", "기준봉 고가 재돌파 시 진입 검토"]
    return ["교집합 유지 여부 확인", "거래대금 1500억 이상 유지"]


# ─── 섹션 렌더러 ──────────────────────────────────────────────────────────────

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
    kospi_level  = market.get("kospi_level")
    kosdaq_level = market.get("kosdaq_level")
    kospi_lv_str  = f" <span style='color:var(--muted);font-size:12px'>({kospi_level:,.0f}pt)</span>"  if kospi_level else ""
    kosdaq_lv_str = f" <span style='color:var(--muted);font-size:12px'>({kosdaq_level:,.0f}pt)</span>" if kosdaq_level else ""
    regime         = market.get("market_regime", "")
    market_adl     = market.get("market_adl")
    market_subtype = market.get("market_subtype", "")
    _regime_cfg  = {"강세": ("regime-bull", "🟢 강세"), "약세": ("regime-bear", "🔴 약세"), "중립": ("regime-neutral", "⚪ 중립")}
    _subtype_icon = {"자금집중형": "💰", "전체하락형": "⬇", "혼조형": "↔"}
    rcls, rlabel = _regime_cfg.get(regime, ("regime-neutral", "⚪ 중립"))
    adl_suffix     = f" <span style='font-size:11px;opacity:0.8'>(ADL {market_adl*100:.1f}%)</span>" if market_adl is not None else ""
    subtype_badge  = (f" <span style='font-size:11px;background:rgba(255,255,255,0.15);"
                      f"padding:1px 7px;border-radius:4px'>{_subtype_icon.get(market_subtype,'')} {market_subtype}</span>"
                      if market_subtype else "")
    regime_badge = f'<span class="{rcls}" style="font-size:14px;padding:3px 12px;margin-left:10px;">{rlabel}{adl_suffix}{subtype_badge}</span>' if regime else ""

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
    <span>코스피 {kospi_tv}{kospi_lv_str}</span>
    <span>코스닥 {kosdaq_tv}{kosdaq_lv_str}</span>
  </div>
</div>
"""


def _section_env_and_signals(data: dict) -> str:
    m        = data.get("market_summary", {})
    core     = data.get("core_candidates", [])
    rejected = data.get("rejected_candidates", [])

    regime         = m.get("market_regime", "")
    market_adl     = m.get("market_adl")
    market_subtype = m.get("market_subtype", "")
    market_type    = m.get("market_type", "")
    tv_1500        = m.get("tv_1500_count", 0)
    g_tv_1500      = m.get("gainers_tv_1500_count", 0)
    inter_n        = m.get("intersection_count", 0)
    limit_up_n     = m.get("limit_up_count", 0)
    core_n         = len(core)
    watch_n        = len([r for r in rejected if "패턴 없음" in r.get("reason", "")])

    _regime_cfg   = {"강세": ("regime-bull", "🟢 강세"), "약세": ("regime-bear", "🔴 약세"), "중립": ("regime-neutral", "⚪ 중립")}
    _subtype_icon = {"자금집중형": "💰", "전체하락형": "⬇", "혼조형": "↔"}
    rcls, rlabel  = _regime_cfg.get(regime, ("regime-neutral", "⚪ 중립"))
    adl_suffix    = f" <span style='font-size:11px;opacity:0.75'>(ADL {market_adl*100:.1f}%)</span>" if market_adl is not None else ""
    subtype_str   = f" · {_subtype_icon.get(market_subtype,'')} {market_subtype}" if market_subtype else ""
    regime_html   = f'<span class="{rcls}" style="font-size:13px;padding:2px 10px">{rlabel}{adl_suffix}{subtype_str}</span>'

    inter_interp  = f"상승률·거래대금 Top20 동시 진입 {inter_n}개" if inter_n > 0 else "교집합 없음"
    tv1500_interp = f"전체 {tv_1500}개"
    g1500_interp  = f"상승Top20 중 {g_tv_1500}개 포함"

    market_type_row = (
        f'<div class="env-row"><span class="env-label">장세 유형</span>'
        f'<span class="env-val" style="color:var(--blue);font-size:12px">{_e(market_type)}</span></div>'
    ) if market_type else ""
    limit_up_row = (
        f'<div class="env-row"><span class="env-label">상한가</span>'
        f'<span class="env-val" style="color:var(--yellow)">{limit_up_n}개</span></div>'
    ) if limit_up_n > 0 else ""

    env_box = f"""<div class="info-box">
  <div class="info-box-title">오늘 환경</div>
  <div class="env-row"><span class="env-label">시장 상태</span><span class="env-val">{regime_html}</span></div>
  {market_type_row}
  {limit_up_row}
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
    try:
        today = _date.fromisoformat(today_str)
    except ValueError:
        today = _date.today()

    date_map = date_map or {}

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
        mkt_r = sec.get("market_ratio_pct")
        ratio_str = f'<span class="s-tv">시장{mkt_r:.1f}%</span>' if mkt_r is not None else ""
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
            f'{ratio_str}'
            f"</div>"
            f'<table class="sector-stocks">{stocks_html}</table>'
            f"</div>"
        )
    return (
        '<div class="section-title">🏭 주도 섹터</div>'
        f'<div class="sector-grid">{"".join(cards)}</div>'
    )


def _section_limit_up(market_summary: dict) -> str:
    limit_up_list  = market_summary.get("limit_up_list", [])
    limit_up_count = market_summary.get("limit_up_count", 0)
    if not limit_up_list or limit_up_count == 0:
        return ""
    rows_html = ""
    for r in limit_up_list:
        chg = float(r.get("등락률", 0))
        rows_html += (
            f"<tr>"
            f'<td class="td-name">{_e(r.get("종목명",""))}</td>'
            f'<td class="td-code">{_e(str(r.get("종목코드","")))}</td>'
            f'<td>{_e(r.get("시장",""))}</td>'
            f'<td class="td-pos">{_sign(chg)}</td>'
            f'<td>{_tv_eok(r.get("거래대금",0))}</td>'
            f"</tr>"
        )
    return (
        f'<div class="section-title">🚀 상한가 {limit_up_count}개</div>'
        '<div class="tbl-wrap"><table>'
        '<thead><tr><th>종목명</th><th>코드</th><th>시장</th><th>등락률</th><th>거래대금</th></tr></thead>'
        f'<tbody>{rows_html}</tbody>'
        '</table></div>'
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


def _section_stock_panel(candidates: list, rejected: list) -> str:
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
        new_high    = pat.get("new_high_60d", False)
        near_hi     = pat.get("near_high_60d", False)
        near_h52w   = c.get("near_high_52w", False)
        consol_flag = pat.get("consolidation_flag", False)
        pbs_flag    = pat.get("pullback_support_flag", False)

        tags = []
        if in_inter:    tags.append("★교집합")
        if new_high:    tags.append("🔺신고가")
        elif near_hi:   tags.append("📍고점권")
        if near_h52w:   tags.append("📈52w")
        if consol_flag: tags.append("📊기간조정")
        if pbs_flag:    tags.append("↩되돌림지지")
        if pat.get("high_tight_consolidation_flag"): tags.append("🔶고가수축")
        tags_str = "  ".join(tags)

        pri_html  = (
            '<span class="priority-badge priority-first">우선확인</span>'
            if priority == "우선확인"
            else '<span class="priority-badge priority-watch">관찰우선</span>'
        )
        pat_cls    = _PAT_CLS.get(pat_label, "")
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

        high_tag    = "🔺신고가" if new_high else ("📍고점권" if near_hi else "")
        sup_inst    = sup.get("institution_net")
        sup_frgn    = sup.get("foreign_net")
        sup_inst_5d = sup.get("institution_net_5d")
        sup_frgn_5d = sup.get("foreign_net_5d")
        js_data.append({
            "idx":          idx,
            "name":         c.get("name", ""),
            "code":         c.get("code", ""),
            "market":       c.get("market", ""),
            "chg_str":      chg_str,
            "chg_pos":      chg >= 0,
            "tv_str":       tv_str,
            "pat_str":      pat_str,
            "in_inter":     in_inter,
            "high_tag":     high_tag,
            "near_h52w":    near_h52w,
            "consol_flag":  consol_flag,
            "pbs_flag":     pbs_flag,
            "priority":     priority,
            "llm_summary":  llm_summary,
            "score":        _score_val(c.get("score")),
            "tv_ratio":     f"{pat.get('tv_ratio'):.2f}" if pat.get("tv_ratio") is not None else "-",
            "inst_str":     f"{sup_inst/1e8:+.0f}억" if sup_inst is not None else "-",
            "frgn_str":     f"{sup_frgn/1e8:+.0f}억" if sup_frgn is not None else "-",
            "inst_5d_str":  f"{sup_inst_5d/1e8:+.0f}억" if sup_inst_5d is not None else "-",
            "frgn_5d_str":  f"{sup_frgn_5d/1e8:+.0f}억" if sup_frgn_5d is not None else "-",
            "supply_label":  sup.get("supply_label", ""),
            "supply_ok":     sup.get("status") == "ok",
            "prog_net_str":  (f"{c['prog_net_eok']:+.0f}억" if c.get("prog_net_eok") is not None else None),
            "htc_flag":      pat.get("high_tight_consolidation_flag", False),
            "htc_reignite":  pat.get("high_tight_reignite_flag", False),
            "htc_avg_str":   (f"{pat['high_tight_tv_ratio_avg']*100:.0f}%" if pat.get("high_tight_tv_ratio_avg") is not None else ""),
            "htc_chg_str":   (f"{pat['high_tight_close_from_base_high_pct']:+.1f}%" if pat.get("high_tight_close_from_base_high_pct") is not None else ""),
            "strengths":    _compute_strengths(c),
            "weaknesses":   _compute_weaknesses(c),
            "checkpoints":  _compute_checkpoints(c),
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
  const extraTags = (c.near_h52w   ? ' <span style="color:var(--green)">📈52w</span>' : '') +
                   (c.consol_flag ? ' <span style="color:var(--blue)">📊기간조정</span>' : '') +
                   (c.pbs_flag    ? ' <span style="color:var(--purple)">↩되돌림지지</span>' : '') +
                   (c.htc_flag    ? ' <span style="color:var(--yellow)">🔶고가수축' + (c.htc_reignite ? '⚡' : '') + (c.htc_avg_str ? ' ' + c.htc_avg_str : '') + (c.htc_chg_str ? ' ' + c.htc_chg_str : '') + '</span>' : '');
  const tagsHtml = (c.in_inter ? '<span class="badge inter">★교집합</span> ' : '') +
                   (c.high_tag ? '<span style="color:var(--yellow)">' + c.high_tag + '</span>' : '') +
                   extraTags;
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
  const labelHtml = c.supply_label ? '<strong style="color:var(--blue)">[' + c.supply_label + ']</strong> ' : '';
  const progHtml  = c.prog_net_str ? ' &nbsp;<span style="color:var(--muted);font-size:11px">프로그램 <strong style="color:' + (c.prog_net_str.startsWith('+') ? 'var(--green)' : 'var(--red)') + '">' + c.prog_net_str + '</strong></span>' : '';
  const supHtml   = c.supply_ok
    ? '<div class="detail-section"><div class="detail-section-title">수급</div><div style="font-size:13px">' + labelHtml + '기관 <strong>' + c.inst_str + '</strong><span style="color:var(--muted);font-size:11px">(5d:' + c.inst_5d_str + ')</span> &nbsp;/&nbsp; 외국인 <strong>' + c.frgn_str + '</strong><span style="color:var(--muted);font-size:11px">(5d:' + c.frgn_5d_str + ')</span>' + progHtml + '</div></div>'
    : (c.prog_net_str ? '<div class="detail-section"><div class="detail-section-title">수급</div><div style="font-size:13px">' + progHtml.trim() + '</div></div>' : '');

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

    tv_ratio     = pat.get("tv_ratio")
    tv_ratio_str = f"{tv_ratio:.2f}" if tv_ratio is not None else "-"
    tv_ratio_cls = "val pos" if tv_ratio is not None and tv_ratio >= 0.4 else "val warn" if tv_ratio is not None and tv_ratio >= 0.2 else "val neg"
    status_summary = pat.get("status_summary", "-")
    tv_3d_flow   = pat.get("tv_3d_flow", [])
    tv_3d_str    = " → ".join(_tv_eok(v) for v in tv_3d_flow) if tv_3d_flow else "-"

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


def _section_cumulative_stats(stats: dict) -> str:
    if not stats or not stats.get("total_measured"):
        return ""

    total = stats["total_measured"]

    def _win_rows(data: dict) -> str:
        html = ""
        for key, v in data.items():
            rate  = v["rate"]
            color = "var(--green)" if rate >= 60 else ("var(--yellow)" if rate >= 40 else "var(--red)")
            html += (
                f"<tr>"
                f"<td>{_e(key)}</td>"
                f'<td style="text-align:center">{v["total"]}</td>'
                f'<td style="text-align:center">{v["success"]}</td>'
                f'<td style="text-align:center;color:{color};font-weight:600">{rate}%</td>'
                f"</tr>"
            )
        return html

    def _tbl(title: str, data: dict, head: str, row_fn) -> str:
        if not data:
            return ""
        return (
            f'<div style="flex:1;min-width:220px">'
            f'<div style="font-size:12px;color:var(--muted);margin-bottom:6px">{title}</div>'
            f'<div class="tbl-wrap"><table>'
            f"<thead>{head}</thead>"
            f"<tbody>{row_fn(data)}</tbody>"
            f"</table></div></div>"
        )

    win_head = "<tr><th>구분</th><th>횟수</th><th>성공</th><th>승률</th></tr>"
    html = (
        f'<div class="section-title">📊 누적 승률 ({total}개 측정)</div>'
        f'<div style="display:flex;gap:24px;flex-wrap:wrap;margin-bottom:16px">'
        f'{_tbl("패턴별", stats.get("pattern", {}), win_head, _win_rows)}'
        f'{_tbl("스코어 구간별", stats.get("score_band", {}), win_head, _win_rows)}'
        f"</div>"
    )

    # ── 멀티데이 통계 ────────────────────────────────────────────
    md = stats.get("multiday")
    if not md or md.get("d1_count", 0) == 0:
        return html

    d1c = md.get("d1_count", 0)
    d3c = md.get("d3_count", 0)
    d5c = md.get("d5_count", 0)

    def _avg_rows(data: dict) -> str:
        rows = ""
        for pat, v in data.items():
            mean = v.get("mean", 0)
            cls  = "td-pos" if mean >= 0 else "td-neg"
            rows += (
                f"<tr><td>{_e(pat)}</td>"
                f'<td style="text-align:center">{v.get("count",0)}</td>'
                f'<td class="{cls}" style="text-align:center">{mean:+.2f}%</td></tr>'
            )
        return rows

    avg_head = "<tr><th>패턴</th><th>N</th><th>평균</th></tr>"
    html += (
        f'<div class="section-title">📈 멀티데이 수익률 통계</div>'
        f'<div style="display:flex;gap:24px;flex-wrap:wrap;margin-bottom:16px">'
        f'{_tbl(f"D+1 시가 평균 ({d1c}개)", md.get("d1_open_by_pattern", {}), avg_head, _avg_rows)}'
        f'{_tbl(f"D+3 고가 평균 ({d3c}개)", md.get("d3_mfe_by_pattern",  {}), avg_head, _avg_rows)}'
        f'{_tbl(f"D+5 MFE 평균 ({d5c}개)",  md.get("d5_mfe_by_pattern",  {}), avg_head, _avg_rows)}'
        f"</div>"
    )

    # 결과 타입 분포
    rtypes = md.get("result_type_counts", {})
    if rtypes:
        _COLOR = {
            "즉시성공형":    "var(--green)",
            "눌림후재상승형": "var(--blue)",
            "스윙전환가능형": "var(--yellow)",
            "과열소멸형":    "#f80",
            "실패형":       "var(--red)",
        }
        rt_html = ""
        for label, v in rtypes.items():
            color = _COLOR.get(label, "var(--muted)")
            rt_html += (
                f"<tr>"
                f'<td style="color:{color};font-weight:600">{_e(label)}</td>'
                f'<td style="text-align:center">{v.get("count",0)}</td>'
                f'<td style="text-align:center">{v.get("pct",0):.1f}%</td>'
                f"</tr>"
            )
        html += (
            '<div style="display:flex;gap:24px;flex-wrap:wrap;margin-bottom:16px">'
            '<div style="flex:1;min-width:220px">'
            '<div style="font-size:12px;color:var(--muted);margin-bottom:6px">결과 타입 분포</div>'
            '<div class="tbl-wrap"><table>'
            "<thead><tr><th>타입</th><th>횟수</th><th>비율</th></tr></thead>"
            f"<tbody>{rt_html}</tbody>"
            "</table></div></div>"
        )

    # 교집합 비교
    ic = md.get("inter_comparison", {})
    if ic:
        def _ic_row(label, mean_key, count_key):
            mean  = ic.get(mean_key)
            count = ic.get(count_key, 0)
            if mean is None:
                return ""
            cls = "td-pos" if mean >= 0 else "td-neg"
            return (
                f"<tr><td>{_e(label)}</td>"
                f'<td style="text-align:center">{count}</td>'
                f'<td class="{cls}" style="text-align:center">{mean:+.2f}%</td></tr>'
            )
        ic_html = (
            _ic_row("교집합 D+1시",    "inter_d1_mean",  "inter_d1_count")
            + _ic_row("교집합 D+3고",  "inter_d3_mean",  "inter_d3_count")
            + _ic_row("비교집합 D+1시", "ninter_d1_mean", "ninter_d1_count")
        )
        if ic_html:
            html += (
                '<div style="flex:1;min-width:200px">'
                '<div style="font-size:12px;color:var(--muted);margin-bottom:6px">교집합 성과 비교</div>'
                '<div class="tbl-wrap"><table>'
                "<thead><tr><th>구분</th><th>N</th><th>평균</th></tr></thead>"
                f"<tbody>{ic_html}</tbody>"
                "</table></div></div>"
            )

    html += "</div>"
    return html


def _rpct(v) -> str:
    """수익률 포맷: +1.2% / -3.4% / -"""
    try:
        f = float(v)
        return f"+{f:.1f}%" if f >= 0 else f"{f:.1f}%"
    except (TypeError, ValueError):
        return "-"


def _rpct_cls(v) -> str:
    try:
        return "td-pos" if float(v) >= 0 else "td-neg"
    except (TypeError, ValueError):
        return ""


def _result_type_badge(interim: str | None, final: str | None) -> str:
    """임시/최종 분류 배지. final 확정 시 최종 우선."""
    _COLOR = {
        "즉시성공형":    "var(--green)",
        "눌림후재상승형": "var(--blue)",
        "스윙전환가능형": "var(--yellow)",
        "과열소멸형":    "var(--orange, #f80)",
        "실패형":       "var(--red)",
        "pending":      "var(--muted)",
    }
    if final:
        label = final
        prefix = ""
    elif interim and interim != "pending":
        label = interim
        prefix = "~"  # 임시 표시
    else:
        label = "pending"
        prefix = ""
    color = _COLOR.get(label, "var(--muted)")
    return f'<span style="color:{color};font-size:11px;font-weight:600">{prefix}{_e(label)}</span>'


def _section_review(results: list) -> str:
    measured = [r for r in results if r.get("result") in ("성공", "실패")]
    if not measured:
        return ""

    success_n = sum(1 for r in measured if r["result"] == "성공")
    total_n   = len(measured)
    rate_pct  = success_n / total_n * 100

    fail_counts: dict[str, int] = {}
    for r in measured:
        if r["result"] == "실패":
            reason = r.get("fail_reason") or "혼조"
            fail_counts[reason] = fail_counts.get(reason, 0) + 1

    reason_parts = " · ".join(f"{k} {v}개" for k, v in fail_counts.items())
    rate_color   = "var(--green)" if rate_pct >= 60 else ("var(--yellow)" if rate_pct >= 40 else "var(--red)")

    rows_html = ""
    for r in measured:
        sp       = r.get("signal_price")
        sp_str   = f"{float(sp):,.0f}" if sp and float(sp) > 0 else "-"

        d1o  = r.get("d1_open_pct")  if r.get("d1_open_pct")  is not None else r.get("gap_pct")
        d1h  = r.get("d1_high_pct")
        d1c  = r.get("d1_close_pct") if r.get("d1_close_pct") is not None else r.get("hold_pct")
        d3h  = r.get("d3_high_pct")
        mfe  = r.get("mfe")
        mae  = r.get("mae")

        mfe_day = r.get("mfe_day") or ""
        mae_day = r.get("mae_day") or ""
        mfe_str = f"{_rpct(mfe)}<span style='font-size:9px;color:var(--muted)'>({_e(mfe_day)})</span>" if mfe is not None else "-"
        mae_str = f"{_rpct(mae)}<span style='font-size:9px;color:var(--muted)'>({_e(mae_day)})</span>" if mae is not None else "-"

        alive   = r.get("alive_pullback")
        alive_s = '<span style="color:var(--green);font-weight:600">살아있음</span>' if alive is True else \
                  ('<span style="color:var(--muted)">-</span>' if alive is None else "")

        rows_html += (
            f"<tr>"
            f'<td class="td-name">{_e(r.get("name",""))}</td>'
            f'<td style="font-size:11px;color:var(--blue)">{_e(r.get("pattern_type",""))}</td>'
            f'<td style="font-size:11px">{sp_str}</td>'
            f'<td class="{_rpct_cls(d1o)}">{_rpct(d1o)}</td>'
            f'<td class="{_rpct_cls(d1h)}">{_rpct(d1h)}</td>'
            f'<td class="{_rpct_cls(d1c)}">{_rpct(d1c)}</td>'
            f'<td class="{_rpct_cls(d3h)}">{_rpct(d3h)}</td>'
            f'<td class="{_rpct_cls(mfe)}">{mfe_str}</td>'
            f'<td class="{_rpct_cls(mae) if mae is not None else ""}">{mae_str}</td>'
            f'<td>{_result_type_badge(r.get("interim_result_type"), r.get("final_result_type"))}</td>'
            f'<td>{alive_s}</td>'
            f"</tr>"
        )

    summary = (
        f'<div style="margin-bottom:8px;font-size:13px">'
        f'총 <strong>{total_n}</strong>개 · '
        f'성공 <strong style="color:{rate_color}">{success_n}개 ({rate_pct:.0f}%)</strong>'
        f'{(" · " + reason_parts) if reason_parts else ""}'
        f'<span style="font-size:11px;color:var(--muted);margin-left:8px">~ = 임시분류 (D+5 전)</span>'
        f'</div>'
    )

    return (
        '<div class="section-title">📋 전일 복기</div>'
        f'{summary}'
        '<div class="tbl-wrap"><table>'
        '<thead><tr>'
        '<th>종목명</th><th>패턴</th><th>진입가</th>'
        '<th>D+1시</th><th>D+1고</th><th>D+1종</th>'
        '<th>D+3고</th><th>MFE</th><th>MAE</th>'
        '<th>분류</th><th>상태</th>'
        '</tr></thead>'
        f'<tbody>{rows_html}</tbody>'
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
