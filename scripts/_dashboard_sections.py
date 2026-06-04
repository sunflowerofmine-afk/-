# scripts/_dashboard_sections.py
"""대시보드 섹션 렌더러 + 포맷 헬퍼 — dashboard.py 내부용"""

import json as _json
import re as _re
from datetime import date as _date, timedelta
from html import escape
from pathlib import Path as _Path


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
_PATTERN_TYPE_ORDER = ["당일돌파형", "고가수축형", "고가횡보형", "없음"]
_PATTERN_SECTION_TITLE = {
    "당일돌파형": "🚀 당일 돌파형",
    "고가수축형": "🔶 고가수축형 (거래대금 수축 대기)",
    "고가횡보형": "📊 1~3일전 기준봉 후 고가횡보형",
    "없음":       "📌 기타 (교집합)",
}
_PATTERN_CARD_COLOR = {
    "당일돌파형": "#3fb950",
    "고가수축형": "#e3b341",
    "고가횡보형": "#58a6ff",
    "없음":       "#8b949e",
}


# ─── compute 헬퍼 ─────────────────────────────────────────────────────────────

def _compute_status(c: dict, market_regime: str = "중립") -> str:
    """BUY_REVIEW / WATCH_ONLY / NOT_BUYABLE"""
    pat_label = c.get("patterns", {}).get("pattern_type_label", "없음")
    in_inter  = c.get("in_inter", False)
    pat       = c.get("patterns", {})

    # 당일돌파형: 교집합 필수
    if pat_label == "당일돌파형" and in_inter:
        return "BUY_REVIEW"

    # 고가수축형: 교집합 불요, 구조 미붕괴만 확인
    if pat_label == "고가수축형" and not pat.get("structure_broken_flag", False):
        return "BUY_REVIEW"

    # 고가횡보형: 교집합 불요, 구조 미붕괴 + 기준봉 고가 -5% 이내
    if pat_label == "고가횡보형" and not pat.get("structure_broken_flag", False):
        if (pat.get("base_high_gap_pct") or -99) >= -5:
            return "BUY_REVIEW"

    return "WATCH_ONLY"


def _status_badge_html(status: str) -> str:
    if status == "BUY_REVIEW":
        return '<span class="status-badge status-buy">매수검토</span>'
    if status == "NOT_BUYABLE":
        return '<span class="status-badge status-no">매수불가</span>'
    return '<span class="status-badge status-watch">관찰</span>'


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
    if ind.get("ma_cluster"):
        _pl = c.get("patterns", {}).get("pattern_type_label", "없음")
        if _pl == "당일돌파형":
            strengths.append("이평 수렴 → 당일 돌파")
        else:
            strengths.append("이평 수렴")
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
    pl  = c.get("patterns", {}).get("pattern_type_label", "없음")
    pat = c.get("patterns", {})
    sup = _supply_info(c.get("supply"))
    tv_ratio  = pat.get("tv_ratio")
    gap_pct   = pat.get("base_high_gap_pct")
    chg       = float(c.get("change_pct", 0))
    inst_net  = sup.get("institution_net")

    if pl == "당일돌파형":
        tv_msg = (f"내일 거래대금 ratio {tv_ratio:.2f} 유지 → 1500억 이상 확인"
                  if tv_ratio is not None else "내일 거래대금 1500억 유지 여부")
        gap_msg = (f"당일 {chg:.0f}% 급등 — 시가 갭업 추격 금지"
                   if chg > 15 else "시가 갭업 시 추격 주의")
        sup_msg = (f"기관 {inst_net/1e8:+.0f}억 매수 — 내일 수급 지속 확인"
                   if inst_net is not None and inst_net > 0 else "재료 지속성 및 외인·기관 수급 확인")
        return [tv_msg, gap_msg, sup_msg]

    if pl == "고가수축형":
        htc_avg  = pat.get("high_tight_tv_ratio_avg")
        htc_chg  = pat.get("high_tight_close_from_base_high_pct")
        ratio_msg = (f"현재 대금ratio 평균 {htc_avg*100:.0f}% — 재폭발 신호 대기"
                     if htc_avg is not None else "거래대금 재폭발 동반 돌파 확인")
        pos_msg   = (f"기준봉 고가 {htc_chg:+.1f}% — 재점화(⚡) 조짐 포착 시 진입"
                     if htc_chg is not None else "재점화 조짐(⚡) 여부 확인")
        return [ratio_msg, pos_msg, "구조붕괴(-8%) 없이 고가권 유지 확인"]

    if pl == "고가횡보형":
        gap_msg  = (f"기준봉 고가 {gap_pct:+.1f}% — 돌파 여부 확인"
                    if gap_pct is not None else "기준봉 고가 돌파 여부")
        tv_msg   = (f"거래대금 ratio {tv_ratio:.2f} — 증가 동반 돌파 필수"
                    if tv_ratio is not None else "거래대금 증가 동반 확인")
        return [gap_msg, tv_msg, "눌림 없이 횡보 유지 확인"]

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

    def _idx_chg(chg):
        if chg is None: return ""
        color = "var(--red)" if chg >= 0 else "var(--green)"
        sign = "+" if chg >= 0 else ""
        return f' <span style="color:{color};font-size:12px;font-weight:700">{sign}{chg:.2f}%</span>'

    kospi_chg_html  = _idx_chg(market.get("kospi_chg"))
    kosdaq_chg_html = _idx_chg(market.get("kosdaq_chg"))
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

    inter_n  = market.get("intersection_count", 0)
    tv_1500  = market.get("tv_1500_count", 0)
    core_n   = len(data.get("core_candidates", []))
    key_nums = (
        f'<div style="margin-top:8px;font-size:15px;font-weight:600;letter-spacing:0.5px">'
        f'<span style="color:var(--yellow)">교집합 {inter_n}개</span>'
        f'<span style="color:var(--muted);margin:0 10px">/</span>'
        f'<span style="color:var(--green)">1500억↑ {tv_1500}개</span>'
        f'<span style="color:var(--muted);margin:0 10px">/</span>'
        f'<span style="color:var(--blue)">핵심후보 {core_n}개</span>'
        f'</div>'
    )

    return f"""
<div class="page-header">
  <h1>📈 종가베팅 스캔 리포트{regime_badge}</h1>
  {key_nums}
  <div class="meta" style="margin-top:6px;">
    <span>📅 {date}</span>
    <span>기준시각 {base_time}</span>
    <span>실행시각 {run_time_hm} KST</span>
    <span>분류 {run_type}</span>
  </div>
  <div class="meta" style="margin-top:4px;">
    <span>코스피 {kospi_tv}{kospi_lv_str}{kospi_chg_html}</span>
    <span>코스닥 {kosdaq_tv}{kosdaq_lv_str}{kosdaq_chg_html}</span>
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
    watch_n        = len(data.get("watch_candidates", []))

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
        sector = _e(r.get("sector", ""))
        rows_html += (
            f"<tr>"
            f'<td class="td-name">{_e(r.get("종목명",""))}</td>'
            f'<td class="td-code">{_e(str(r.get("종목코드","")))}</td>'
            f'<td>{_e(r.get("시장",""))}</td>'
            f'<td style="color:var(--muted);font-size:0.82em">{sector}</td>'
            f'<td class="td-pos">{_sign(chg)}</td>'
            f'<td>{_tv_eok(r.get("거래대금",0))}</td>'
            f"</tr>"
        )
    return (
        f'<div class="section-title">🚀 상한가 {limit_up_count}개</div>'
        '<div class="tbl-wrap"><table>'
        '<thead><tr><th>종목명</th><th>코드</th><th>시장</th><th>섹터</th><th>등락률</th><th>거래대금</th></tr></thead>'
        f'<tbody>{rows_html}</tbody>'
        '</table></div>'
    )


def _section_watch_panel(watch_candidates: list, market_regime: str = "중립") -> str:
    """시장 상황으로 핵심후보에서 제외된 관심 후보 리스트."""
    if not watch_candidates:
        return ""

    _max_cfg = {"강세": 5, "중립": 3, "약세": 2}
    max_n = _max_cfg.get(market_regime, 3)

    rows_html = ""
    for c in watch_candidates:
        pat_label = c.get("patterns", {}).get("pattern_type_label", "없음")
        chg = float(c.get("change_pct", 0))
        chg_cls = "td-pos" if chg >= 0 else "td-neg"
        tv = float(c.get("trading_value", 0))
        status = _compute_status(c, market_regime)
        in_inter = c.get("in_inter", False)
        inter_badge = '<span class="badge inter" style="font-size:10px">교집합</span> ' if in_inter else ""
        status_html = _status_badge_html(status)
        sector = _e(c.get("sector", ""))
        rows_html += (
            f"<tr>"
            f'<td class="td-name">{inter_badge}{_e(c.get("name",""))}'
            f'<br><small class="td-code" style="font-size:10px">{_e(c.get("code",""))} {_e(c.get("market",""))}</small></td>'
            f"<td>{status_html}</td>"
            f'<td style="color:{_PATTERN_CARD_COLOR.get(pat_label,"#8b949e")};font-weight:600">{_e(pat_label)}</td>'
            f'<td class="{chg_cls}">{_sign(chg)}</td>'
            f"<td>{_tv_eok(tv)}</td>"
            f'<td style="color:var(--blue);font-weight:700">{_score_val(c.get("score"))}점</td>'
            f'<td style="color:var(--muted);font-size:12px">{sector}</td>'
            f"</tr>"
        )

    return (
        f'<details open><summary class="section-title" style="cursor:pointer;list-style:none">'
        f'📋 관심 후보 {len(watch_candidates)}개 '
        f'<span style="font-size:12px;color:var(--muted);font-weight:400">'
        f'(시장 {market_regime} → 핵심후보 상한 {max_n}개 초과분)</span>'
        f'</summary>\n'
        f'<div class="tbl-wrap"><table>'
        f'<thead><tr><th>종목</th><th>등급</th><th>패턴</th><th>등락률</th><th>거래대금</th><th>점수</th><th>섹터</th></tr></thead>'
        f'<tbody>{rows_html}</tbody>'
        f'</table></div></details>'
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

    items = " ".join(
        f'<span class="status-badge status-no">매수불가</span> {_e(k)}: {v}개'
        for k, v in counts.items()
    )

    watches = sorted(
        [r for r in rejected if "패턴 없음" in r.get("reason", "")],
        key=lambda x: x.get("trading_value", 0),
        reverse=True,
    )[:5]

    watch_html = ""
    if watches:
        cards_html = ""
        for c in watches:
            tv  = c.get("trading_value", 0)
            chg = float(c.get("change_pct", 0))
            chg_cls = "pos" if chg >= 0 else "neg"
            cards_html += (
                f'<div class="watch-card">'
                f'<div><span class="name">{_e(c.get("name",""))}</span>'
                f'<span class="code">{_e(c.get("code",""))}</span></div>'
                f'<div style="font-size:11px;color:var(--muted);margin:2px 0">패턴 미충족 · 거래대금 상위</div>'
                f'<div class="watch-body">'
                f'거래대금 <strong>{_tv_eok(tv)}</strong>'
                f' &nbsp;·&nbsp; 등락률 <strong class="{chg_cls}">{_sign(chg)}</strong>'
                f'</div></div>'
            )
        watch_html = (
            f'<details style="margin-top:10px">'
            f'<summary style="cursor:pointer;font-size:13px;color:var(--blue);user-select:none">'
            f'📋 패턴미충족 주시 {len(watches)}개 (거래대금 상위) — 매수 후보 아님</summary>'
            f'<div class="watch-grid" style="margin-top:8px">{cards_html}</div>'
            f'</details>'
        )

    return (
        f'<div class="section-title">🚫 탈락 요약</div>'
        f'<div class="rejected-summary">{items}</div>'
        f'{watch_html}'
    )


def _section_stock_panel(candidates: list, rejected: list, market_regime: str = "중립") -> str:
    if not candidates:
        return (
            '<div class="section-title">🎯 핵심 후보</div>'
            '<div class="empty-msg">조건 충족 핵심 후보 없음</div>'
        )

    _PAT_CLS = {"당일돌파형": "pat-break", "고가수축형": "pat-htc", "고가횡보형": "pat-hold"}

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
        if pat_label != "없음":
            pat_str = f"{pat_label}({offset_str})"
        elif (pat.get("today_close_from_high_pct") or 0) <= -5.0:
            pat_str = "5%↑윗꼬리"
        else:
            pat_str = "패턴없음"

        status  = _compute_status(c, market_regime)
        summary = _compute_summary_short(c)

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
        if tv >= 1_000_000_000_000: tags.append("💰1조+")
        if consol_flag: tags.append("📊기간조정")
        if pbs_flag:    tags.append("↩되돌림지지")
        if pat.get("high_tight_consolidation_flag"): tags.append("🔶고가수축")
        prog_net = c.get("prog_net_eok")
        if prog_net is not None and prog_net > 0: tags.append("💹프로그램매수")
        tags_str = "  ".join(tags)

        pri_html   = _status_badge_html(status)
        pat_cls    = _PAT_CLS.get(pat_label, "")
        active_cls = " active" if idx == 0 else ""
        score_str  = _score_val(c.get("score"))

        list_cards.append(f"""<div class="list-card {pat_cls}{active_cls}" data-idx="{idx}" onclick="renderDetail({idx})">
  <div class="lc-head">
    <div><span class="lc-name">{_e(c.get('name',''))}</span><span class="lc-code">{_e(c.get('code',''))}</span></div>
    {pri_html}
  </div>
  <div class="lc-stats"><span class="{chg_cls}">{chg_str}</span> · {tv_str} · {_e(pat_str)} · <span style="color:var(--blue);font-weight:700">{score_str}점</span>{'  ' + _e(tags_str) if tags_str else ''}</div>
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
            "tv_1t":        tv >= 1_000_000_000_000,
            "consol_flag":  consol_flag,
            "pbs_flag":     pbs_flag,
            "status":       status,
            "llm_summary":  llm_summary,
            "score":        _score_val(c.get("score")),
            "score_news":   getattr(c.get("score"), "news_score",          "-"),
            "score_tv":     getattr(c.get("score"), "trading_value_score", "-"),
            "score_candle": getattr(c.get("score"), "candle_score",        "-"),
            "score_supply": getattr(c.get("score"), "supply_score",        "-"),
            "score_bonus":  getattr(c.get("score"), "bonus_score",         "-"),
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
            "entry_ref_str": (f"{c['entry_reference_price']:,.0f}원" if c.get("entry_reference_price") else "-"),
            "price_src":     c.get("price_source", ""),
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
                   (c.tv_1t       ? ' <span style="color:var(--yellow)">💰1조+</span>' : '') +
                   (c.consol_flag ? ' <span style="color:var(--blue)">📊기간조정</span>' : '') +
                   (c.pbs_flag    ? ' <span style="color:var(--purple)">↩되돌림지지</span>' : '') +
                   (c.htc_flag    ? ' <span style="color:var(--yellow)">🔶고가수축' + (c.htc_reignite ? '⚡' : '') + (c.htc_avg_str ? ' ' + c.htc_avg_str : '') + (c.htc_chg_str ? ' ' + c.htc_chg_str : '') + '</span>' : '');
  const tagsHtml = (c.in_inter ? '<span class="badge inter">교집합</span> ' : '') +
                   (c.high_tag ? '<span style="color:var(--yellow)">' + c.high_tag + '</span>' : '') +
                   extraTags;
  const priHtml  = c.status === 'BUY_REVIEW'
    ? '<span class="status-badge status-buy">매수검토</span>'
    : (c.status === 'NOT_BUYABLE'
      ? '<span class="status-badge status-no">매수불가</span>'
      : '<span class="status-badge status-watch">관찰</span>');
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
  h += '<div class="detail-kv"><span class="k">점수</span><span class="v" style="color:var(--blue);font-weight:700">' + c.score + '점</span><div style="font-size:11px;color:var(--muted);margin-top:2px">뉴스 ' + c.score_news + ' · 대금 ' + c.score_tv + ' · 캔들 ' + c.score_candle + ' · 수급 ' + c.score_supply + ' · 보너스 ' + c.score_bonus + '</div></div>';
  h += '<div class="detail-kv"><span class="k">신호가</span><span class="v">' + c.entry_ref_str + (c.price_src ? ' <span style="color:var(--muted);font-size:11px">(' + c.price_src + ')</span>' : '') + '</span></div>';
  h += '</div></div>';
  h += '<div class="detail-section"><div class="detail-section-title">강점</div>' + strHtml + '</div>';
  h += '<div class="detail-section"><div class="detail-section-title">약점</div>' + wkHtml + '</div>';
  h += supHtml;
  h += '<div class="detail-section"><div class="detail-section-title">체크포인트</div>' + ckHtml + '</div>';
  document.getElementById('stock-detail').innerHTML = h;
}}
if (CANDS.length > 0) renderDetail(0);
function filterPat(cls) {{
  document.querySelectorAll('.pat-filter-btn').forEach(b => {{
    b.classList.toggle('active', b.dataset.cls === cls);
  }});
  document.querySelectorAll('.list-card').forEach(card => {{
    card.style.display = (!cls || card.classList.contains(cls)) ? '' : 'none';
  }});
  const first = document.querySelector('.list-card' + (cls ? '.' + cls : '') + '[data-idx]');
  if (first) renderDetail(parseInt(first.dataset.idx));
}}
</script>"""

    # ── 패턴별 필터 버튼 ───────────────────────────────────────
    _pat_colors = {"pat-break": "var(--green)", "pat-htc": "var(--yellow)", "pat-hold": "var(--blue)"}
    _pat_labels = [("당일돌파형", "pat-break"), ("고가수축형", "pat-htc"), ("고가횡보형", "pat-hold")]
    _pat_cnt    = {}
    for c in candidates:
        pl = c.get("patterns", {}).get("pattern_type_label", "없음")
        _pat_cnt[pl] = _pat_cnt.get(pl, 0) + 1

    filter_btns = (
        f'<button class="pat-filter-btn active" data-cls="" onclick="filterPat(\'\')">전체 {len(candidates)}개</button>'
    )
    for label, cls in _pat_labels:
        cnt = _pat_cnt.get(label, 0)
        if cnt == 0:
            continue
        color = _pat_colors[cls]
        filter_btns += (
            f'<button class="pat-filter-btn" data-cls="{cls}" '
            f'style="color:{color}" onclick="filterPat(\'{cls}\')">'
            f'{label} {cnt}개</button>'
        )
    filter_bar = f'<div class="pat-filter-bar">{filter_btns}</div>\n'

    return (
        f'<div class="section-title">🎯 핵심 후보 {len(candidates)}개</div>\n'
        f'{filter_bar}'
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


def _short_html(c: dict) -> str:
    """공매도 잔고율 행 — 없으면 빈 문자열"""
    ratio = c.get("short_ratio")
    if ratio is None:
        return ""
    cls = "val neg" if ratio >= 5 else "val warn" if ratio >= 2 else "val"
    return (
        f'<div class="card-row"><span class="lbl">공매도 잔고율(T+2)</span>'
        f'<span class="{cls}">{ratio:.2f}%</span></div>'
    )


def _pension_html(c: dict) -> str:
    """연기금 순매수 행 — 없으면 빈 문자열"""
    net = c.get("pension_net")
    if net is None:
        return ""
    eok = net / 1e8
    cls = "val pos" if eok > 0 else "val neg"
    return (
        f'<div class="card-row"><span class="lbl">연기금 순매수(T-1)</span>'
        f'<span class="{cls}">{eok:+.0f}억</span></div>'
    )


def _position_guide_html(c: dict) -> str:
    """손절남 기준 비중 가이드 행 HTML"""
    chg   = float(c.get("change_pct", 0))
    _sc   = c.get("score")
    score = int(_sc.total_score) if _sc and hasattr(_sc, "total_score") else int(c.get("total_score") or 0)
    inter = c.get("in_inter", False)
    if chg >= 25:
        txt = "⚠ 축소 권고 (급등25%↑ · 승률 50%)"
        cls = "val neg"
    elif score >= 13 or (score >= 10 and inter):
        txt = "강한 후보 (30~50%)"
        cls = "val pos"
    elif score >= 10:
        txt = "일반 후보 (20~30%)"
        cls = "val"
    elif score >= 7:
        txt = "소액 테스트 (10~20%)"
        cls = "val warn"
    else:
        return ""
    return (
        f'<div class="card-row"><span class="lbl">💼 비중 가이드</span>'
        f'<span class="{cls}">{txt}</span></div>'
    )


def _risk_tags_html(c: dict) -> str:
    """리스크 경고 뱃지 HTML — 해당 없으면 빈 문자열"""
    chg   = float(c.get("change_pct", 0))
    _sc   = c.get("score")
    score = int(_sc.total_score) if _sc and hasattr(_sc, "total_score") else int(c.get("total_score") or 0)
    tv    = float(c.get("trading_value", 0))
    tags  = []
    if chg >= 25:
        tags.append("⚠ 급등25%↑")
    if 0 < score <= 9:
        tags.append("⚠ 저스코어")
    if 0 < tv < 250_000_000_000:
        tags.append("⚠ 대금근접")
    if not tags:
        return ""
    badges = "".join(
        f'<span style="background:#fff3cd;color:#856404;border-radius:3px;'
        f'padding:1px 5px;font-size:10px;margin-right:4px">{t}</span>'
        for t in tags
    )
    return f'<div style="margin:4px 0">{badges}</div>'


def _dart_html(c: dict) -> str:
    """DART 공시 섹션 HTML — 없으면 빈 문자열"""
    notices = c.get("dart_notices")
    if notices is None:          # 조회 안 된 상태 (1차 등)
        return ""
    if not notices:              # 조회됐으나 공시 없음
        return '<div style="margin-top:6px;font-size:11px;color:var(--muted)">📋 공시: 없음</div>'
    rows = "".join(
        f'<div style="font-size:11px;padding:2px 0">📋 {_e(n)}</div>'
        for n in notices[:3]
    )
    return f'<div style="margin-top:6px;padding:6px 8px;background:var(--bg3);border-radius:4px">{rows}</div>'


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

    inter_badge = '<span class="badge inter">교집합</span> ' if in_inter else ""
    if c.get("is_nxt"):
        nxt_badge = '<span class="badge nxt">🔵NXT</span> '
    elif c.get("nxt_fetch_ran"):
        nxt_badge = '<span class="badge" style="background:#f0ad4e;color:#5a3e00;font-size:10px">KRX전용 ⚠15:20전</span> '
    else:
        nxt_badge = ""

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
      <div class="name">{inter_badge}{nxt_badge}{_e(c.get('name',''))}</div>
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
    {_short_html(c)}
    {_pension_html(c)}
    {_position_guide_html(c)}
    {_risk_tags_html(c)}
    <div style="margin-top:8px;">{news_html}{llm_html}</div>
    {_dart_html(c)}
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

    # 종목 팝업용 JS 데이터 수집
    _all_stocks: dict[str, dict] = {}  # key: "d1|당일돌파형" → {count, stocks}
    for _key, _src in [("d1", "d1_open_by_pattern"), ("d3", "d3_mfe_by_pattern"), ("d5", "d5_mfe_by_pattern")]:
        for _pat, _v in md.get(_src, {}).items():
            if _v.get("stocks"):
                _all_stocks[f"{_key}|{_pat}"] = _v["stocks"]
    _stocks_json = _json.dumps(_all_stocks, ensure_ascii=False)

    def _avg_rows(data: dict, prefix: str) -> str:
        rows = ""
        for pat, v in data.items():
            mean = v.get("mean", 0)
            cls  = "td-pos" if mean >= 0 else "td-neg"
            cnt  = v.get("count", 0)
            key  = f"{prefix}|{pat}"
            cnt_html = (
                f'<span style="cursor:pointer;color:var(--blue);text-decoration:underline dotted" '
                f'onclick="showMdStocks(\'{_e(key)}\',\'{_e(pat)}\',\'{prefix}\')">{cnt}</span>'
                if key in _all_stocks else str(cnt)
            )
            rows += (
                f"<tr><td>{_e(pat)}</td>"
                f'<td style="text-align:center">{cnt_html}</td>'
                f'<td class="{cls}" style="text-align:center">{mean:+.2f}%</td></tr>'
            )
        return rows

    # 팝업 모달 + JS
    modal_html = """
<div id="md-modal" onclick="if(event.target===this)this.style.display='none'"
  style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:9999;align-items:center;justify-content:center">
  <div style="background:var(--bg2);border:1px solid var(--border);border-radius:10px;
    padding:20px;max-width:500px;width:90%;max-height:70vh;overflow-y:auto;position:relative">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <div id="md-modal-title" style="font-weight:700;font-size:15px"></div>
      <span onclick="document.getElementById('md-modal').style.display='none'"
        style="cursor:pointer;font-size:20px;color:var(--muted);line-height:1">×</span>
    </div>
    <div id="md-modal-body"></div>
  </div>
</div>
<script>
const _MD_STOCKS = """ + _stocks_json + """;
function showMdStocks(key, pat, prefix) {
  const stocks = _MD_STOCKS[key];
  if (!stocks || !stocks.length) return;
  const label = {d1:'D+1 시가', d3:'D+3 고가', d5:'D+5 MFE'}[prefix] || prefix;
  document.getElementById('md-modal-title').textContent = label + ' · ' + pat + ' (' + stocks.length + '개)';
  const rows = stocks.map(s => {
    const cls = s.pct >= 0 ? 'td-pos' : 'td-neg';
    return '<tr><td style="color:var(--muted);font-size:11px">' + s.date + '</td>' +
           '<td style="padding:4px 8px">' + s.name + ' <span style="color:var(--muted);font-size:11px">' + s.code + '</span></td>' +
           '<td class="' + cls + '" style="text-align:right;padding:4px 8px">' + (s.pct >= 0 ? '+' : '') + s.pct.toFixed(2) + '%</td></tr>';
  }).join('');
  document.getElementById('md-modal-body').innerHTML =
    '<table style="width:100%;border-collapse:collapse;font-size:13px">' +
    '<thead><tr><th style="color:var(--muted);font-weight:400;text-align:left">날짜</th>' +
    '<th style="color:var(--muted);font-weight:400;text-align:left">종목</th>' +
    '<th style="color:var(--muted);font-weight:400;text-align:right">수익률</th></tr></thead>' +
    '<tbody>' + rows + '</tbody></table>';
  const m = document.getElementById('md-modal');
  m.style.display = 'flex';
}
</script>"""

    avg_head = "<tr><th>패턴</th><th>종목수</th><th>평균</th></tr>"
    html += (
        f'{modal_html}'
        f'<div class="section-title">📈 멀티데이 수익률 통계</div>'
        f'<div style="display:flex;gap:24px;flex-wrap:wrap;margin-bottom:16px">'
        f'{_tbl(f"D+1 시가 평균 ({d1c}개) ★09:30 이전 매도 기준", md.get("d1_open_by_pattern", {}), avg_head, lambda d: _avg_rows(d, "d1"))}'
        f'{_tbl(f"D+3 고가 평균 ({d3c}개)", md.get("d3_mfe_by_pattern",  {}), avg_head, lambda d: _avg_rows(d, "d3"))}'
        f'{_tbl(f"D+5 MFE 평균 ({d5c}개)",  md.get("d5_mfe_by_pattern",  {}), avg_head, lambda d: _avg_rows(d, "d5"))}'
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
                "<thead><tr><th>구분</th><th>종목수</th><th>평균</th></tr></thead>"
                f"<tbody>{ic_html}</tbody>"
                "</table></div></div>"
            )

    html += "</div>"

    # ── 교집합/비교집합 상세 통계 ─────────────────────────────────
    ifs = stats.get("inter_full_stats", {})
    if ifs:
        _FIELDS = [
            ("D+1 시가", "d1_open"),
            ("D+1 종가", "d1_close"),
            ("D+3 종가", "d3_close"),
            ("MFE",     "mfe"),
            ("MAE",     "mae"),
        ]

        def _st_cell(st: dict | None) -> str:
            if not st:
                return '<td style="text-align:center;color:var(--muted)">-</td>' * 4
            mean = st["mean"]
            med  = st.get("median")
            wr   = st.get("win_rate")
            cls  = "td-pos" if mean >= 0 else "td-neg"
            sl   = st.get("sample_label", "")
            sl_color = "var(--red)" if sl == "데이터부족" else ("var(--yellow)" if sl == "참고용" else "var(--muted)")
            return (
                f'<td style="text-align:center">{st["n"]} <span style="font-size:10px;color:{sl_color}">({_e(sl)})</span></td>'
                f'<td class="{cls}" style="text-align:center">{mean:+.2f}%</td>'
                f'<td style="text-align:center">{f"{med:+.2f}%" if med is not None else "-"}</td>'
                f'<td style="text-align:center">{f"{wr:.1f}%" if wr is not None else "-"}</td>'
            )

        def _group_rows(grp: dict) -> str:
            rows = ""
            for label, field in _FIELDS:
                rows += f"<tr><td>{_e(label)}</td>{_st_cell(grp.get(field))}</tr>"
            return rows

        tbl_head = "<thead><tr><th>항목</th><th>측정수</th><th>평균</th><th>중앙</th><th>승률</th></tr></thead>"
        inter_g  = ifs.get("inter",  {})
        ninter_g = ifs.get("ninter", {})
        html += (
            '<div class="section-title">🔍 교집합/비교집합 상세 비교</div>'
            '<div style="display:flex;gap:24px;flex-wrap:wrap;margin-bottom:16px">'
            '<div style="flex:1;min-width:280px">'
            '<div style="font-size:12px;color:var(--muted);margin-bottom:6px">교집합</div>'
            '<div class="tbl-wrap"><table>'
            f"{tbl_head}<tbody>{_group_rows(inter_g)}</tbody>"
            "</table></div></div>"
            '<div style="flex:1;min-width:280px">'
            '<div style="font-size:12px;color:var(--muted);margin-bottom:6px">비교집합</div>'
            '<div class="tbl-wrap"><table>'
            f"{tbl_head}<tbody>{_group_rows(ninter_g)}</tbody>"
            "</table></div></div>"
            "</div>"
        )

    # ── 신호일 상승률 구간별 통계 ──────────────────────────────────
    cbs = stats.get("change_band_stats", [])
    if cbs:
        def _cb_cells(st: dict | None) -> str:
            if not st:
                return '<td style="text-align:center;color:var(--muted)">-</td>' * 3
            mean = st["mean"]
            med  = st.get("median")
            wr   = st.get("win_rate")
            cls  = "td-pos" if mean >= 0 else "td-neg"
            return (
                f'<td class="{cls}" style="text-align:center">{mean:+.2f}%</td>'
                f'<td style="text-align:center">{f"{med:+.2f}%" if med is not None else "-"}</td>'
                f'<td style="text-align:center">{f"{wr:.1f}%" if wr is not None else "-"}</td>'
            )

        cb_rows = ""
        for b in cbs:
            n  = b["n"]
            sl = b.get("sample_label", "")
            sl_color = "var(--red)" if sl == "데이터부족" else ("var(--yellow)" if sl == "참고용" else "var(--green)")
            cb_rows += (
                f"<tr><td>{_e(b['label'])}</td>"
                f'<td style="text-align:center">{n} <span style="font-size:10px;color:{sl_color}">({_e(sl)})</span></td>'
                + _cb_cells(b.get("d1_open"))
                + _cb_cells(b.get("d3_close"))
                + _cb_cells(b.get("mfe"))
                + "</tr>"
            )
        html += (
            '<div class="section-title">📉 신호일 상승률 구간별 통계</div>'
            '<div style="margin-bottom:16px">'
            '<div class="tbl-wrap"><table>'
            '<thead>'
            '<tr><th rowspan="2">구간</th><th rowspan="2">종목수</th>'
            '<th colspan="3">D+1 시가</th>'
            '<th colspan="3">D+3 종가</th>'
            '<th colspan="3">MFE</th></tr>'
            '<tr><th>평균</th><th>중앙</th><th>승률</th>'
            '<th>평균</th><th>중앙</th><th>승률</th>'
            '<th>평균</th><th>중앙</th><th>승률</th></tr>'
            '</thead>'
            f"<tbody>{cb_rows}</tbody>"
            "</table></div></div>"
        )

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


# ─── 김형준 기법 관찰 후보 섹션 ────────────────────────────────────────────────

_KH_CSS = """
<style>
.kh-notice{background:#1c1c1c;border-left:3px solid #555;padding:10px 14px;
  font-size:12px;color:#888;margin-bottom:14px;border-radius:4px;line-height:1.7;}
.kh-card{background:var(--bg2);border:1px solid #444;border-radius:8px;
  padding:12px 16px;margin-bottom:10px;}
.kh-card-head{font-size:14px;font-weight:600;margin-bottom:6px;color:#ccc;}
.kh-card-body{font-size:12px;color:#888;line-height:1.8;}
.kh-title{color:#888!important;font-size:1rem!important;}
.badge-kh-only{background:#333;color:#aaa;font-size:11px;
  padding:1px 6px;border-radius:4px;margin-left:6px;}
</style>
"""


def _section_kh_candidates(
    key_candidates: list,
    kh_only_candidates: list,
    obs_candidates: list = [],
    scope: str = "top40_only",
) -> str:
    """김형준 기법 탐지 섹션 — 관찰 상태, 매수 신호 아님."""

    kh_from_key = [(c, "스캔") for c in key_candidates
                   if c.get("patterns", {}).get("kim_hyungjun_flag")]
    kh_main = kh_from_key + [( c, "종베외") for c in kh_only_candidates]

    obs_kh = [(c, "기준봉추적") for c in obs_candidates
              if c.get("kim_hyungjun_flag")]

    header = (
        _KH_CSS
        + '<div class="section-title kh-title">📊 김형준 기법 관찰 후보'
        + f' <span style="font-size:12px;color:var(--muted);font-weight:400">· 관찰 상태 · 매수 신호 아님</span></div>'
    )

    def _kh_card(c, source_label):
        pat           = c.get("patterns", {})
        base_offset   = pat.get("kim_hyungjun_base_offset")
        base_tv_r     = pat.get("kim_hyungjun_base_tv_ratio")
        today_tv_r    = pat.get("kim_hyungjun_today_tv_ratio")
        close_vs_base = pat.get("kim_hyungjun_close_vs_base_high_pct")
        above_ma5     = pat.get("kim_hyungjun_above_ma5")
        supply_ok     = pat.get("kim_hyungjun_supply_ok")
        in_inter      = c.get("in_inter", False)
        sector        = c.get("sector", "")

        offset_label = {1: "1일전", 2: "2일전", 3: "3일전"}.get(base_offset, "-")
        tv_r_str     = f"{today_tv_r*100:.0f}%" if today_tv_r is not None else "-"
        close_str    = f"{close_vs_base:+.1f}%" if close_vs_base is not None else "-"
        base_r_str   = f"{base_tv_r}x" if base_tv_r is not None else "-"
        supply_str   = ("기관O" if supply_ok is True
                        else ("기관X" if supply_ok is False else "-"))

        badges = f'<span class="badge na">{_e(source_label)}</span>'
        if in_inter: badges += ' <span class="badge ok">★교집합</span>'

        return (
            f'<div class="kh-card">'
            f'<div class="kh-card-head">'
            f'<b>{_e(c.get("name"))}({_e(c.get("code"))})</b>'
            f' [{_e(c.get("market"))}] {badges} '
            f'{_status_badge_html("WATCH_ONLY")}</div>'
            f'<div class="kh-card-body">'
            f'등락률 {_sign(c.get("change_pct",0))} | 거래대금 {_tv_eok(c.get("trading_value",0))}'
            f'{f" | {_e(sector)}" if sector else ""}<br>'
            f'기준봉 {_e(offset_label)} | 기준봉TV {_e(base_r_str)} | 오늘TV {_e(tv_r_str)}'
            f' | 고가대비 {_e(close_str)} | 5일선 {_badge(above_ma5)} | 수급 {_e(supply_str)}'
            f'</div></div>'
        )

    main_html = "".join(_kh_card(c, lbl) for c, lbl in kh_main) if kh_main else (
        '<p style="color:var(--muted);font-size:13px;padding:8px 0;">오늘 탐지된 김형준 기법 관찰 후보 없음</p>'
    )

    obs_html = ""
    if obs_kh:
        obs_cards = "".join(_kh_card(c, lbl) for c, lbl in obs_kh)
        obs_html = (
            f'<details style="margin-top:10px">'
            f'<summary style="cursor:pointer;font-size:13px;color:var(--blue);user-select:none">'
            f'🔭 기준봉 추적 관찰 중 {len(obs_kh)}개 (거자름 대기)</summary>'
            f'<div style="margin-top:8px">{obs_cards}</div>'
            f'</details>'
        )

    return header + main_html + obs_html


# ─── 기준봉 이후 관찰 풀 섹션 ─────────────────────────────────────────────────

_OBS_NOTICE_CSS = """
<style>
.obs-notice{background:#1a1a2e;border-left:3px solid #4a6fa5;padding:10px 14px;
  font-size:12px;color:#8899aa;margin-bottom:14px;border-radius:4px;line-height:1.7;}
</style>
"""

_OBS_TAG_COLOR = {
    "당일돌파형": "#3fb950", "고가수축형": "#e3b341",
    "고가횡보형": "#58a6ff", "없음": "#8b949e",
}


def _section_recent_base_pool(obs_candidates: list) -> str:
    """기준봉 이후 관찰 후보 — KH 제외(KH 섹션에 표시), 비KH만."""
    non_kh = [c for c in obs_candidates if not c.get("kim_hyungjun_flag")]
    if not non_kh:
        return ""

    def _is_intraday_excluded(c: dict) -> bool:
        gap = c.get("intraday_gap_pct")
        return gap is not None and gap < -5.0

    active   = [c for c in non_kh if not _is_intraday_excluded(c)]
    excluded = [c for c in non_kh if _is_intraday_excluded(c)]

    def _obs_row(c: dict, show_excl_reason: bool = False) -> str:
        pat_label   = c.get("pattern_type_label", "없음")
        label_color = _OBS_TAG_COLOR.get(pat_label, "#8b949e")

        tags = []
        if c.get("is_htc_candidate"):        tags.append('<span class="badge ok">HTC</span>')
        if c.get("is_high_range_candidate"): tags.append('<span class="badge na">횡보</span>')
        if not tags:                         tags.append('<span class="badge na">기준봉</span>')

        close_gap  = c.get("close_from_base_high_pct")
        gap_str    = f"{close_gap:+.1f}%" if close_gap is not None else "-"
        tv_ratio   = c.get("today_tv_ratio")
        tv_r_str   = f"{tv_ratio:.2f}" if tv_ratio is not None else "-"
        base_date  = _e(c.get("base_candle_date") or "-")
        offset     = c.get("base_candle_offset")
        offset_str = _OFFSET_LABEL.get(offset, f"{offset}일전") if offset is not None else "-"

        sector_html = (f" · {_e(c.get('sector', ''))}" if c.get("sector") else "")

        if show_excl_reason:
            ig = c.get("intraday_gap_pct", 0)
            name_extra = f' <span style="color:#f85149;font-size:11px">당일 고가 대비 {ig:.1f}%</span>'
        else:
            name_extra = ""

        return (
            f"<tr>"
            f'<td class="td-name">{_e(c.get("name",""))}{name_extra}'
            f'<br><small class="muted">{_e(c.get("code",""))} · {_e(c.get("market",""))}'
            f'{sector_html}</small></td>'
            f'<td>{_sign(c.get("change_pct",0))}</td>'
            f'<td>{_tv_eok(c.get("trading_value",0))}</td>'
            f'<td style="color:{label_color};font-weight:600">{_e(pat_label)}</td>'
            f'<td>{"&nbsp;".join(tags)}</td>'
            f'<td>{gap_str}</td>'
            f'<td>{tv_r_str}</td>'
            f'<td>{base_date}<br><small class="muted">{offset_str}</small></td>'
            f'<td>{_e(c.get("supply_label","") or "-")}</td>'
            f"</tr>"
        )

    active_rows = "".join(_obs_row(c) for c in active)
    htc_n = sum(1 for c in active if c.get("is_htc_candidate"))
    hrh_n = sum(1 for c in active if c.get("is_high_range_candidate"))

    excl_html = ""
    if excluded:
        excl_rows = "".join(_obs_row(c, show_excl_reason=True) for c in excluded)
        excl_html = (
            f'<details style="margin-top:8px">'
            f'<summary style="cursor:pointer;font-size:12px;color:#f85149;user-select:none">'
            f'⛔ 당일 고가 이격 탈락 {len(excluded)}개 (당일 고가 대비 -5% 초과)</summary>'
            f'<div class="tbl-wrap" style="margin-top:6px"><table>'
            f'<thead><tr>'
            f'<th>종목</th><th>등락률</th><th>거래대금</th><th>패턴</th><th>태그</th>'
            f'<th>고가대비(기준봉)</th><th>TV비율</th><th>기준봉일</th><th>수급</th>'
            f'</tr></thead>'
            f'<tbody>{excl_rows}</tbody>'
            f'</table></div>'
            f'</details>'
        )

    main_table = ""
    if active:
        main_table = (
            '<div class="tbl-wrap"><table>'
            '<thead><tr>'
            '<th>종목</th><th>등락률</th><th>거래대금</th><th>패턴</th><th>태그</th>'
            '<th>고가대비(기준봉)</th><th>TV비율</th><th>기준봉일</th><th>수급</th>'
            '</tr></thead>'
            f'<tbody>{active_rows}</tbody>'
            '</table></div>'
            f'<p style="font-size:11px;color:var(--muted);margin-top:6px">'
            f'총 {len(active)}개 · HTC={htc_n} 횡보={hrh_n}'
            '</p>'
        )
    else:
        main_table = '<p style="color:var(--muted);font-size:13px;padding:8px 0;">당일 조건 통과 관찰 후보 없음</p>'

    return (
        _OBS_NOTICE_CSS
        + '<div class="section-title">🔭 기준봉 이후 관찰 후보</div>'
        + '<div class="obs-notice">'
        + '<b>관찰 상태 · 매수 신호 아님</b> — 최근 기준봉 이후 고가수축/눌림 패턴 추적.'
        + ' 당일 고가 대비 -5% 초과 이격 종목은 탈락 처리.'
        + ' 김형준 기법 관찰 후보는 위 김형준 기법 관찰 후보 섹션 참고.'
        + '</div>'
        + main_table
        + excl_html
    )


# ─── 일반 눌림 관찰 섹션 (완전 별도 체계) ──────────────────────────────────────

def _section_pullback_observer(candidates: list) -> str:
    """일반 눌림 관찰 — 매수 신호 아님. 기존 종가베팅/김형준 기법과 완전 분리."""
    if not candidates:
        return ""

    normal = [c for c in candidates if "구조훼손" not in c.get("observation_tags", [])]
    broken = [c for c in candidates if "구조훼손"     in c.get("observation_tags", [])]

    def _pb_row(c: dict, grayed: bool = False) -> str:
        tags        = c.get("observation_tags", [])
        danger_tags = [t for t in tags if "위험" in t or "구조훼손" in t]
        good_tags   = [t for t in tags if "상대강도 양호" in t or "거래대금건조" in t]
        deep_tag    = [t for t in tags if "깊은눌림" in t]

        danger_html = "  ".join(
            f'<span style="color:#f85149;font-size:11px">{_e(t)}</span>' for t in danger_tags
        )
        good_html = "  ".join(
            f'<span style="color:#58a6ff;font-size:11px">{_e(t)}</span>' for t in good_tags
        )
        deep_html = "  ".join(
            f'<span style="color:#e3b341;font-size:11px">{_e(t)}</span>' for t in deep_tag
        )

        near_badges = []
        if c.get("near_ma5"):      near_badges.append("5일")
        if c.get("near_ma10"):     near_badges.append("10일")
        if c.get("near_ma20"):     near_badges.append("20일")
        if c.get("near_base_mid"): near_badges.append("기준봉중심")
        near_html = "  ".join(f'<span class="badge na">{b}</span>' for b in near_badges)

        drawdown     = c.get("drawdown_from_peak_pct")
        drawdown_str = f"{drawdown:+.1f}%" if drawdown is not None else "-"
        drawdown_cls = "neg" if (drawdown is not None and drawdown < 0) else "pos"

        tag_html = " ".join(filter(None, [deep_html, danger_html, good_html]))
        row_style = ' style="opacity:0.5"' if grayed else ""

        return (
            f"<tr{row_style}>"
            f'<td class="td-name">{_e(c.get("name",""))}'
            f'<br><small class="muted">{_e(c.get("code",""))} · {_e(c.get("market",""))}</small></td>'
            f'<td><small style="color:var(--muted)">{_e(c.get("sector","") or "-")}</small></td>'
            f'<td class="{drawdown_cls}" style="font-weight:600">{drawdown_str}</td>'
            f'<td><small>{_e(c.get("base_date","") or "-")}</small>'
            f'<br><small class="muted">{_sign(c.get("base_change_pct", 0))}</small></td>'
            f'<td><small>{_tv_eok(c.get("base_trading_value", 0))}</small></td>'
            f'<td><small>{_tv_eok(c.get("trading_value", 0))}</small></td>'
            f'<td>{near_html}</td>'
            f'<td>{tag_html}</td>'
            f"</tr>"
        )

    _THEAD = (
        '<thead><tr>'
        '<th>종목</th><th>섹터</th><th>고점대비낙폭</th><th>기준봉</th>'
        '<th>기준봉TV</th><th>현재TV</th><th>지지선근접</th><th>태그</th>'
        '</tr></thead>'
    )

    main_html = ""
    if normal:
        rows = "".join(_pb_row(c) for c in normal)
        main_html = f'<div class="tbl-wrap"><table>{_THEAD}<tbody>{rows}</tbody></table></div>'
    else:
        main_html = '<p style="color:var(--muted);font-size:13px;padding:8px 0;">해당 없음</p>'

    broken_html = ""
    if broken:
        rows = "".join(_pb_row(c, grayed=True) for c in broken)
        broken_html = (
            f'<details style="margin-top:8px">'
            f'<summary style="cursor:pointer;font-size:12px;color:#f85149;user-select:none">'
            f'⚠️ 구조훼손 {len(broken)}개 (참고용)</summary>'
            f'<div class="tbl-wrap" style="margin-top:6px">'
            f'<table>{_THEAD}<tbody>{rows}</tbody></table></div>'
            f'</details>'
        )

    n_total  = len(candidates)
    n_normal = len(normal)
    n_broken = len(broken)

    return (
        f'<details style="margin:16px 0">'
        f'<summary style="cursor:pointer;font-size:15px;font-weight:700;'
        f'user-select:none;padding:10px 0;list-style:none">'
        f'🔍 일반 눌림 관찰 후보 ({n_total}개 · 구조양호 {n_normal}개)</summary>'
        f'<div style="margin-top:8px">'
        f'<div class="obs-notice">'
        f'<b>관찰/검증용 · 매수 신호 아님</b> · '
        f'기존 종가베팅/김형준 기법과 별도 기록 · '
        f'지지선 근접 후 재상승 여부를 추후 D+수익률로 검증 예정'
        f'</div>'
        + main_html
        + broken_html
        + f'<p style="font-size:11px;color:var(--muted);margin-top:6px">'
        f'총 {n_total}개 · 구조양호 {n_normal}개 · 구조훼손(참고) {n_broken}개'
        f'</p>'
        f'</div>'
        f'</details>'
    )


def _section_52w_trend(days: int = 20) -> str:
    """
    data/signals_extra/ 에 누적된 JSON을 읽어 최근 N일
    신고가 근처 비율 트렌드를 테이블로 표시.
    파일이 없으면 빈 문자열 반환.
    """
    extra_dir = _Path("data/signals_extra")
    if not extra_dir.exists():
        return ""

    files = sorted(extra_dir.glob("*_extra.json"), reverse=True)[:days]
    if not files:
        return ""

    rows_html = ""
    for f in sorted(files):
        try:
            data = _json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        stocks = data.get("stocks", {})
        total  = len(stocks)
        if total == 0:
            continue
        near   = sum(1 for v in stocks.values() if v.get("is_near_52w_high"))
        pct    = round(near / total * 100)
        bar_w  = pct
        color  = "var(--green)" if pct >= 60 else ("var(--yellow)" if pct >= 30 else "var(--muted)")
        near_names = ", ".join(
            _e(v.get("name", k))
            for k, v in stocks.items()
            if v.get("is_near_52w_high")
        ) or "-"
        rows_html += (
            f"<tr>"
            f"<td>{_e(data.get('date',''))}</td>"
            f'<td style="text-align:center">{total}</td>'
            f'<td style="text-align:center;color:{color};font-weight:600">{near}</td>'
            f'<td style="text-align:center;color:{color}">{pct}%</td>'
            f'<td style="min-width:80px">'
            f'<div style="background:{color};height:8px;width:{bar_w}%;border-radius:4px"></div></td>'
            f'<td style="font-size:11px;color:var(--muted)">{near_names}</td>'
            f"</tr>"
        )

    if not rows_html:
        return ""

    return (
        f'<details open>'
        f'<summary class="section-title">📈 52주 신고가 근처 비율 추이</summary>'
        f'<div class="tbl-wrap" style="margin-top:8px"><table>'
        f'<thead><tr>'
        f'<th>날짜</th><th>신호수</th><th>신고가근처</th><th>비율</th><th></th><th>종목</th>'
        f'</tr></thead>'
        f'<tbody>{rows_html}</tbody>'
        f'</table></div>'
        f'<p style="font-size:11px;color:var(--muted);margin-top:4px">'
        f'52주 최고가 ±5% 이내 = 신고가 근처. data/signals_extra/ 누적 기준.</p>'
        f'</details>'
    )
