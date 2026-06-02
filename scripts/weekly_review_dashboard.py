# scripts/weekly_review_dashboard.py
"""누적 복기 대시보드 — compliance_history.json + trade_history.json 기반.

사용법:
  python -m scripts.weekly_review_dashboard [--open] [--latest]

  --open   : 생성 후 HTML을 기본 브라우저로 열기
  --latest : trade_analyzer 호환용 (동작에 영향 없음)
"""

import argparse
import json
import webbrowser
from datetime import datetime
from pathlib import Path

_BASE        = Path(__file__).parent.parent
_HISTORY_DIR = _BASE / "data"  / "trade_reviews"
_REPORT_DIR  = _BASE / "reports" / "trade_reviews"
_OUT_HTML    = _REPORT_DIR / "weekly_dashboard.html"

# trade_analyzer.py 와 동일한 태그 설명 (중복 import 없이 복사)
_TAG_DESC = {
    "NON_SIGNAL_TRADE":            "시스템 미신호 종목 매수",
    "SIGNAL_FILE_MISSING":         "신호 파일 없음 (확인불가)",
    "NON_INTERSECTION_TRADE":      "교집합 미포함 종목 매수",
    "NOT_CLOSE_ENTRY":             "종가진입 원칙 위반",
    "D1_CHASE_ENTRY":              "D+1 장초 고점 추격매수",
    "REVERSE_AT_EXIT_ZONE":        "D+1 09:20~09:40 역추격매수",
    "AVERAGING_DOWN":              "물타기 (하락 후 추가매수)",
    "ADDITIONAL_BUY":              "추가매수 (정보용)",
    "RE_ENTRY":                    "재진입 (당일 청산 후 재매수)",
    "NXT_ENTRY":                   "NXT 시간외 체결 (정보용)",
    "AFTER_1750_NXT_ENTRY":        "17:50 이후 NXT 진입 (정보용)",
    "CONDITIONAL_NXT_ENTRY":       "조건부 허용 NXT 진입 (정보용)",
    "NXT_ENTRY_CAUTION":           "NXT 주의 진입",
    "NXT_CHASE_ENTRY":             "NXT 추격 진입",
    "PRICE_REFERENCE_MISSING":     "기준가 확인불가 (정보용)",
    "OVERSIZED_POSITION":          "과대 포지션 (30% 초과)",
    "POSITION_RULE_OK":            "포지션 비중 정상 (정보용)",
    "POSITION_RULE_BROKEN":        "포지션 비중 위반",
    "MAX_POSITION_COUNT_EXCEEDED": "동시 보유 종목 3개 초과",
    "D1_EXIT_RULE_TARGET":         "D+1 청산 대상 (정보용)",
    "D1_EXIT_ON_TIME":             "D+1 09:20~09:40 청산 (정보용)",
    "D1_EXIT_EARLY":               "D+1 09:20 이전 조기 청산 (정보용)",
    "D1_EXIT_DELAYED":             "D+1 09:40 이후 청산 (지연)",
    "D1_EXIT_MISSED":              "D+1 미청산",
    "GAP_DOWN_STOP_REQUIRED":      "갭하락 -3% 손절 대상 (정보용)",
    "GAP_DOWN_STOP_DONE":          "갭하락 손절 이행 (정보용)",
    "GAP_DOWN_STOP_MISSED":        "갭하락 손절 미이행",
    "POST_GAPDOWN_AVERAGING_DOWN": "갭하락 후 추가매수 (강한 위반)",
    "EXTENDED_HOLD_ALLOWED":       "연장 보유 조건 충족 (정보용)",
    "EXTENDED_HOLD_NOT_ALLOWED":   "연장 보유 조건 미충족 (정보용)",
    "UNAUTHORIZED_EXTENDED_HOLD":  "허용 조건 없는 연장 보유",
    "EXTENDED_HOLD_PROFIT":        "연장 보유 후 수익 (정보용)",
    "EXTENDED_HOLD_LOSS":          "연장 보유 후 손실 (정보용)",
    "EXTENDED_HOLD_REVIEW_NEEDED": "연장 보유 판단 불가 (정보용)",
}

_INFO_TAGS = {
    "NXT_ENTRY", "ADDITIONAL_BUY", "PRICE_REFERENCE_MISSING",
    "AFTER_1750_NXT_ENTRY", "D1_EXIT_RULE_TARGET", "NXT_MORNING_EXIT", "D1_EXIT_ON_TIME",
    "D1_EXIT_EARLY", "GAP_DOWN_STOP_REQUIRED", "GAP_DOWN_STOP_DONE",
    "EXTENDED_HOLD_ALLOWED", "EXTENDED_HOLD_PROFIT", "EXTENDED_HOLD_LOSS",
    "EXTENDED_HOLD_NOT_ALLOWED", "EXTENDED_HOLD_REVIEW_NEEDED",
    "POSITION_RULE_OK", "CONDITIONAL_NXT_ENTRY",
}

_TAG_COLOR = {
    "NON_SIGNAL_TRADE":            "#e53935",
    "SIGNAL_FILE_MISSING":         "#9e9e9e",
    "NON_INTERSECTION_TRADE":      "#fb8c00",
    "NOT_CLOSE_ENTRY":             "#e53935",
    "D1_CHASE_ENTRY":              "#e53935",
    "REVERSE_AT_EXIT_ZONE":        "#c62828",
    "AVERAGING_DOWN":              "#e53935",
    "ADDITIONAL_BUY":              "#546e7a",
    "RE_ENTRY":                    "#fb8c00",
    "NXT_ENTRY":                   "#546e7a",
    "AFTER_1750_NXT_ENTRY":        "#0288d1",
    "CONDITIONAL_NXT_ENTRY":       "#00897b",
    "NXT_ENTRY_CAUTION":           "#f57c00",
    "NXT_CHASE_ENTRY":             "#c62828",
    "PRICE_REFERENCE_MISSING":     "#607d8b",
    "OVERSIZED_POSITION":          "#8e24aa",
    "POSITION_RULE_OK":            "#4caf50",
    "POSITION_RULE_BROKEN":        "#8e24aa",
    "MAX_POSITION_COUNT_EXCEEDED": "#c62828",
    "D1_EXIT_RULE_TARGET":         "#546e7a",
    "D1_EXIT_ON_TIME":             "#4caf50",
    "D1_EXIT_DELAYED":             "#fb8c00",
    "D1_EXIT_MISSED":              "#e53935",
    "GAP_DOWN_STOP_REQUIRED":      "#f57c00",
    "GAP_DOWN_STOP_DONE":          "#4caf50",
    "GAP_DOWN_STOP_MISSED":        "#c62828",
    "POST_GAPDOWN_AVERAGING_DOWN": "#c62828",
    "EXTENDED_HOLD_ALLOWED":       "#00897b",
    "EXTENDED_HOLD_NOT_ALLOWED":   "#607d8b",
    "UNAUTHORIZED_EXTENDED_HOLD":  "#e53935",
    "EXTENDED_HOLD_PROFIT":        "#4caf50",
    "EXTENDED_HOLD_LOSS":          "#ef5350",
    "EXTENDED_HOLD_REVIEW_NEEDED": "#607d8b",
}


# ── 유틸 ──────────────────────────────────────────────────────

def _e(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def _fmt_krw(v: float) -> str:
    return f"{v:+,.0f}원" if v >= 0 else f"{v:,.0f}원"

def _fmt_pct(v) -> str:
    if v is None:
        return "-"
    return f"{float(v):+.2f}%"

def _cr_color(v) -> str:
    if v is None:
        return "#888"
    return "#4caf50" if v >= 80 else ("#fb8c00" if v >= 60 else "#ef5350")

def _pnl_color(v) -> str:
    if v is None:
        return "#888"
    return "#4caf50" if v >= 0 else "#ef5350"

def _tag_badge(tag: str) -> str:
    color = _TAG_COLOR.get(tag, "#666")
    desc  = _TAG_DESC.get(tag, tag)
    return (
        f'<span style="display:inline-block;background:{color};color:#fff;'
        f'font-size:11px;padding:2px 6px;border-radius:3px;margin:2px 2px 2px 0;'
        f'white-space:nowrap" title="{_e(desc)}">{_e(tag)}</span>'
    )


# ── 데이터 로드 ───────────────────────────────────────────────

def _load_history() -> list[dict]:
    p = _HISTORY_DIR / "compliance_history.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def _load_trades() -> list[dict]:
    p = _HISTORY_DIR / "trade_history.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def _load_cumulative_stats() -> dict:
    p = _BASE / "reports" / "cumulative_stats.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ── 집계 ──────────────────────────────────────────────────────

def _aggregate(history: list[dict], trades: list[dict]) -> dict:
    if not history:
        return {}

    total_realized   = sum(h.get("total_realized") or 0 for h in history)
    total_stocks_all = sum(h.get("total_stocks") or 0 for h in history)
    cr_list          = [h["compliance_rate"] for h in history if h.get("compliance_rate") is not None]
    avg_cr           = round(sum(cr_list) / len(cr_list), 1) if cr_list else None

    # 전체 태그 집계 (위반만)
    total_tag_counts: dict[str, int] = {}
    for h in history:
        for tag, cnt in (h.get("tag_counts") or {}).items():
            if tag not in _INFO_TAGS:
                total_tag_counts[tag] = total_tag_counts.get(tag, 0) + cnt

    # 주차별 항목 준수율 추세
    item_keys = [
        ("bot_signal_rate",  "봇 신호"),
        ("inter_rate",       "교집합"),
        ("close_entry_rate", "종가 진입"),
        ("d1_exit_rate",     "D+1 청산"),
        ("avg_down_rate",    "물타기 금지"),
        ("pos_limit_rate",   "포지션 한도"),
    ]

    # 종목별 통계 (trade_history 기반)
    violation_trades = [
        t for t in trades
        if any(tag not in _INFO_TAGS for tag in (t.get("tags") or []))
    ]
    clean_trades = [
        t for t in trades
        if not any(tag not in _INFO_TAGS for tag in (t.get("tags") or []))
    ]

    # 진입방식별 누적 손익
    entry_pnl = {
        "정규종가": 0, "조건부NXT": 0, "추격NXT": 0, "D1추격": 0, "기타": 0
    }
    for t in trades:
        pnl  = t.get("realized") or 0
        et   = t.get("entry_type", "UNKNOWN")
        tags = t.get("tags") or []
        if et == "REGULAR_CLOSE_ENTRY":
            entry_pnl["정규종가"] += pnl
        elif "CONDITIONAL_NXT_ENTRY" in tags:
            entry_pnl["조건부NXT"] += pnl
        elif "NXT_CHASE_ENTRY" in tags or "NXT_ENTRY_CAUTION" in tags:
            entry_pnl["추격NXT"] += pnl
        elif et in ("D1_CHASE_ENTRY", "REVERSE_AT_EXIT_ZONE"):
            entry_pnl["D1추격"] += pnl
        else:
            entry_pnl["기타"] += pnl

    return {
        "total_realized":    total_realized,
        "total_stocks_all":  total_stocks_all,
        "avg_cr":            avg_cr,
        "weeks":             len(history),
        "total_tag_counts":  total_tag_counts,
        "item_keys":         item_keys,
        "violation_trades":  violation_trades,
        "clean_trades":      clean_trades,
        "entry_pnl":         entry_pnl,
    }


def _section_system_stats(stats: dict) -> str:
    """reports/cumulative_stats.json 기반 시스템 누적 성과 섹션."""
    if not stats or not stats.get("total_measured"):
        return ""
    total = stats["total_measured"]

    # 패턴별 승률 rows
    pattern_rows = ""
    for pat, v in stats.get("pattern", {}).items():
        rate = v["rate"]
        color = "#4caf50" if rate >= 65 else ("#fb8c00" if rate >= 50 else "#ef5350")
        pattern_rows += (
            f"<tr><td>{pat}</td>"
            f"<td style='text-align:center'>{v['total']}</td>"
            f"<td style='text-align:center;color:{color};font-weight:600'>{rate:.0f}%</td></tr>"
        )

    # 스코어 구간별 rows
    score_rows = ""
    for band, v in stats.get("score_band", {}).items():
        rate = v["rate"]
        color = "#4caf50" if rate >= 65 else ("#fb8c00" if rate >= 50 else "#ef5350")
        score_rows += (
            f"<tr><td>점수 {band}</td>"
            f"<td style='text-align:center'>{v['total']}</td>"
            f"<td style='text-align:center;color:{color};font-weight:600'>{rate:.0f}%</td></tr>"
        )

    # 교집합 vs 비교집합
    inter_html = ""
    inter_fs = stats.get("inter_full_stats", {})
    d1_inter  = (inter_fs.get("inter",  {}).get("d1_open") or {})
    d1_ninter = (inter_fs.get("ninter", {}).get("d1_open") or {})
    if d1_inter or d1_ninter:
        def _wr_cell(d: dict) -> str:
            if not d:
                return "<td style='text-align:center;color:#555'>-</td>"
            wr = d.get("win_rate", 0)
            avg = d.get("mean", 0)
            n   = d.get("n", 0)
            c   = "#4caf50" if wr >= 60 else ("#fb8c00" if wr >= 50 else "#ef5350")
            return (f"<td style='text-align:center'>"
                    f"<span style='color:{c};font-weight:600'>{wr:.0f}%</span>"
                    f" <span style='color:#555;font-size:11px'>(avg {avg:+.1f}% / {n}개)</span>"
                    f"</td>")
        inter_html = f"""
<div style="margin-top:12px">
  <div style="font-size:12px;color:#888;margin-bottom:6px">교집합 vs 비교집합 (D+1 시가 기준)</div>
  <table style="max-width:500px">
    <thead><tr><th>구분</th><th style='text-align:center'>D+1 시가 승률</th></tr></thead>
    <tbody>
      <tr><td>★ 교집합</td>{_wr_cell(d1_inter)}</tr>
      <tr><td>비교집합</td>{_wr_cell(d1_ninter)}</tr>
    </tbody>
  </table>
</div>"""

    return f"""
<div class="card">
  <div class="section-title">📊 시스템 누적 성과 ({total}개 측정) <span style="font-size:11px;color:#555;font-weight:400">D+1 시가 기준 성공/실패 판정</span></div>
  <div style="display:flex;gap:32px;flex-wrap:wrap">
    <div>
      <div style="font-size:12px;color:#888;margin-bottom:6px">패턴별 성공률</div>
      <table style="max-width:260px">
        <thead><tr><th>패턴</th><th style='text-align:center'>n</th><th style='text-align:center'>성공률</th></tr></thead>
        <tbody>{pattern_rows}</tbody>
      </table>
    </div>
    <div>
      <div style="font-size:12px;color:#888;margin-bottom:6px">스코어 구간별 성공률</div>
      <table style="max-width:260px">
        <thead><tr><th>구간</th><th style='text-align:center'>n</th><th style='text-align:center'>성공률</th></tr></thead>
        <tbody>{score_rows}</tbody>
      </table>
    </div>
  </div>
  {inter_html}
  <div style="font-size:11px;color:#555;margin-top:8px">※ run_trade_review.bat 실행 시 자동 갱신 (backfill_reviews → stats 순서)</div>
</div>"""


# ── HTML 생성 ─────────────────────────────────────────────────

def _generate_html(history: list[dict], agg: dict, cumulative_stats: dict | None = None) -> str:
    if not history:
        return "<html><body><p>데이터 없음 — 먼저 trade_analyzer를 실행하세요.</p></body></html>"

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    # ── 요약 카드 ──────────────────────────────────────────────
    tr  = agg["total_realized"]
    acr = agg["avg_cr"]
    summary_html = f"""
<div class="card">
  <div style="display:flex;gap:40px;flex-wrap:wrap">
    <div>
      <div class="label">누적 실현 손익</div>
      <div style="font-size:32px;font-weight:700;color:{_pnl_color(tr)}">{_fmt_krw(tr)}</div>
      <div class="sub">{agg['weeks']}주 합산</div>
    </div>
    <div>
      <div class="label">평균 엄격 준수율</div>
      <div style="font-size:32px;font-weight:700;color:{_cr_color(acr)}">{f"{acr:.1f}%" if acr is not None else "-"}</div>
      <div class="sub">위반 태그 없는 종목 기준</div>
    </div>
    <div>
      <div class="label">전체 종목 수</div>
      <div style="font-size:28px;font-weight:700">{agg['total_stocks_all']}개</div>
      <div class="sub">{agg['weeks']}주 합산</div>
    </div>
    <div>
      <div class="label">분석 기간</div>
      <div style="font-size:18px;font-weight:600;color:#ccc">{history[0].get('period_end','?')} ~ {history[-1].get('period_end','?')}</div>
      <div class="sub">{agg['weeks']}주</div>
    </div>
  </div>
</div>"""

    # ── 주차별 실적 표 ─────────────────────────────────────────
    week_rows = ""
    for h in history:
        pe   = h.get("period_end", h.get("date", "-"))
        cr   = h.get("compliance_rate")
        rl   = h.get("total_realized") or 0
        rp   = h.get("total_realized_pct")
        ns   = h.get("total_stocks", 0)
        tc   = h.get("tag_counts") or {}
        ic   = h.get("item_compliance") or {}

        avg_n  = tc.get("AVERAGING_DOWN", 0)
        d1_n   = tc.get("D1_CHASE_ENTRY", 0) + tc.get("REVERSE_AT_EXIT_ZONE", 0)
        nxt_n  = tc.get("NXT_CHASE_ENTRY", 0) + tc.get("NXT_ENTRY_CAUTION", 0)
        pos_n  = tc.get("OVERSIZED_POSITION", 0)
        non_s  = tc.get("NON_SIGNAL_TRADE", 0)
        non_i  = tc.get("NON_INTERSECTION_TRADE", 0)

        # 개별 HTML 링크
        report_date = h.get("date", "")
        link_html   = ""
        if report_date:
            report_path = _REPORT_DIR / f"trade_review_{report_date}.html"
            if report_path.exists():
                link_html = f'<a href="trade_review_{_e(report_date)}.html" style="font-size:11px;color:#29b6f6">리포트↗</a>'

        week_rows += (
            f"<tr>"
            f"<td>{_e(pe)} {link_html}</td>"
            f"<td style='text-align:center;color:{_cr_color(cr)};font-weight:600'>{f'{cr:.1f}%' if cr is not None else '-'}</td>"
            f"<td style='text-align:right;color:{_pnl_color(rl)}'>{_fmt_krw(rl)}</td>"
            f"<td style='text-align:center;color:#aaa'>{f'{rp:+.2f}%' if rp is not None else '-'}</td>"
            f"<td style='text-align:center'>{ns}</td>"
            f"<td style='text-align:center;color:{'#e53935' if avg_n else '#4caf50'}'>{avg_n}</td>"
            f"<td style='text-align:center;color:{'#e53935' if d1_n else '#4caf50'}'>{d1_n}</td>"
            f"<td style='text-align:center;color:{'#f57c00' if nxt_n else '#4caf50'}'>{nxt_n}</td>"
            f"<td style='text-align:center;color:{'#8e24aa' if pos_n else '#4caf50'}'>{pos_n}</td>"
            f"<td style='text-align:center;color:{'#fb8c00' if non_s else '#4caf50'}'>{non_s}</td>"
            f"<td style='text-align:center;color:{'#fb8c00' if non_i else '#4caf50'}'>{non_i}</td>"
            f"</tr>"
        )

    week_table = f"""
<div class="card">
  <div class="section-title">주차별 실적</div>
  <table>
    <thead><tr>
      <th>기간</th>
      <th style="text-align:center">엄격준수율</th>
      <th style="text-align:right">실현손익</th>
      <th style="text-align:center">수익률</th>
      <th style="text-align:center">종목수</th>
      <th style="text-align:center">물타기</th>
      <th style="text-align:center">D1추격</th>
      <th style="text-align:center">NXT추격</th>
      <th style="text-align:center">과대포지션</th>
      <th style="text-align:center">미신호</th>
      <th style="text-align:center">비교집합</th>
    </tr></thead>
    <tbody>{week_rows}</tbody>
  </table>
</div>"""

    # ── 항목별 준수율 추세 ─────────────────────────────────────
    item_keys = agg["item_keys"]
    item_header = "".join(f"<th style='text-align:center'>{_e(label)}</th>" for _, label in item_keys)
    item_rows = ""
    for h in history:
        pe = h.get("period_end", h.get("date", "-"))
        ic = h.get("item_compliance") or {}
        cells = ""
        for key, _ in item_keys:
            v = ic.get(key)
            col = _cr_color(v)
            cells += f"<td style='text-align:center;color:{col};font-weight:500'>{f'{v:.0f}%' if v is not None else '-'}</td>"
        item_rows += f"<tr><td>{_e(pe)}</td>{cells}</tr>"

    item_table = f"""
<div class="card">
  <div class="section-title">항목별 준수율 추세</div>
  <div style="font-size:11px;color:#555;margin-bottom:8px">🟢 80%+ &nbsp; 🟡 60~80% &nbsp; 🔴 60% 미만</div>
  <table>
    <thead><tr><th>기간</th>{item_header}</tr></thead>
    <tbody>{item_rows}</tbody>
  </table>
</div>"""

    # ── 위반 태그 집계 ─────────────────────────────────────────
    total_tc = agg["total_tag_counts"]
    tag_rows = ""
    for tag, cnt in sorted(total_tc.items(), key=lambda x: -x[1]):
        color = _TAG_COLOR.get(tag, "#666")
        desc  = _TAG_DESC.get(tag, "")
        # 주차별 breakdown
        weekly_counts = [str((h.get("tag_counts") or {}).get(tag, 0)) for h in history]
        tag_rows += (
            f"<tr>"
            f"<td>{_tag_badge(tag)}</td>"
            f"<td style='text-align:center;font-weight:700;color:{color}'>{cnt}</td>"
            f"<td style='color:#aaa;font-size:12px'>{_e(desc)}</td>"
            f"<td style='font-size:11px;color:#555'>{' / '.join(weekly_counts)}</td>"
            f"</tr>"
        )

    if tag_rows:
        tag_table = f"""
<div class="card">
  <div class="section-title">누적 위반 태그 집계 <span style="font-size:11px;color:#555;font-weight:400">(정보용 태그 제외)</span></div>
  <table>
    <thead><tr>
      <th>태그</th>
      <th style="text-align:center">전체</th>
      <th>설명</th>
      <th style="color:#555">주차별 ({' / '.join(h.get('period_end','?') for h in history)})</th>
    </tr></thead>
    <tbody>{tag_rows}</tbody>
  </table>
</div>"""
    else:
        tag_table = ""

    # ── 진입 방식별 누적 손익 ─────────────────────────────────
    ep = agg["entry_pnl"]
    entry_rows = ""
    for label, pnl in ep.items():
        if pnl == 0:
            pnl_html = '<span style="color:#555">0원</span>'
        else:
            pnl_html = f'<span style="color:{_pnl_color(pnl)}">{_fmt_krw(pnl)}</span>'
        entry_rows += f"<tr><td>{_e(label)}</td><td style='text-align:right'>{pnl_html}</td></tr>"

    entry_table = f"""
<div class="card">
  <div class="section-title">진입 방식별 누적 손익</div>
  <table style="max-width:400px">
    <thead><tr><th>진입 유형</th><th style="text-align:right">누적 실현 손익</th></tr></thead>
    <tbody>{entry_rows}</tbody>
  </table>
  <div style="font-size:11px;color:#555;margin-top:6px">
    ※ 정규종가 진입이 장기적으로 안정적인지 확인하는 지표
  </div>
</div>"""

    # ── 종목별 위반 내역 전체 ─────────────────────────────────
    violation_trades = agg["violation_trades"]
    trade_rows = ""
    for t in sorted(violation_trades, key=lambda x: (x.get("report_date", ""), x.get("code", ""))):
        tags     = [tag for tag in (t.get("tags") or []) if tag not in _INFO_TAGS]
        tag_html = "".join(_tag_badge(tag) for tag in tags)
        rl       = t.get("realized")
        rp       = t.get("realized_pct")
        is_inter = t.get("is_inter")
        inter_html = (
            '<span style="color:#29b6f6;font-size:10px">[교]</span>' if is_inter else
            '<span style="color:#555;font-size:10px">[비]</span>'    if is_inter is False else ""
        )
        trade_rows += (
            f"<tr>"
            f"<td style='font-size:12px;color:#aaa'>{_e(t.get('report_date',''))}</td>"
            f"<td>{_e(t.get('name',''))} {inter_html}</td>"
            f"<td style='font-size:11px;color:#666'>{_e(t.get('code',''))}</td>"
            f"<td style='text-align:right;color:{_pnl_color(rl)}'>"
            f"{_fmt_krw(rl) if rl is not None else '-'}</td>"
            f"<td style='text-align:right;color:#aaa;font-size:12px'>"
            f"{f'{rp:+.2f}%' if rp is not None else '-'}</td>"
            f"<td>{tag_html}</td>"
            f"</tr>"
        )

    trade_section = f"""
<div class="card">
  <div class="section-title">
    위반 종목 전체 기록
    <span style="font-size:11px;color:#555;font-weight:400">({len(violation_trades)}건 / 전체 {len(violation_trades)+len(agg['clean_trades'])}건)</span>
  </div>
  <table>
    <thead><tr>
      <th>리뷰일</th><th>종목명</th><th>코드</th>
      <th style="text-align:right">손익</th>
      <th style="text-align:right">수익률</th>
      <th>위반 태그</th>
    </tr></thead>
    <tbody>{trade_rows if trade_rows else '<tr><td colspan="6" style="color:#4caf50;text-align:center">위반 없음</td></tr>'}</tbody>
  </table>
</div>"""

    # ── 개별 리포트 링크 ───────────────────────────────────────
    link_items = ""
    for h in reversed(history):
        report_date = h.get("date", "")
        if not report_date:
            continue
        report_path = _REPORT_DIR / f"trade_review_{report_date}.html"
        if report_path.exists():
            pe  = h.get("period_end", report_date)
            cr  = h.get("compliance_rate")
            rl  = h.get("total_realized") or 0
            link_items += (
                f'<li><a href="trade_review_{_e(report_date)}.html">'
                f'{_e(pe)} 주간 리포트</a>'
                f' <span style="color:{_cr_color(cr)}">{f"{cr:.0f}%" if cr is not None else "-"}</span>'
                f' <span style="color:{_pnl_color(rl)};font-size:12px">{_fmt_krw(rl)}</span>'
                f'</li>'
            )

    link_section = f"""
<div class="card">
  <div class="section-title">개별 주간 리포트</div>
  <ul style="list-style:none;padding:0;margin:0;line-height:2">{link_items or '<li style="color:#555">리포트 없음</li>'}</ul>
</div>"""

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>매매 복기 누적 대시보드</title>
<style>
  body{{background:#121212;color:#e0e0e0;font-family:'Malgun Gothic',sans-serif;margin:0;padding:24px;max-width:1200px}}
  h1{{font-size:22px;margin-bottom:4px}}
  .label{{font-size:12px;color:#888;margin-bottom:4px}}
  .sub{{font-size:12px;color:#555;margin-top:2px}}
  .card{{background:#1a1a1a;border-radius:8px;padding:16px;margin-bottom:16px}}
  .section-title{{font-size:14px;font-weight:600;margin-bottom:12px}}
  table{{width:100%;border-collapse:collapse}}
  th,td{{padding:7px 8px;border-bottom:1px solid #2a2a2a;text-align:left}}
  th{{color:#888;font-size:12px;font-weight:500}}
  a{{color:#29b6f6;text-decoration:none}}
  a:hover{{text-decoration:underline}}
  ul li{{padding:2px 0}}
</style>
</head>
<body>
<h1>📊 매매 복기 누적 대시보드</h1>
<p style="color:#555;font-size:12px;margin-bottom:20px">생성: {now_str}</p>

{summary_html}
{week_table}
{item_table}
{entry_table}
{tag_table}
{trade_section}
{_section_system_stats(cumulative_stats or {})}
{link_section}
</body>
</html>"""


# ── 메인 ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="누적 복기 대시보드 생성")
    parser.add_argument("--open",   action="store_true", help="HTML을 브라우저로 열기")
    parser.add_argument("--latest", action="store_true", help="(trade_analyzer 호환용, 동작 없음)")
    args = parser.parse_args()

    history = _load_history()
    trades  = _load_trades()

    if not history:
        print("[오류] compliance_history.json 없음 — trade_analyzer를 먼저 실행하세요.")
        return

    agg              = _aggregate(history, trades)
    cumulative_stats = _load_cumulative_stats()
    html             = _generate_html(history, agg, cumulative_stats)

    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    _OUT_HTML.write_text(html, encoding="utf-8")
    print(f"[완료] {_OUT_HTML}")

    s      = agg
    avg_cr = f"{s['avg_cr']:.1f}%" if s['avg_cr'] is not None else '-'
    print(f"  {s['weeks']}주 | 누적 손익 {_fmt_krw(s['total_realized'])} | 평균 준수율 {avg_cr}")

    if args.open:
        webbrowser.open(_OUT_HTML.as_uri())


if __name__ == "__main__":
    main()
