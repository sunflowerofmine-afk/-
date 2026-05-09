# scripts/weekly_review_dashboard.py
"""주간 통합 리뷰 대시보드 — 시스템 후보 vs 실제 매매 비교 요약"""

import argparse
import json
import re
import webbrowser
from datetime import date, datetime
from pathlib import Path

import pandas as pd

# ── 경로 상수 ─────────────────────────────────────────────────────────────
_ROOT         = Path(__file__).parent.parent
_SIGNALS_DIR  = _ROOT / "data" / "signals"
_GAP_ALL      = _ROOT / "data" / "gap_results" / "all.csv"
_TRADE_REVS   = _ROOT / "data" / "trade_reviews"
_OUT_HTML     = _ROOT / "reports" / "weekly_reviews"
_OUT_JSON     = _ROOT / "data" / "weekly_reviews"
_HISTORY      = _OUT_JSON / "weekly_review_history.json"

# ── 태그 → 원칙 텍스트 ────────────────────────────────────────────────────
_PRINCIPLE = {
    "AVERAGING_DOWN":         "물타기 금지 - 추가매수는 평균단가 위에서만",
    "D1_CHASE_ENTRY":         "D+1 장초 추격매수 금지",
    "NXT_CHASE_ENTRY":        "NXT 추격 진입 금지 (+3% 초과)",
    "NON_SIGNAL_TRADE":       "봇 신호 종목만 매매",
    "NON_INTERSECTION_TRADE": "교집합 우선 원칙 - 비교집합 진입 자제",
    "OVERSIZED_POSITION":     "종목당 비중 10% 이하 유지",
    "REVERSE_AT_EXIT_ZONE":   "D+1 09:20~09:40 역매매 금지",
    "MISSED_D1_EXIT":         "갭업 발생 시 D+1 장초 청산 원칙 준수",
    "NOT_CLOSE_ENTRY":        "14:50~15:30 창 내 종가 진입",
}

# ── 코드 정규화 ───────────────────────────────────────────────────────────
def _norm(code) -> str:
    try:
        return str(int(str(code).strip().lstrip("'"))).zfill(6)
    except (ValueError, TypeError):
        return str(code).strip().lstrip("'")


# ──────────────────────────────────────────────────────────────────────────
# 데이터 로딩
# ──────────────────────────────────────────────────────────────────────────

def _load_latest_trade_review() -> tuple[dict, Path]:
    files = sorted(_TRADE_REVS.glob("trade_review_*.json"))
    if not files:
        return {}, Path()
    latest = files[-1]
    try:
        return json.loads(latest.read_text(encoding="utf-8")), latest
    except Exception:
        return {}, latest


def _date_range(review: dict) -> tuple[str, str]:
    """trade_review stocks의 sig_date 범위 (YYYYMMDD)"""
    dates = [s["sig_date"] for s in review.get("stocks", []) if s.get("sig_date")]
    if not dates:
        return "", ""
    return min(dates), max(dates)


def _load_signals(d_min: str, d_max: str) -> pd.DataFrame:
    """기간 내 signals.csv 로드. 같은 날짜 중 최신 타임스탬프 우선."""
    if not d_min:
        return pd.DataFrame()
    d_min_d = datetime.strptime(d_min, "%Y%m%d").date()
    d_max_d = datetime.strptime(d_max, "%Y%m%d").date()

    best: dict[str, Path] = {}
    for f in sorted(_SIGNALS_DIR.glob("*_signals.csv")):
        m = re.match(r"(\d{4}-\d{2}-\d{2})_(\d{4})_signals\.csv", f.name)
        if not m:
            continue
        fd = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        if not (d_min_d <= fd <= d_max_d):
            continue
        key = m.group(1)
        cur = best.get(key)
        if cur is None or m.group(2) > re.search(r"_(\d{4})_signals", cur.name).group(1):
            best[key] = f

    dfs = []
    for f in best.values():
        try:
            df = pd.read_csv(f, encoding="utf-8-sig", dtype=str)
            df["_code"] = df["종목코드"].apply(_norm)
            dfs.append(df)
        except Exception:
            pass
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()


def _load_gap(d_min: str, d_max: str) -> pd.DataFrame:
    if not _GAP_ALL.exists() or not d_min:
        return pd.DataFrame()
    try:
        df = pd.read_csv(_GAP_ALL, encoding="utf-8-sig")
        df["_code"] = df["code"].apply(_norm)
        lo, hi = int(d_min), int(d_max)
        return df[(df["entry_date"] >= lo) & (df["entry_date"] <= hi)].copy()
    except Exception:
        return pd.DataFrame()


def _load_review_entries(d_min: str, d_max: str) -> list:
    if not d_min:
        return []
    lo = datetime.strptime(d_min, "%Y%m%d").date()
    hi = datetime.strptime(d_max, "%Y%m%d").date()
    entries = []
    for f in _SIGNALS_DIR.glob("*_review.json"):
        m = re.match(r"(\d{4}-\d{2}-\d{2})_review\.json", f.name)
        if not m:
            continue
        fd = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        if not (lo <= fd <= hi):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, list):
                entries.extend(data)
        except Exception:
            pass
    return entries


# ──────────────────────────────────────────────────────────────────────────
# 분석
# ──────────────────────────────────────────────────────────────────────────

def _is_inter(val) -> bool:
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("true", "1", "yes")


def _gap_stats(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"n": 0}
    ret = pd.to_numeric(df["return_pct"], errors="coerce") if "return_pct" in df.columns else pd.Series(dtype=float)
    ret_valid = ret.dropna()
    win_col = df["win"].apply(lambda x: bool(x)) if "win" in df.columns else pd.Series(dtype=bool)
    win_valid = win_col[ret.notna()] if (not ret.empty and not win_col.empty) else win_col
    return {
        "n": len(df),
        "d1_open_avg":    round(ret_valid.mean(), 2)   if not ret_valid.empty else None,
        "d1_open_median": round(ret_valid.median(), 2) if not ret_valid.empty else None,
        "win_rate":       round(win_valid.mean() * 100, 1) if not win_valid.empty else None,
    }


def _review_stats(rows: list) -> dict:
    if not rows:
        return {"n": 0}
    df = pd.DataFrame(rows)
    s: dict = {"n": len(df)}
    for src, dst in [("d1_open_pct", "d1_open_avg"), ("d1_high_pct", "d1_high_avg"),
                     ("d1_close_pct", "d1_close_avg"), ("d3_close_pct", "d3_close_avg"),
                     ("mfe", "mfe_avg"), ("mae", "mae_avg")]:
        if src in df.columns:
            v = pd.to_numeric(df[src], errors="coerce").dropna()
            s[dst] = round(v.mean(), 2) if not v.empty else None
    if "final_result_type" in df.columns:
        s["result_types"] = df["final_result_type"].value_counts().to_dict()
    return s


def _system_perf(gap: pd.DataFrame, review_entries: list) -> dict:
    has_gap    = not gap.empty
    has_review = bool(review_entries)

    gap_perf = None
    if has_gap:
        inter_mask = gap["in_inter"].apply(_is_inter)
        gap_perf = {
            "n_total":     len(gap),
            "n_inter":     int(inter_mask.sum()),
            "n_non_inter": int((~inter_mask).sum()),
            "overall":     _gap_stats(gap),
            "inter":       _gap_stats(gap[inter_mask]),
            "non_inter":   _gap_stats(gap[~inter_mask]),
        }

    rev_perf = None
    if has_review:
        rev_df = pd.DataFrame(review_entries)
        inter_mask = rev_df["in_inter"].apply(_is_inter) if "in_inter" in rev_df.columns else pd.Series([False] * len(rev_df))
        rev_perf = {
            "n_total":     len(rev_df),
            "n_inter":     int(inter_mask.sum()),
            "n_non_inter": int((~inter_mask).sum()),
            "overall":     _review_stats(review_entries),
            "inter":       _review_stats(rev_df[inter_mask].to_dict("records")),
            "non_inter":   _review_stats(rev_df[~inter_mask].to_dict("records")),
        }

    source  = "review_json" if has_review else ("gap_results" if has_gap else "none")
    primary = rev_perf or gap_perf or {}
    return {
        "source":      source,
        "n_total":     primary.get("n_total",     0),
        "n_inter":     primary.get("n_inter",     0),
        "n_non_inter": primary.get("n_non_inter", 0),
        "overall":     primary.get("overall",     {}),
        "inter":       primary.get("inter",       {}),
        "non_inter":   primary.get("non_inter",   {}),
        "gap":         gap_perf,
        "review":      rev_perf,
    }


def _is_valid_success(row: dict) -> bool:
    """gap_results 시스템 성공 판정: return_pct 유효 + win=True + return_pct > 0"""
    ret = pd.to_numeric(row.get("return_pct"), errors="coerce")
    return pd.notna(ret) and bool(row.get("win")) and float(ret) > 0


def _compare(stocks: list, sig_df: pd.DataFrame, gap: pd.DataFrame) -> dict:
    # 시스템 후보 코드
    sig_codes: set[str] = set()
    inter_sig: set[str] = set()
    if not sig_df.empty and "_code" in sig_df.columns:
        sig_codes = set(sig_df["_code"].dropna())
        if "in_inter" in sig_df.columns:
            inter_sig = set(sig_df[sig_df["in_inter"].apply(_is_inter)]["_code"].dropna())

    # 실제 매매 종목
    traded_map: dict[str, dict] = {}
    for s in stocks:
        c = _norm(s.get("code", ""))
        traded_map[c] = s
    traded_codes  = set(traded_map)
    traded_inter  = {c for c, s in traded_map.items() if s.get("is_inter")}

    # gap_results 성공: return_pct 유효 + win=True + return_pct > 0
    # gap_map은 valid success 행 우선 저장 (같은 코드에 복수 행 존재 시 NaN 행 덮어쓰기 방지)
    gap_map: dict[str, dict] = {}
    gap_success: set[str] = set()
    if not gap.empty and "_code" in gap.columns:
        for _, row in gap.iterrows():
            rd = row.to_dict()
            c  = row["_code"]
            if c not in gap_map or _is_valid_success(rd):
                gap_map[c] = rd
            if _is_valid_success(rd):
                gap_success.add(c)

    # 코드 × signals 맵
    sig_map: dict[str, dict] = {}
    if not sig_df.empty and "_code" in sig_df.columns:
        for _, row in sig_df.drop_duplicates("_code").iterrows():
            sig_map[row["_code"]] = row.to_dict()

    hit    = gap_success & traded_codes
    missed = gap_success - traded_codes
    no_sig = traded_codes - sig_codes

    # 놓친 종목 상세 (gap_success 필터 통과 = 유효한 수익 종목만)
    missed_detail = []
    for c in missed:
        g   = gap_map.get(c, {})
        ret = float(pd.to_numeric(g.get("return_pct"), errors="coerce"))
        missed_detail.append({
            "code":     c,
            "name":     str(g.get("name", c)),
            "in_inter": bool(g.get("in_inter", False)),
            "d1_ret":   ret,
        })
    missed_detail.sort(key=lambda x: -x["d1_ret"])

    # 비신호 매매 종목명
    no_sig_names = [traded_map[c].get("name", c) for c in sorted(no_sig)]

    # 실현손익
    total_realized   = sum(s.get("realized", 0) for s in stocks)
    n = len(stocks)
    avg_realized_pct = round(sum(s.get("realized_pct") or 0 for s in stocks) / n, 2) if n else 0

    # 교집합 종목 D+1 시초 평균 (sec2 gap_results 기준과 동일 기준으로 통일)
    d1_avg = None
    if not gap.empty and "return_pct" in gap.columns:
        inter_gap = gap[gap["in_inter"].apply(_is_inter)] if "in_inter" in gap.columns else gap
        d1_avg = round(pd.to_numeric(inter_gap["return_pct"], errors="coerce").dropna().mean(), 2)

    return {
        "n_sig": len(sig_codes), "n_inter_sig": len(inter_sig),
        "n_traded": len(traded_map), "n_traded_inter": len(traded_inter),
        "n_gap_success": len(gap_success),
        "n_hit": len(hit), "n_missed": len(missed), "n_no_sig": len(no_sig),
        "total_realized": total_realized, "avg_realized_pct": avg_realized_pct,
        "sys_d1_avg": d1_avg,
        "missed_detail": missed_detail[:5],
        "no_sig_names": no_sig_names[:5],
        "traded_map": traded_map,
        "gap_map": gap_map,
        "sig_map": sig_map,
        "all_codes": sorted(set(traded_map) | set(gap_map)),
    }


# ──────────────────────────────────────────────────────────────────────────
# HTML 생성
# ──────────────────────────────────────────────────────────────────────────

_BG   = "#1e1e2e"
_CARD = "#2a2a3e"
_FG   = "#e0e0e0"
_DIM  = "#888"
_GRN  = "#4caf50"
_RED  = "#ef5350"
_BLU  = "#64b5f6"
_AMB  = "#fb8c00"


def _pc(v) -> str:
    """값에 따른 색상"""
    if v is None or (isinstance(v, float) and pd.isna(v)): return _DIM
    return _GRN if v > 0 else (_RED if v < 0 else _DIM)


def _fp(v) -> str:
    """소수점 2자리 %, None/NaN → 확인불가"""
    if v is None or (isinstance(v, float) and pd.isna(v)): return "확인불가"
    return f"{'+' if v > 0 else ''}{v:.2f}%"


def _fw(v) -> str:
    """원 포맷 colored span"""
    if v is None: return "확인불가"
    color = _pc(v)
    sign  = "+" if v > 0 else ""
    return f'<span style="color:{color}">{sign}{v:,.0f}원</span>'


def _nw(n: int) -> str:
    """표본 수 경고"""
    if n < 5:  return f' <span style="color:{_RED};font-size:10px">(n={n}, 데이터 부족)</span>'
    if n < 20: return f' <span style="color:{_AMB};font-size:10px">(n={n}, 참고용)</span>'
    return f' <span style="color:{_DIM};font-size:10px">(n={n})</span>'


def _card(body: str) -> str:
    return f'<div style="background:{_CARD};border-radius:10px;padding:20px;margin-bottom:16px">{body}</div>'


def _title(t: str) -> str:
    return (f'<div style="font-size:14px;font-weight:700;color:{_BLU};'
            f'margin-bottom:12px;border-bottom:1px solid #444;padding-bottom:6px">{t}</div>')


def _generate_html(d_min, d_max, review, perf, cmp, tr_path, report_date) -> str:
    summary  = review.get("summary", {})
    lesson   = summary.get("lesson", {})
    realized = summary.get("total_realized", 0)
    real_pct = summary.get("total_realized_pct", 0)
    comp_rt  = summary.get("compliance_rate", 0)

    period = f"{d_min[:4]}-{d_min[4:6]}-{d_min[6:]} ~ {d_max[:4]}-{d_max[4:6]}-{d_max[6:]}"

    worst_tag = lesson.get("worst_loss_tag", "")
    principle = _PRINCIPLE.get(worst_tag, worst_tag)
    worst_amt = lesson.get("worst_loss_amount")

    # trade_analyzer HTML 링크 (JSON → HTML 경로 변환)
    ta_rel = ""
    if tr_path.name:
        html_name = tr_path.stem + ".html"
        ta_html_path = _ROOT / "reports" / "trade_reviews" / html_name
        if ta_html_path.exists():
            try:
                ta_rel = "../trade_reviews/" + html_name
            except Exception:
                pass
    ta_link = (f'<a href="{ta_rel}" target="_blank" style="color:{_BLU};font-size:12px">'
               f'매매원칙 분석 상세 리포트 →</a>') if ta_rel else ""

    # ── [1] 한눈 요약 ─────────────────────────────────────────────────────
    lesson_block = ""
    if worst_tag:
        amt_str = f" ({_fw(worst_amt)})" if worst_amt else ""
        lesson_block = (
            f'<div style="margin-top:12px;padding:10px 14px;background:#1a1a2e;'
            f'border-left:3px solid {_RED};border-radius:4px">'
            f'<span style="font-size:11px;color:{_DIM}">최대 손실 원인: </span>'
            f'<span style="color:{_RED};font-weight:600">{worst_tag}</span>{amt_str}'
            f'<div style="font-size:13px;color:{_FG};margin-top:4px">'
            f'다음 주 집중 원칙: {principle}</div></div>'
        )

    sec1 = _card(f"""
{_title("이번 주 한눈 요약")}
<div style="display:flex;gap:28px;flex-wrap:wrap">
  <div><div style="font-size:11px;color:{_DIM}">분석 기간</div>
       <div style="font-size:13px">{period}</div></div>
  <div><div style="font-size:11px;color:{_DIM}">시스템 후보</div>
       <div style="font-size:18px;font-weight:700">{cmp['n_sig']}개
         <span style="font-size:12px;color:{_DIM}">(교집합 {cmp['n_inter_sig']}개)</span></div></div>
  <div><div style="font-size:11px;color:{_DIM}">실제 매매</div>
       <div style="font-size:18px;font-weight:700">{cmp['n_traded']}개</div></div>
  <div><div style="font-size:11px;color:{_DIM}">실현손익</div>
       <div style="font-size:22px;font-weight:700;color:{_pc(realized)}">{_fw(realized)}</div>
       <div style="font-size:11px;color:{_DIM}">{_fp(real_pct)}</div></div>
  <div><div style="font-size:11px;color:{_DIM}">엄격 준수율</div>
       <div style="font-size:22px;font-weight:700;color:{_pc(comp_rt-50)}">{comp_rt:.1f}%</div></div>
</div>
{lesson_block}
<div style="margin-top:12px">{ta_link}</div>
""")

    # ── [2] 시스템 후보 성과 요약 ─────────────────────────────────────────
    src      = perf.get("source", "none")
    rev_perf = perf.get("review")
    gap_perf = perf.get("gap")

    # review.json 블록
    if rev_perf:
        ov  = rev_perf.get("overall", {})
        n_t = rev_perf.get("n_total", 0)
        d1a = ov.get("d1_open_avg"); d3a = ov.get("d3_close_avg")
        mfe = ov.get("mfe_avg");     mae = ov.get("mae_avg")
        rt  = ov.get("result_types", {})
        rt_str = " | ".join(f"{k}: {v}건" for k, v in rt.items()) if rt else ""
        review_block = f"""
<div style="font-size:11px;color:{_GRN};font-weight:600;margin-bottom:6px">review.json 기준 (D+1~D+5 / MFE / MAE)</div>
<div style="display:flex;gap:28px;flex-wrap:wrap;margin-bottom:8px">
  <div><div style="font-size:11px;color:{_DIM}">D+1 시초 평균{_nw(n_t)}</div>
       <div style="font-size:22px;font-weight:700;color:{_pc(d1a)}">{_fp(d1a)}</div></div>
  <div><div style="font-size:11px;color:{_DIM}">D+3 종가 평균</div>
       <div style="font-size:22px;font-weight:700;color:{_pc(d3a)}">{_fp(d3a)}</div></div>
  <div><div style="font-size:11px;color:{_GRN}">MFE 평균</div>
       <div style="font-size:20px;font-weight:700;color:{_GRN}">{_fp(mfe)}</div></div>
  <div><div style="font-size:11px;color:{_RED}">MAE 평균</div>
       <div style="font-size:20px;font-weight:700;color:{_RED}">{_fp(mae)}</div></div>
</div>
{f'<div style="font-size:11px;color:{_DIM};margin-bottom:10px">{rt_str}</div>' if rt_str else ""}"""
    else:
        review_block = (
            f'<div style="font-size:11px;margin-bottom:10px">'
            f'<span style="color:{_GRN};font-weight:600">review.json 기준</span>'
            f' D+1~D+5 / MFE / MAE: <span style="color:{_AMB}">확인불가</span>'
            f' (해당 기간 review.json 없음)</div>'
        )

    # gap_results 블록
    if gap_perf:
        g_inter = gap_perf.get("inter", {})
        n_gi    = gap_perf.get("n_inter", 0)
        d1a_g   = g_inter.get("d1_open_avg")
        wr_g    = g_inter.get("win_rate")
        gap_block = (
            f'<div style="font-size:11px;color:{_BLU};font-weight:600;margin-bottom:4px">'
            f'gap_results 기준 (교집합 D+1 시초, NaN 제외)</div>'
            f'<div style="display:flex;gap:28px;flex-wrap:wrap">'
            f'<div><div style="font-size:11px;color:{_DIM}">D+1 시초 평균{_nw(n_gi)}</div>'
            f'<div style="font-size:20px;font-weight:700;color:{_pc(d1a_g)}">{_fp(d1a_g)}</div></div>'
            f'<div><div style="font-size:11px;color:{_DIM}">D+1 승률</div>'
            f'<div style="font-size:20px;font-weight:700">{_fp(wr_g)}</div></div>'
            f'</div>'
        )
    else:
        gap_block = (
            f'<div style="font-size:11px">'
            f'<span style="color:{_BLU};font-weight:600">gap_results 기준</span>'
            f' 교집합 D+1 시초: <span style="color:{_AMB}">확인불가</span></div>'
        )

    if src == "none":
        body2 = f'<div style="color:{_DIM}">데이터 없음 — gap_results와 review.json 모두 해당 기간 데이터가 없습니다.</div>'
    else:
        body2 = review_block + f'<hr style="border-color:#333;margin:10px 0">' + gap_block

    sec2 = _card(f"""
{_title("시스템 후보 성과 요약")}
<div style="font-size:11px;color:{_DIM};margin-bottom:10px">
  아래 성과는 실제 매매 결과가 아닌, 시스템이 발송한 후보의 신호 기준 가상 성과입니다.</div>
{body2}
""")

    # ── [3] 교집합 / 비교집합 비교 ────────────────────────────────────────
    inter_s   = perf.get("inter", {})
    n_inter_s = perf.get("n_inter", 0)
    ninon_s   = perf.get("n_non_inter", 0)
    non_s     = perf.get("non_inter", {})

    gap_note = ""
    if src == "gap_results":
        gap_note = (
            f'<div style="font-size:11px;color:{_AMB};margin-bottom:8px">'
            f'비교집합 성과는 review.json이 없으면 확인불가입니다.<br>'
            f'현재 시스템 후보 성과는 gap_results 기준 교집합 D+1 시초 성과 중심입니다.</div>'
        )

    def _tc(v, n=0):
        if v is None: return f'<td style="text-align:center;color:{_DIM}">확인불가</td>'
        return f'<td style="text-align:center;color:{_pc(v)};font-weight:600">{_fp(v)}</td>'

    non_d1  = None if src == "gap_results" else non_s.get("d1_open_avg")
    non_wr  = None if src == "gap_results" else non_s.get("win_rate")
    n_non_disp = "—" if src == "gap_results" else str(ninon_s)

    sec3 = _card(f"""
{_title("교집합 / 비교집합 성과 비교")}
{gap_note}
<table style="width:100%;border-collapse:collapse;font-size:13px">
  <thead><tr style="color:{_DIM}">
    <th style="text-align:left;padding:6px">구분</th>
    <th style="text-align:center">D+1 시초 평균</th>
    <th style="text-align:center">D+1 승률</th>
    <th style="text-align:center">표본</th>
  </tr></thead>
  <tbody>
    <tr style="border-top:1px solid #333">
      <td style="padding:6px 8px">교집합{_nw(n_inter_s)}</td>
      {_tc(inter_s.get("d1_open_avg"))}
      {_tc(inter_s.get("win_rate"))}
      <td style="text-align:center;color:{_DIM}">{n_inter_s}</td>
    </tr>
    <tr style="border-top:1px solid #333">
      <td style="padding:6px 8px">비교집합{_nw(ninon_s) if src != "gap_results" else ""}</td>
      {_tc(non_d1)}
      {_tc(non_wr)}
      <td style="text-align:center;color:{_DIM}">{n_non_disp}</td>
    </tr>
  </tbody>
</table>
""")

    # ── [4] 시스템 후보 vs 실제 매매 비교 ────────────────────────────────
    d1a_sys = cmp.get("sys_d1_avg")
    d1a_act = cmp.get("avg_realized_pct")

    no_sig_str = (", ".join(cmp.get("no_sig_names", []))
                  or f'<span style="color:{_DIM}">없음</span>')

    missed_rows = ""
    for m in cmp.get("missed_detail", []):
        badge = "★" if m.get("in_inter") else ""
        ret   = m.get("d1_ret")
        missed_rows += (
            f'<tr style="border-top:1px solid #333">'
            f'<td style="padding:4px 8px">{m["name"]} {badge}</td>'
            f'<td style="text-align:center;color:{_pc(ret)}">{_fp(ret)}</td></tr>'
        )

    sec4 = _card(f"""
{_title("시스템 후보 vs 내 실제 매매 비교")}
<table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:14px">
  <thead><tr style="color:{_DIM}">
    <th style="text-align:left;padding:6px">항목</th>
    <th style="text-align:center">시스템 후보</th>
    <th style="text-align:center">내 실제 매매</th>
  </tr></thead>
  <tbody>
    <tr style="border-top:1px solid #333">
      <td style="padding:6px 8px">종목 수</td>
      <td style="text-align:center">{cmp['n_sig']}개</td>
      <td style="text-align:center">{cmp['n_traded']}개</td>
    </tr>
    <tr style="border-top:1px solid #333">
      <td style="padding:6px 8px">교집합 수</td>
      <td style="text-align:center">{cmp['n_inter_sig']}개</td>
      <td style="text-align:center">{cmp['n_traded_inter']}개</td>
    </tr>
    <tr style="border-top:1px solid #333">
      <td style="padding:6px 8px">D+1 시초 평균</td>
      <td style="text-align:center;color:{_pc(d1a_sys)}">{_fp(d1a_sys)}
        <span style="font-size:10px;color:{_DIM}"> (교집합 기준)</span></td>
      <td style="text-align:center;color:{_pc(d1a_act)}">{_fp(d1a_act)}
        <span style="font-size:10px;color:{_DIM}"> (실현 평균)</span></td>
    </tr>
    <tr style="border-top:1px solid #333">
      <td style="padding:6px 8px">시스템 성공 종목 중 매매한 것</td>
      <td colspan="2" style="text-align:center">
        {cmp['n_hit']}개 / {cmp['n_gap_success']}개
        <span style="font-size:10px;color:{_DIM}"> (gap_results D+1 시초 기준, NaN 제외)</span></td>
    </tr>
    <tr style="border-top:1px solid #333">
      <td style="padding:6px 8px">시스템 성공 종목 중 놓친 것</td>
      <td colspan="2" style="text-align:center">{cmp['n_missed']}개</td>
    </tr>
    <tr style="border-top:1px solid #333">
      <td style="padding:6px 8px">비신호 매매 종목</td>
      <td colspan="2" style="text-align:center">{cmp['n_no_sig']}개
        {f"({no_sig_str})" if cmp['n_no_sig'] else ""}</td>
    </tr>
  </tbody>
</table>
{f'''<div style="font-size:12px;color:{_DIM};margin-bottom:4px">놓친 시스템 성공 종목 (D+1 시초 기준, 상위 5개):</div>
<table style="width:100%;border-collapse:collapse;font-size:12px">
  <thead><tr style="color:{_DIM}"><th style="text-align:left;padding:4px 8px">종목</th>
  <th style="text-align:center">D+1 시초</th></tr></thead>
  <tbody>{missed_rows}</tbody></table>''' if missed_rows else ""}
""")

    # ── [5] 종목별 통합 카드 ──────────────────────────────────────────────
    traded_map = cmp.get("traded_map", {})
    gap_map    = cmp.get("gap_map", {})
    sig_map    = cmp.get("sig_map", {})

    cards = ""
    for code in cmp.get("all_codes", []):
        ts  = traded_map.get(code)
        g   = gap_map.get(code)
        sig = sig_map.get(code)
        name = (ts or g or sig or {}).get("name", code)
        is_inter = bool((ts or {}).get("is_inter") or _is_inter((g or {}).get("in_inter", False)))
        has_sig  = bool(sig or g)

        inter_badge = (f'<span style="background:#1565c0;padding:1px 5px;border-radius:10px;'
                       f'font-size:10px">★교집합</span> ') if is_inter else ""
        sig_badge   = (f'<span style="background:#2e7d32;padding:1px 5px;border-radius:10px;'
                       f'font-size:10px">신호</span>') if has_sig else (
                      f'<span style="background:#424242;padding:1px 5px;border-radius:10px;'
                       f'font-size:10px">비신호</span>')

        # 시스템 성과 (NaN이면 확인불가, 아이콘 없음)
        if g:
            d1r = pd.to_numeric(g.get("return_pct"), errors="coerce")
            if pd.isna(d1r):
                sys_txt = f'D+1 시초 확인불가'
            else:
                d1r = float(d1r)
                sys_txt = f'D+1 시초 <span style="color:{_pc(d1r)}">{_fp(d1r)}</span>'
        else:
            sys_txt = f'<span style="color:{_DIM}">확인불가</span>'

        # 실제 매매
        if ts:
            real = ts.get("realized", 0)
            rpct = ts.get("realized_pct")
            tags = ts.get("tags", [])
            tag_str = " ".join(
                f'<span style="background:#333;padding:1px 4px;border-radius:3px;font-size:10px">{t}</span>'
                for t in tags[:3]
            )
            rem = ts.get("remaining_qty", 0)
            rem_txt = (f' <span style="color:{_AMB};font-size:10px">잔고 {rem}주</span>') if rem else ""
            act_txt = f'{_fw(real)} ({_fp(rpct)}) {tag_str}{rem_txt}'
        else:
            act_txt = f'<span style="color:{_DIM}">매매 없음</span>'

        cards += (
            f'<div style="background:#252535;border-radius:8px;padding:10px 14px;margin-bottom:8px">'
            f'<div style="font-size:13px;font-weight:600;margin-bottom:6px">'
            f'{name} <span style="font-size:11px;color:{_DIM}">({code})</span>'
            f' {inter_badge}{sig_badge}</div>'
            f'<div style="display:flex;gap:28px;font-size:12px;flex-wrap:wrap">'
            f'<div><span style="color:{_DIM}">시스템 성과 </span>{sys_txt}</div>'
            f'<div><span style="color:{_DIM}">실제 매매 </span>{act_txt}</div>'
            f'</div></div>'
        )

    sec5 = _card(f"""
{_title("종목별 통합 카드")}
<div style="font-size:11px;color:{_DIM};margin-bottom:10px">
  시스템 신호 성과(D+1 시초 기준)와 실제 매매 결과를 함께 표시합니다.</div>
{cards or f'<div style="color:{_DIM}">표시할 종목 없음</div>'}
""")

    # ── [6] 링크 ──────────────────────────────────────────────────────────
    sec6 = _card(f"""
{_title("관련 리포트")}
<div style="font-size:13px;line-height:2.2">
  {ta_link if ta_link else f'<span style="color:{_DIM}">매매원칙 분석 리포트 없음</span>'}<br>
  <span style="font-size:11px;color:{_DIM}">
    상세 원칙 준수율 / 위반 태그별 손익 / 진입 방식별 성과는 위 리포트에서 확인하세요.</span>
</div>
""")

    # ── 조립 ──────────────────────────────────────────────────────────────
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>주간 통합 리뷰 {report_date}</title>
<style>
  body {{background:{_BG};color:{_FG};font-family:'Segoe UI',sans-serif;margin:0;padding:20px;}}
  table {{width:100%;border-collapse:collapse;}}
  th,td {{padding:6px 8px;}}
  a {{color:{_BLU};text-decoration:none;}} a:hover {{text-decoration:underline;}}
</style>
</head>
<body>
<div style="max-width:920px;margin:0 auto">
  <div style="font-size:20px;font-weight:700;margin-bottom:4px">주간 통합 리뷰</div>
  <div style="font-size:12px;color:{_DIM};margin-bottom:20px">
    {report_date} 생성 · 시스템 후보 vs 실제 매매 비교 요약</div>
  {sec1}{sec2}{sec3}{sec4}{sec5}{sec6}
</div>
</body>
</html>"""


# ──────────────────────────────────────────────────────────────────────────
# 저장 / 히스토리
# ──────────────────────────────────────────────────────────────────────────

def _build_entry(d_min, d_max, review, perf, cmp, report_date) -> dict:
    summary = review.get("summary", {})
    lesson  = summary.get("lesson", {})
    return {
        "report_date":    report_date,
        "period_start":   d_min,
        "period_end":     d_max,
        "n_sig":          cmp["n_sig"],
        "n_inter_sig":    cmp["n_inter_sig"],
        "n_traded":       cmp["n_traded"],
        "total_realized": cmp["total_realized"],
        "compliance_rate": summary.get("compliance_rate"),
        "worst_loss_tag": lesson.get("worst_loss_tag", ""),
        "sys_d1_avg":     perf.get("overall", {}).get("d1_open_avg"),
        "sys_source":     perf.get("source"),
        "n_hit":          cmp["n_hit"],
        "n_missed":       cmp["n_missed"],
        "n_no_sig":       cmp["n_no_sig"],
    }


def _save_history(entry: dict, overwrite: bool):
    _OUT_JSON.mkdir(parents=True, exist_ok=True)
    history: list = []
    if _HISTORY.exists():
        try:
            history = json.loads(_HISTORY.read_text(encoding="utf-8"))
        except Exception:
            pass
    already = any(h.get("report_date") == entry["report_date"] for h in history)
    if already and not overwrite:
        return
    updated = [h for h in history if h.get("report_date") != entry["report_date"]]
    updated.append(entry)
    _HISTORY.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")


# ──────────────────────────────────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="주간 통합 리뷰 대시보드")
    parser.add_argument("--latest",    action="store_true")
    parser.add_argument("--open",      action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    report_date = date.today().strftime("%Y-%m-%d")

    # 1. trade_review 로드
    review, tr_path = _load_latest_trade_review()
    if not review:
        print("[오류] trade_review JSON 없음. 먼저 trade_analyzer를 실행하세요.")
        return

    # 2. 날짜 범위
    d_min, d_max = _date_range(review)
    if not d_min:
        print("[오류] trade_review JSON에서 날짜 범위 추출 실패")
        return
    print(f"[주간리뷰] 기간: {d_min} ~ {d_max}")

    # 3. 데이터
    sig_df   = _load_signals(d_min, d_max)
    gap      = _load_gap(d_min, d_max)
    rev_ents = _load_review_entries(d_min, d_max)
    print(f"  signals {len(sig_df)}행 | gap_results {len(gap)}행 | review_entries {len(rev_ents)}건")

    # 4. 분석
    perf = _system_perf(gap, rev_ents)
    cmp  = _compare(review.get("stocks", []), sig_df, gap)

    # 5. 출력 경로 결정
    _OUT_HTML.mkdir(parents=True, exist_ok=True)
    _OUT_JSON.mkdir(parents=True, exist_ok=True)

    stem      = f"weekly_review_{report_date}"
    html_path = _OUT_HTML / f"{stem}.html"
    json_path = _OUT_JSON / f"{stem}.json"

    if html_path.exists() and not args.overwrite:
        i = 2
        while (_OUT_HTML / f"{stem}_v{i}.html").exists():
            i += 1
        html_path = _OUT_HTML / f"{stem}_v{i}.html"
        json_path = _OUT_JSON / f"{stem}_v{i}.json"

    # 6. HTML 생성
    html = _generate_html(d_min, d_max, review, perf, cmp, tr_path, report_date)
    html_path.write_text(html, encoding="utf-8")

    # 7. JSON 저장
    entry = _build_entry(d_min, d_max, review, perf, cmp, report_date)
    json_path.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")

    # 8. 히스토리
    _save_history(entry, args.overwrite)

    # 9. 콘솔 요약
    d1 = perf.get("overall", {}).get("d1_open_avg")
    d1_str = (f"{'+' if d1 > 0 else ''}{d1:.2f}%") if d1 is not None else "확인불가"
    wt = review.get("summary", {}).get("lesson", {}).get("worst_loss_tag", "")
    print(f"  시스템 후보 D+1 시초 평균: {d1_str}")
    print(f"  실제 손익: {cmp['total_realized']:+,.0f}원")
    print(f"  엄격 준수율: {review.get('summary', {}).get('compliance_rate', 0):.1f}%")
    if wt:
        print(f"  다음 주 집중 원칙: {_PRINCIPLE.get(wt, wt)}")
    print(f"  HTML -> {html_path}")

    if args.open:
        webbrowser.open(html_path.as_uri())


if __name__ == "__main__":
    main()
