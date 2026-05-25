#!/usr/bin/env python3
"""
기준봉 발생 후 고가횡보/수축/KH 탈락 사유 분석.
최근 N일 1750 시그널에서 당일돌파형 → D+1~D+5 조건별 pass/fail 출력.

사용법:
    python -m scripts.pattern_failure_analysis
    python -m scripts.pattern_failure_analysis --days 21 --open
"""

import sys
import argparse
import webbrowser
from datetime import datetime, timedelta, date as date_t
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import (
    HIGH_RANGE_HOLD_MAX_GAP_FROM_BASE_HIGH_PCT,
    STRUCTURE_BREAK_MAX_GAP_PCT,
    TV_RATIO_WATCH_MIN,
    HTC_POST_AVG_TV_RATIO_MAX,
    HTC_TODAY_TV_RATIO_MAX,
    HTC_MIN_TODAY_TV_EOK,
    HTC_CLOSE_FROM_BASE_HIGH_MIN_PCT,
    HTC_CLOSE_FROM_BASE_CLOSE_MIN_PCT,
    HTC_LOWEST_CLOSE_FROM_BASE_CLOSE_MIN_PCT,
    HTC_RANGE_MAX_PCT,
    HTC_CLOSE_RANGE_MAX_PCT,
    HTC_STRUCTURE_BREAK_FROM_BASE_HIGH_PCT,
    HTC_BREAKDOWN_CANDLE_CHANGE_MIN_PCT,
    HTC_BREAKDOWN_CANDLE_TV_RATIO_MIN,
    KH_BASE_TV_MIN_EOK,
    KH_BASE_TV_EXPLOSION_MULT,
    KH_TODAY_TV_RATIO_MAX,
    KH_CLOSE_FROM_BASE_HIGH_MIN_PCT,
    KH_VOLUME_UP_BEARISH_RATIO,
    SIGNALS_DIR,
    REPORTS_DIR,
)


# ── 시그널 파일 로드 ──────────────────────────────────────────────────

def _parse_date(fname: str) -> date_t | None:
    try:
        return datetime.strptime(fname[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def load_1750_signals(days: int = 21) -> dict[date_t, pd.DataFrame]:
    """최근 N일 1750 시그널 파일 로드. 날짜별 가장 늦은 파일 1개 사용."""
    cutoff = datetime.now().date() - timedelta(days=days)
    by_date: dict[date_t, list[Path]] = {}
    for f in sorted(SIGNALS_DIR.glob("*_1750_signals.csv")):
        d = _parse_date(f.name)
        if d and d >= cutoff:
            by_date.setdefault(d, []).append(f)

    result: dict[date_t, pd.DataFrame] = {}
    for d, files in by_date.items():
        f = sorted(files)[-1]
        try:
            df = pd.read_csv(f, encoding="utf-8-sig")
            # 종목코드 6자리 통일
            df["종목코드"] = df["종목코드"].astype(str).str.zfill(6)
            result[d] = df
        except Exception:
            pass
    return result


# ── 당일돌파형 이벤트 추출 ────────────────────────────────────────────

def find_base_candle_events(signals: dict[date_t, pd.DataFrame]) -> list[dict]:
    events = []
    for d, df in sorted(signals.items()):
        for _, row in df.iterrows():
            if row.get("pattern_type_label") == "당일돌파형":
                events.append({
                    "base_date": d,
                    "code": str(row.get("종목코드", "")).zfill(6),
                    "name": row.get("종목명", ""),
                    "market": row.get("시장", "KOSPI"),
                    "base_tv_won": float(row.get("거래대금", 0) or 0),
                    "base_change_pct": float(row.get("등락률", 0) or 0),
                })
    return events


# ── yfinance OHLCV 조회 ───────────────────────────────────────────────

def fetch_yf(code: str, market: str, start: date_t, end: date_t) -> pd.DataFrame:
    """yfinance로 OHLCV 조회. Volume×Close = 근사 거래대금(원)."""
    try:
        import yfinance as yf
        suffix = ".KS" if market == "KOSPI" else ".KQ"
        ticker = code + suffix
        hist = yf.Ticker(ticker).history(
            start=start.strftime("%Y-%m-%d"),
            end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
            auto_adjust=True,
        )
        if hist.empty:
            return pd.DataFrame()
        hist.index = pd.to_datetime(hist.index).tz_localize(None).normalize()
        hist.index = [i.date() for i in hist.index]
        hist["tv_calc"] = hist["Volume"] * hist["Close"]
        return hist
    except Exception:
        return pd.DataFrame()


# ── 조건 평가 헬퍼 ───────────────────────────────────────────────────

def _r(ok: bool, value: str, note: str = "") -> dict:
    return {"ok": ok, "value": value, "note": note}


def eval_p3(today_close: float, today_tv: float,
            base_high: float, base_tv: float,
            inter_closes: list[float]) -> dict[str, dict]:
    """고가횡보형 조건 평가."""
    res = {}

    gap = (today_close - base_high) / base_high * 100
    res["가격유지"] = _r(
        gap >= -HIGH_RANGE_HOLD_MAX_GAP_FROM_BASE_HIGH_PCT,
        f"{gap:+.1f}%", f"기준: ≥{-HIGH_RANGE_HOLD_MAX_GAP_FROM_BASE_HIGH_PCT}%"
    )

    tv_ratio = today_tv / base_tv if base_tv > 0 else None
    if tv_ratio is None:
        res["TV최소"] = _r(True, "N/A", "base_tv 없음")
    else:
        res["TV최소"] = _r(
            tv_ratio >= TV_RATIO_WATCH_MIN,
            f"{tv_ratio:.2f}", f"기준: ≥{TV_RATIO_WATCH_MIN}"
        )

    struct_broken = any(
        base_high > 0 and c > 0 and
        (base_high - c) / base_high * 100 > STRUCTURE_BREAK_MAX_GAP_PCT
        for c in inter_closes
    )
    res["구조붕괴없음"] = _r(
        not struct_broken,
        "붕괴발생" if struct_broken else "정상",
        f"중간일 기준봉 고가 대비 -{STRUCTURE_BREAK_MAX_GAP_PCT}% 이내"
    )

    ok = res["가격유지"]["ok"] and res["TV최소"]["ok"] and res["구조붕괴없음"]["ok"]
    res["최종"] = _r(ok, "✅ 해당" if ok else "❌ 탈락")
    return res


def eval_htc(today_close: float, today_high: float, today_open: float, today_tv: float,
             base_high: float, base_close: float, base_open: float, base_tv: float,
             inter: list[dict]) -> dict[str, dict]:
    """고가수축형(HTC) 조건 평가."""
    res = {}

    # 1. 오늘 종가 vs 기준봉 고가
    g_high = (today_close - base_high) / base_high * 100
    res["가격(고가기준)"] = _r(
        g_high >= HTC_CLOSE_FROM_BASE_HIGH_MIN_PCT,
        f"{g_high:+.1f}%", f"기준: ≥{HTC_CLOSE_FROM_BASE_HIGH_MIN_PCT}%"
    )

    # 2. 오늘 종가 vs 기준봉 종가
    g_close = (today_close - base_close) / base_close * 100
    res["가격(종가기준)"] = _r(
        g_close >= HTC_CLOSE_FROM_BASE_CLOSE_MIN_PCT,
        f"{g_close:+.1f}%", f"기준: ≥{HTC_CLOSE_FROM_BASE_CLOSE_MIN_PCT}%"
    )

    # 3. 중간일 최저 종가
    all_closes = [d["close"] for d in inter if d.get("close", 0) > 0] + [today_close]
    min_c = min(all_closes) if all_closes else today_close
    min_gap = (min_c - base_close) / base_close * 100
    res["중간일최저종가"] = _r(
        min_gap >= HTC_LOWEST_CLOSE_FROM_BASE_CLOSE_MIN_PCT,
        f"{min_gap:+.1f}%", f"기준: ≥{HTC_LOWEST_CLOSE_FROM_BASE_CLOSE_MIN_PCT}%"
    )

    # 4. TV 수축 — 평균 (오늘 포함)
    all_tvs = [d["tv"] for d in inter if d.get("tv", 0) > 0] + [today_tv]
    avg_tv = sum(all_tvs) / len(all_tvs) if all_tvs else 0
    tv_avg_ratio = avg_tv / base_tv if base_tv > 0 else 0
    res["TV평균수축"] = _r(
        tv_avg_ratio <= HTC_POST_AVG_TV_RATIO_MAX,
        f"{tv_avg_ratio:.2f}", f"기준: ≤{HTC_POST_AVG_TV_RATIO_MAX}"
    )

    # 5. TV 수축 — 오늘
    tv_today_ratio = today_tv / base_tv if base_tv > 0 else 0
    res["TV오늘수축"] = _r(
        tv_today_ratio <= HTC_TODAY_TV_RATIO_MAX,
        f"{tv_today_ratio:.2f}", f"기준: ≤{HTC_TODAY_TV_RATIO_MAX}"
    )

    # 6. TV 최소 절대값
    res["TV최소(300억)"] = _r(
        today_tv >= HTC_MIN_TODAY_TV_EOK * 1e8,
        f"{today_tv / 1e8:.0f}억", f"기준: ≥{HTC_MIN_TODAY_TV_EOK}억"
    )

    # 7. 고저 변동폭
    all_highs = [d.get("high", 0) for d in inter] + [today_high]
    all_lows  = [d.get("low",  0) for d in inter]
    max_h = max([h for h in all_highs if h > 0], default=0)
    min_l = min([l for l in all_lows  if l > 0], default=0)
    range_pct = (max_h - min_l) / max_h * 100 if max_h > 0 and min_l > 0 else 0
    res["고저변동폭"] = _r(
        range_pct <= HTC_RANGE_MAX_PCT,
        f"{range_pct:.1f}%", f"기준: ≤{HTC_RANGE_MAX_PCT}%"
    )

    # 8. 종가 변동폭
    max_c2 = max([c for c in all_closes if c > 0], default=0)
    min_c2 = min([c for c in all_closes if c > 0], default=0)
    close_rng = (max_c2 - min_c2) / max_c2 * 100 if max_c2 > 0 else 0
    res["종가변동폭"] = _r(
        close_rng <= HTC_CLOSE_RANGE_MAX_PCT,
        f"{close_rng:.1f}%", f"기준: ≤{HTC_CLOSE_RANGE_MAX_PCT}%"
    )

    # 9. 중간일 구조붕괴 (기준봉 고가 대비 -8%)
    struct_broken = any(
        base_high > 0 and d.get("close", 0) > 0 and
        (d["close"] - base_high) / base_high * 100 < HTC_STRUCTURE_BREAK_FROM_BASE_HIGH_PCT
        for d in inter
    )
    res["구조붕괴없음"] = _r(
        not struct_broken,
        "붕괴발생" if struct_broken else "정상",
        f"기준봉 고가 대비 {HTC_STRUCTURE_BREAK_FROM_BASE_HIGH_PCT}% 이내"
    )

    # 10. 기준봉 몸통 중간값 하회 (hardcoded)
    body_mid = (base_open + base_close) / 2
    body_broken = any(
        d.get("close", 0) > 0 and body_mid > 0 and d["close"] < body_mid
        for d in inter
    )
    res["몸통중간값유지"] = _r(
        not body_broken,
        f"하회발생({body_mid:,.0f}원)" if body_broken else "정상",
        "중간일 종가 ≥ 기준봉 몸통 중간값"
    )

    # 11. 장대음봉 없음
    breakdown = any(
        d.get("close", 0) > 0 and d.get("open", 0) > 0 and
        d["close"] < d["open"] and
        d.get("change_pct", 0) <= HTC_BREAKDOWN_CANDLE_CHANGE_MIN_PCT and
        d.get("tv", 0) >= base_tv * HTC_BREAKDOWN_CANDLE_TV_RATIO_MIN
        for d in inter
    )
    res["장대음봉없음"] = _r(
        not breakdown,
        "발생" if breakdown else "정상",
        f"음봉 {HTC_BREAKDOWN_CANDLE_CHANGE_MIN_PCT}%↓ + TV≥기준봉×{HTC_BREAKDOWN_CANDLE_TV_RATIO_MIN}"
    )

    ok = all(v["ok"] for k, v in res.items() if k != "최종")
    res["최종"] = _r(ok, "✅ 해당" if ok else "❌ 탈락")
    return res


def eval_kh(today_close: float, today_open: float, today_tv: float,
            base_high: float, base_tv: float, avg_20d_tv: float | None,
            above_ma5: bool | None, near_high_60d: bool) -> dict[str, dict]:
    """김형준 패턴 조건 평가."""
    res = {}

    # 1. 기준봉 TV 폭발
    if avg_20d_tv and avg_20d_tv > 0:
        explosion = base_tv / avg_20d_tv
        res["기준봉TV폭발"] = _r(
            explosion >= KH_BASE_TV_EXPLOSION_MULT,
            f"{explosion:.1f}배", f"기준: ≥{KH_BASE_TV_EXPLOSION_MULT}배"
        )
    else:
        res["기준봉TV폭발"] = _r(True, "N/A", "20일평균 없음")

    # 2. 기준봉 TV 절대값
    res["기준봉TV최소"] = _r(
        base_tv >= KH_BASE_TV_MIN_EOK * 1e8,
        f"{base_tv / 1e8:.0f}억", f"기준: ≥{KH_BASE_TV_MIN_EOK}억"
    )

    # 3. 오늘 TV 수축
    tv_r = today_tv / base_tv if base_tv > 0 else 0
    res["TV수축"] = _r(
        tv_r <= KH_TODAY_TV_RATIO_MAX,
        f"{tv_r:.2f}", f"기준: ≤{KH_TODAY_TV_RATIO_MAX}"
    )

    # 4. 가격 유지 (기준봉 고가 대비)
    gap = (today_close - base_high) / base_high * 100
    res["가격유지"] = _r(
        gap >= KH_CLOSE_FROM_BASE_HIGH_MIN_PCT,
        f"{gap:+.1f}%", f"기준: ≥{KH_CLOSE_FROM_BASE_HIGH_MIN_PCT}%"
    )

    # 5. 5일선 위
    if above_ma5 is None:
        res["5일선위"] = _r(True, "N/A", "계산불가")
    else:
        res["5일선위"] = _r(above_ma5, "✓" if above_ma5 else "✗")

    # 6. 신고가권 (60일)
    res["신고가권"] = _r(near_high_60d, "✓" if near_high_60d else "✗", "60일 고가 97% 이내")

    # 7. 거래량 증가 음봉 제외
    vol_bearish = today_close < today_open and tv_r >= KH_VOLUME_UP_BEARISH_RATIO
    res["거래량증가음봉없음"] = _r(
        not vol_bearish,
        "발생" if vol_bearish else "정상",
        f"음봉 + TV≥기준봉×{KH_VOLUME_UP_BEARISH_RATIO}"
    )

    ok = all(v["ok"] for k, v in res.items() if k != "최종")
    res["최종"] = _r(ok, "✅ 해당" if ok else "❌ 탈락")
    return res


# ── 단일 이벤트 분석 ─────────────────────────────────────────────────

def analyze_event(event: dict, signals: dict[date_t, pd.DataFrame]) -> dict:
    code       = event["code"]
    market     = event["market"]
    base_date  = event["base_date"]
    base_tv    = event["base_tv_won"]

    # yfinance 조회 (기준봉 -35일 ~ +8일)
    hist = fetch_yf(code, market, base_date - timedelta(days=35), base_date + timedelta(days=8))
    if hist.empty:
        return {**event, "error": "yfinance 조회 실패", "days": {}}

    dated = list(hist.index)

    # 기준봉 행
    candidates = [d for d in dated if d <= base_date]
    if not candidates:
        return {**event, "error": "기준봉 데이터 없음", "days": {}}
    base_date_yf = max(candidates)
    br = hist.loc[base_date_yf]
    base_high  = float(br["High"])
    base_close = float(br["Close"])
    base_open  = float(br["Open"])

    # 20일 평균 TV (기준봉 이전)
    past20 = sorted([d for d in dated if d < base_date_yf])[-20:]
    avg_20d_tv = (
        sum(float(hist.loc[d]["tv_calc"]) for d in past20) / len(past20)
        if past20 else None
    )

    # D+1 ~ D+5 거래일
    future_dates = sorted([d for d in dated if d > base_date_yf])[:5]

    day_results: dict[int, dict] = {}
    for i, td in enumerate(future_dates, 1):
        row = hist.loc[td]
        today_close = float(row["Close"])
        today_open  = float(row["Open"])
        today_high  = float(row["High"])
        today_low   = float(row["Low"])
        today_tv_yf = float(row["tv_calc"])

        # 시그널 파일에서 TV 보완
        sig_row = None
        sig_df  = signals.get(td)
        if sig_df is not None:
            match = sig_df[sig_df["종목코드"] == code]
            if not match.empty:
                sig_row = match.iloc[0]

        if sig_row is not None and not pd.isna(sig_row.get("거래대금", float("nan"))):
            today_tv    = float(sig_row["거래대금"])
            tv_source   = "시그널"
            sig_pattern = sig_row.get("pattern_type_label", "N/A")
        else:
            today_tv    = today_tv_yf
            tv_source   = "yfinance(근사)"
            sig_pattern = "시그널없음"

        # 중간일 데이터 구성 (D+1 ~ D+i-1)
        inter: list[dict] = []
        for prev_td in future_dates[:i - 1]:
            pr  = hist.loc[prev_td]
            # 전전일 close (change_pct 계산용)
            prev_prev = sorted([d for d in dated if d < prev_td])
            pp_close  = float(hist.loc[prev_prev[-1]]["Close"]) if prev_prev else base_close

            # 시그널 파일에서 TV 보완
            prev_tv = float(pr["tv_calc"])
            if sig_df is not None:  # 오늘 날짜 기준이 아닌 prev_td 기준 시그널 찾기
                ps_df = signals.get(prev_td)
                if ps_df is not None:
                    pm = ps_df[ps_df["종목코드"] == code]
                    if not pm.empty and not pd.isna(pm.iloc[0].get("거래대금", float("nan"))):
                        prev_tv = float(pm.iloc[0]["거래대금"])

            inter.append({
                "date":       prev_td,
                "close":      float(pr["Close"]),
                "open":       float(pr["Open"]),
                "high":       float(pr["High"]),
                "low":        float(pr["Low"]),
                "tv":         prev_tv,
                "change_pct": (float(pr["Close"]) - pp_close) / pp_close * 100,
            })

        # 60일 신고가 여부
        past60 = sorted([d for d in dated if d < td])[-60:]
        high_60d = max((float(hist.loc[d]["High"]) for d in past60), default=0)
        near_high_60d = high_60d > 0 and today_close >= high_60d * 0.97

        # 5일 이동평균
        past5 = sorted([d for d in dated if d <= td])[-5:]
        above_ma5 = (
            today_close > sum(float(hist.loc[d]["Close"]) for d in past5) / len(past5)
            if len(past5) == 5 else None
        )

        # 전일 대비 등락률
        prev_dates = sorted([d for d in dated if d < td])
        prev_close = float(hist.loc[prev_dates[-1]]["Close"]) if prev_dates else base_close
        change_pct = (today_close - prev_close) / prev_close * 100

        day_results[i] = {
            "date":        td,
            "close":       today_close,
            "change_pct":  change_pct,
            "tv_won":      today_tv,
            "tv_source":   tv_source,
            "sig_pattern": sig_pattern,
            "p3":  eval_p3(today_close, today_tv, base_high, base_tv,
                           [d["close"] for d in inter]),
            "htc": eval_htc(today_close, today_high, today_open, today_tv,
                            base_high, base_close, base_open, base_tv, inter),
            "kh":  eval_kh(today_close, today_open, today_tv,
                           base_high, base_tv, avg_20d_tv,
                           above_ma5, near_high_60d),
        }

    return {**event, "base_high": base_high, "base_close": base_close,
            "error": None, "days": day_results}


# ── HTML 생성 ─────────────────────────────────────────────────────────

_CSS = """
body{font-family:'Noto Sans KR',sans-serif;font-size:13px;background:#f8f9fa;margin:0;padding:20px}
h1{font-size:18px;margin-bottom:4px}
.meta{color:#666;font-size:12px;margin-bottom:24px}
.card{background:#fff;border:1px solid #dee2e6;border-radius:8px;margin-bottom:28px;overflow:hidden}
.card-header{background:#343a40;color:#fff;padding:10px 16px;font-weight:700;display:flex;align-items:center;gap:12px}
.badge{font-size:11px;padding:2px 7px;border-radius:4px;background:#6c757d}
.badge.market{background:#0d6efd}
table{width:100%;border-collapse:collapse}
th{background:#f1f3f5;font-weight:600;padding:6px 10px;text-align:center;font-size:12px;border-bottom:2px solid #dee2e6}
td{padding:5px 10px;border-bottom:1px solid #f0f0f0;text-align:center;font-size:12px}
td.label{text-align:left;color:#495057;font-weight:500;padding-left:16px}
td.section{text-align:left;font-weight:700;background:#e9ecef;color:#343a40;padding:5px 10px}
.ok{color:#198754;font-weight:700}
.fail{color:#dc3545;font-weight:700}
.na{color:#adb5bd}
.final-ok{background:#d1e7dd;font-weight:700;color:#0f5132}
.final-fail{background:#f8d7da;font-weight:700;color:#842029}
.sig-label{font-size:11px;padding:1px 5px;border-radius:3px;background:#e9ecef;color:#495057}
.no-data{color:#adb5bd;font-style:italic}
.error{color:#dc3545;padding:12px 16px;font-style:italic}
"""

_P3_KEYS  = ["가격유지", "TV최소", "구조붕괴없음", "최종"]
_HTC_KEYS = ["가격(고가기준)", "가격(종가기준)", "중간일최저종가",
             "TV평균수축", "TV오늘수축", "TV최소(300억)",
             "고저변동폭", "종가변동폭",
             "구조붕괴없음", "몸통중간값유지", "장대음봉없음", "최종"]
_KH_KEYS  = ["기준봉TV폭발", "기준봉TV최소", "TV수축", "가격유지",
             "5일선위", "신고가권", "거래량증가음봉없음", "최종"]


def _cell(item: dict | None, key: str) -> str:
    if item is None:
        return '<td class="na">-</td>'
    v = item.get(key)
    if v is None:
        return '<td class="na">-</td>'
    ok  = v["ok"]
    val = v["value"]
    is_final = key == "최종"
    cls = ("final-ok" if ok else "final-fail") if is_final else ("ok" if ok else "fail")
    return f'<td class="{cls}">{val}</td>'


def build_html(results: list[dict], days: int) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    rows_html = ""

    for ev in results:
        name   = ev["name"]
        code   = ev["code"]
        market = ev["market"]
        bd     = ev["base_date"].strftime("%m/%d")
        b_tv   = ev.get("base_tv_won", 0)
        b_tv_s = f"{b_tv / 1e8:.0f}억" if b_tv else "-"
        b_ch   = ev.get("base_change_pct", 0)

        rows_html += f"""
<div class="card">
  <div class="card-header">
    {name}
    <span class="badge market">{market}</span>
    <span class="badge">{code}</span>
    <span style="font-size:12px;font-weight:400">기준봉: {bd} ({b_ch:+.1f}%) | 거래대금: {b_tv_s}</span>
  </div>
"""
        if ev.get("error"):
            rows_html += f'<p class="error">⚠ {ev["error"]}</p></div>\n'
            continue

        days_data = ev.get("days", {})
        d_range   = range(1, 6)

        # 테이블
        rows_html += '<table><thead><tr><th style="text-align:left;min-width:160px">조건</th>'
        for i in d_range:
            dd = days_data.get(i)
            if dd:
                ds   = dd["date"].strftime("%m/%d")
                chg  = dd.get("change_pct", 0)
                tv_s = f"{dd['tv_won']/1e8:.0f}억"
                src  = "●" if dd["tv_source"] == "시그널" else "○"
                sp   = dd.get("sig_pattern", "")
                sig_html = f'<br><span class="sig-label">{sp}</span>' if sp and sp not in ("시그널없음","N/A") else ""
                rows_html += f'<th>D+{i}<br>{ds}<br><span style="color:{"#198754" if chg>=0 else "#dc3545"}">{chg:+.1f}%</span> {tv_s}{src}{sig_html}</th>'
            else:
                rows_html += f'<th>D+{i}<br><span class="no-data">데이터없음</span></th>'
        rows_html += "</tr></thead><tbody>\n"

        def section(label):
            return f'<tr><td class="section" colspan="{1+len(list(d_range))}">{label}</td></tr>\n'

        def data_row(key, p_key):
            r = f'<tr><td class="label">{key}</td>'
            for i in d_range:
                dd = days_data.get(i)
                r += _cell(dd.get(p_key) if dd else None, key)
            r += "</tr>\n"
            return r

        # 고가횡보형
        rows_html += section("📊 고가횡보형")
        for k in _P3_KEYS:
            rows_html += data_row(k, "p3")

        # 고가수축형
        rows_html += section("📊 고가수축형 (HTC)")
        for k in _HTC_KEYS:
            rows_html += data_row(k, "htc")

        # KH
        rows_html += section("📊 김형준 기법 (KH)")
        for k in _KH_KEYS:
            rows_html += data_row(k, "kh")

        rows_html += "</tbody></table></div>\n"

    total = len(results)
    errors = sum(1 for r in results if r.get("error"))

    html = f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8">
<title>패턴 탈락 사유 분석</title>
<style>{_CSS}</style>
</head><body>
<h1>📉 기준봉 → 패턴 탈락 사유 분석</h1>
<p class="meta">분석 기간: 최근 {days}일 | 기준봉 이벤트: {total}건 (데이터오류: {errors}건) | 생성: {now}</p>
<p class="meta">● 시그널파일 TV(정확) &nbsp; ○ yfinance TV(근사) &nbsp; D+N 헤더 색상: 전일 대비 등락률</p>
{rows_html}
</body></html>"""
    return html


# ── 메인 ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="기준봉 패턴 탈락 사유 분석")
    parser.add_argument("--days",  type=int, default=21, help="분석 기간 (일, 기본 21)")
    parser.add_argument("--open",  action="store_true",  help="완료 후 브라우저 자동 열기")
    args = parser.parse_args()

    print(f"[1/4] 최근 {args.days}일 1750 시그널 파일 로드 중...")
    signals = load_1750_signals(args.days)
    print(f"      {len(signals)}개 날짜 로드 완료")

    print("[2/4] 당일돌파형 이벤트 추출 중...")
    events = find_base_candle_events(signals)
    print(f"      {len(events)}건 기준봉 이벤트 발견")

    if not events:
        print("      기준봉 이벤트 없음. 종료.")
        return

    print("[3/4] D+1~D+5 조건 분석 중 (yfinance 조회)...")
    results = []
    for i, ev in enumerate(events, 1):
        print(f"      ({i}/{len(events)}) {ev['name']} ({ev['code']}) {ev['base_date']}")
        results.append(analyze_event(ev, signals))

    print("[4/4] HTML 리포트 생성 중...")
    html = build_html(results, args.days)
    today_str = datetime.now().strftime("%Y-%m-%d")
    out_path  = REPORTS_DIR / f"pattern_failure_{today_str}.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"      저장 완료: {out_path}")

    if args.open:
        webbrowser.open(out_path.as_uri())


if __name__ == "__main__":
    main()
