"""
scripts/pattern_diagnostics.py
기준봉 발생 후 KH / 고가횡보형 / 고가수축형 탈락 사유 진단.

사용법:
    python -m scripts.pattern_diagnostics
"""

import sys
import logging
from pathlib import Path
from collections import Counter, defaultdict

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import (
    HIGH_RANGE_HOLD_MAX_GAP_FROM_BASE_HIGH_PCT,
    OVERHEATED_GAP_FROM_BASE_HIGH_PCT,
    STRUCTURE_BREAK_MAX_GAP_PCT,
    TV_RATIO_WATCH_MIN, TV_RATIO_P3_MAX,
    HTC_BASE_LOOKBACK_DAYS,
    HTC_POST_AVG_TV_RATIO_MAX, HTC_TODAY_TV_RATIO_MAX,
    HTC_MIN_TODAY_TV_EOK,
    HTC_CLOSE_FROM_BASE_HIGH_MIN_PCT, HTC_CLOSE_FROM_BASE_CLOSE_MIN_PCT,
    HTC_LOWEST_CLOSE_FROM_BASE_CLOSE_MIN_PCT,
    HTC_RANGE_MAX_PCT, HTC_CLOSE_RANGE_MAX_PCT,
    HTC_STRUCTURE_BREAK_FROM_BASE_HIGH_PCT,
    KH_BASE_TV_EXPLOSION_MULT, KH_BASE_TV_MIN_EOK,
    KH_TODAY_TV_RATIO_MAX, KH_CLOSE_FROM_BASE_HIGH_MIN_PCT,
    KH_BASE_LOOKBACK_DAYS,
    KH_VOLUME_UP_BEARISH_RATIO, KH_SQUEEZE_CANDLE_BODY_MAX_RATIO,
    BASE_TV_EXPLOSION_MULT,
    SIGNALS_DIR,
)
from scripts.fetch_stock_data import fetch_chart_data

logging.basicConfig(level=logging.WARNING)

# ── 최근 기준봉 종목 (signals 파일 + 수동 입력) ────────────────────────
# format: (code, signal_date_str)
MANUAL_BASE_STOCKS = [
    ("356680", "2026-05-22"),  # 엑스게이트
    ("036930", "2026-05-22"),  # 주성엔지니어링
    ("009150", "2026-05-22"),  # 삼성전기
    ("086520", "2026-05-22"),  # 에코프로
    ("247540", "2026-05-22"),  # 에코프로비엠
    ("009150", "2026-05-26"),  # 삼성전기
    ("011070", "2026-05-26"),  # LG이노텍
    ("042660", "2026-05-26"),  # 한화오션
    ("007660", "2026-05-26"),  # 이수페타시스
    ("010170", "2026-05-26"),  # 대한광통신
    ("307950", "2026-05-27"),  # 현대오토에버
    ("064400", "2026-05-27"),  # LG씨엔에스
    ("009420", "2026-05-27"),  # 한올바이오파마
    ("001820", "2026-05-28"),  # 삼화콘덴서
    ("373220", "2026-05-28"),  # LG에너지솔루션
    ("009150", "2026-05-28"),  # 삼성전기
    ("066970", "2026-05-28"),  # 엘앤에프
]


def _load_signals_base_stocks() -> list[tuple[str, str]]:
    """로컬 signals 파일에서 당일돌파형 종목 수집."""
    result = []
    for f in sorted(SIGNALS_DIR.glob("*_1750_signals.csv")):
        import re
        m = re.match(r"^(\d{4}-\d{2}-\d{2})_", f.name)
        if not m:
            continue
        date_str = m.group(1)
        try:
            df = pd.read_csv(f, dtype={"종목코드": str})
            if "pattern_type_label" not in df.columns or "종목코드" not in df.columns:
                continue
            for code in df.loc[df["pattern_type_label"] == "당일돌파형", "종목코드"].dropna():
                result.append((str(code).zfill(6), date_str))
        except Exception:
            continue
    return result


def _fetch_ohlcv(code: str) -> pd.DataFrame:
    """yfinance로 3개월 OHLCV 수집 (KS 우선, KQ fallback)."""
    for suffix in [".KS", ".KQ"]:
        try:
            ticker = yf.Ticker(code + suffix)
            df = ticker.history(period="3mo", auto_adjust=True)
            if df.empty or len(df) < 10:
                continue
            df = df.rename(columns={
                "Open": "open", "High": "high", "Low": "low",
                "Close": "close", "Volume": "volume",
            })
            df["trading_value"] = df["close"] * df["volume"]
            df["change_pct"] = df["close"].pct_change() * 100
            df["date"] = df.index.strftime("%Y.%m.%d")
            df = df.reset_index(drop=True).iloc[::-1].reset_index(drop=True)
            return df
        except Exception:
            continue
    return pd.DataFrame()


def _find_base_idx_in_df(df: pd.DataFrame, signal_date: str, max_offset: int) -> int | None:
    """signal_date에 해당하는 df 인덱스를 찾아 반환. date 컬럼 형식: 'YYYY.MM.DD'."""
    date_dotted = signal_date.replace("-", ".")
    for i in range(len(df)):
        d = str(df.iloc[i].get("date", ""))
        if d == date_dotted:
            return i
    return None


# ── 탈락 사유 트레이서 ────────────────────────────────────────────────
def _find_base_candle(df: pd.DataFrame, start: int, lookback: int):
    """기준봉 탐색 (내부 복사본)."""
    MIN_TV_WON = 1500 * 100_000_000
    for i in range(start, min(start + lookback, len(df))):
        row = df.iloc[i]
        chg = float(row.get("change_pct", row.get("change", 0)) or 0)
        if chg < 10.0:
            continue
        high = float(row.get("high", 0) or 0)
        low  = float(row.get("low",  0) or 0)
        close= float(row.get("close",0) or 0)
        op   = float(row.get("open", 0) or 0)
        if high <= 0 or close <= 0:
            continue
        # 윗꼬리 체크
        if high > close and (high - close) / high * 100 > 5.0:
            continue
        base_tv = float(row.get("trading_value", 0) or 0)
        past = df.iloc[i+1:i+21]["trading_value"].replace(0, float("nan"))
        avg_tv = float(past.mean()) if past.notna().any() else float("nan")
        import math
        if not math.isnan(avg_tv) and avg_tv > 0 and base_tv < avg_tv * BASE_TV_EXPLOSION_MULT:
            continue
        return i
    return None


def _trace_kh(df: pd.DataFrame, today_idx: int) -> str:
    """KH 탈락 사유 1순위 반환. 통과하면 '통과'."""
    today = df.iloc[today_idx]
    today_close = float(today.get("close", 0) or 0)
    today_open  = float(today.get("open",  0) or 0)
    today_tv    = float(today.get("trading_value", 0) or 0)

    # 기준봉 탐색
    sub = df.iloc[today_idx:]
    base_local = _find_base_candle(sub, start=1, lookback=KH_BASE_LOOKBACK_DAYS)
    if base_local is None:
        return "기준봉 없음 (최근 10일 내 장대양봉+3배 미충족)"
    base_row  = sub.iloc[base_local]
    base_high = float(base_row.get("high",          0) or 0)
    base_tv   = float(base_row.get("trading_value", 0) or 0)

    if base_tv < KH_BASE_TV_MIN_EOK * 100_000_000:
        return f"기준봉 TV 최소 미달 (기준봉 {base_tv/1e8:.0f}억 < {KH_BASE_TV_MIN_EOK}억)"

    past_tv  = sub.iloc[base_local+1:base_local+21]["trading_value"].replace(0, float("nan"))
    avg_20d  = float(past_tv.mean()) if past_tv.notna().any() else float("nan")
    import math
    if not math.isnan(avg_20d) and avg_20d > 0 and base_tv < avg_20d * KH_BASE_TV_EXPLOSION_MULT:
        return f"기준봉 TV 폭발 미달 ({base_tv/1e8:.0f}억 < 20일평균{avg_20d/1e8:.0f}억×{KH_BASE_TV_EXPLOSION_MULT}배)"

    tv_ratio = today_tv / base_tv if base_tv > 0 else 0
    if today_tv <= 0 or tv_ratio > KH_TODAY_TV_RATIO_MAX:
        return f"TV 수축 부족 ({tv_ratio*100:.1f}% > {KH_TODAY_TV_RATIO_MAX*100:.0f}%)"

    close_vs = (today_close - base_high) / base_high * 100 if base_high > 0 else -999
    if close_vs < KH_CLOSE_FROM_BASE_HIGH_MIN_PCT:
        return f"기준봉 고가 이탈 ({close_vs:.1f}% < {KH_CLOSE_FROM_BASE_HIGH_MIN_PCT}%)"

    # 5일선
    try:
        recent_5 = df["close"].iloc[today_idx:today_idx+5].replace(0, float("nan"))
        ma5 = float(recent_5.mean()) if recent_5.notna().sum() >= 5 else None
    except Exception:
        ma5 = None
    if ma5 is not None and today_close < ma5:
        return f"5일선 하회 (종가 {today_close:,.0f} < MA5 {ma5:,.0f})"

    # 거래량 증가 음봉
    if today_close < today_open and today_tv >= base_tv * KH_VOLUME_UP_BEARISH_RATIO:
        return f"거래량 증가 음봉 ({tv_ratio*100:.1f}% ≥ {KH_VOLUME_UP_BEARISH_RATIO*100:.0f}%)"

    # 짧은 캔들 체크
    t_high = float(today.get("high", 0) or 0)
    t_low  = float(today.get("low",  0) or 0)
    rng = t_high - t_low
    if rng > 0:
        body = abs(today_close - today_open) / rng
        if body > KH_SQUEEZE_CANDLE_BODY_MAX_RATIO:
            return f"캔들 몸통 너무 큼 ({body*100:.1f}% > {KH_SQUEEZE_CANDLE_BODY_MAX_RATIO*100:.0f}%)"

    # 60일 신고가 근접
    past_highs = df["high"].iloc[today_idx+1:today_idx+61].replace(0, float("nan")).dropna()
    high_60d = float(past_highs.max()) if not past_highs.empty else 0
    near = high_60d > 0 and today_close >= high_60d * 0.97
    if not near:
        return f"60일 신고가 근처 아님 (종가 {today_close:,.0f}, 60일고가 {high_60d:,.0f})"

    return "통과"


def _trace_p3(df: pd.DataFrame, today_idx: int) -> str:
    """고가횡보형 탈락 사유 반환."""
    today_close = float(df.iloc[today_idx].get("close", 0) or 0)
    today_tv    = float(df.iloc[today_idx].get("trading_value", 0) or 0)

    sub = df.iloc[today_idx:]
    base_local = _find_base_candle(sub, start=1, lookback=HTC_BASE_LOOKBACK_DAYS)
    if base_local is None:
        return "기준봉 없음 (최근 5일 내 장대양봉+3배 미충족)"

    base_row  = sub.iloc[base_local]
    base_high = float(base_row.get("high",          0) or 0)
    base_tv   = float(base_row.get("trading_value", 0) or 0)

    # 구조 붕괴
    for i in range(1, base_local):
        dc = float(sub.iloc[i].get("close", 0) or 0)
        if dc > 0 and (base_high - dc) / base_high * 100 > STRUCTURE_BREAK_MAX_GAP_PCT:
            return f"구조 붕괴 (중간일 종가 {dc:,.0f}, 기준봉 고가 -{(base_high-dc)/base_high*100:.1f}%)"

    gap = (today_close - base_high) / base_high * 100 if base_high > 0 else -999

    if gap < -HIGH_RANGE_HOLD_MAX_GAP_FROM_BASE_HIGH_PCT:
        return f"고가 이탈 (기준봉 고가 대비 {gap:.1f}% < -{HIGH_RANGE_HOLD_MAX_GAP_FROM_BASE_HIGH_PCT}%)"

    if gap > OVERHEATED_GAP_FROM_BASE_HIGH_PCT:
        return f"과확장 (기준봉 고가 대비 +{gap:.1f}% > +{OVERHEATED_GAP_FROM_BASE_HIGH_PCT}%)"

    tv_ratio = today_tv / base_tv if base_tv > 0 else 0
    if tv_ratio < TV_RATIO_WATCH_MIN:
        return f"TV 너무 적음 ({tv_ratio*100:.1f}% < {TV_RATIO_WATCH_MIN*100:.0f}%)"
    if tv_ratio > TV_RATIO_P3_MAX:
        return f"TV 과다 (재상승 의심, {tv_ratio*100:.1f}% > {TV_RATIO_P3_MAX*100:.0f}%)"

    return "통과"


def _trace_htc(df: pd.DataFrame, today_idx: int) -> str:
    """고가수축형 탈락 사유 반환."""
    today     = df.iloc[today_idx]
    today_close = float(today.get("close", 0) or 0)
    today_tv    = float(today.get("trading_value", 0) or 0)

    sub = df.iloc[today_idx:]
    base_local = _find_base_candle(sub, start=1, lookback=HTC_BASE_LOOKBACK_DAYS)
    if base_local is None:
        return "기준봉 없음 (최근 5일 내 장대양봉+3배 미충족)"

    base_row   = sub.iloc[base_local]
    base_high  = float(base_row.get("high",          0) or 0)
    base_close = float(base_row.get("close",         0) or 0)
    base_open  = float(base_row.get("open",          0) or 0)
    base_tv    = float(base_row.get("trading_value", 0) or 0)

    post_idx    = list(range(0, base_local))
    post_tvs    = [float(sub.iloc[i].get("trading_value", 0) or 0) for i in post_idx]
    post_closes = [float(sub.iloc[i].get("close",         0) or 0) for i in post_idx]
    post_highs  = [float(sub.iloc[i].get("high",          0) or 0) for i in post_idx]
    post_lows   = [float(sub.iloc[i].get("low",           0) or 0) for i in post_idx]

    close_from_base_high = (today_close - base_high) / base_high * 100 if base_high > 0 else -999
    if close_from_base_high < HTC_CLOSE_FROM_BASE_HIGH_MIN_PCT:
        return f"종가 기준봉 고가 이탈 ({close_from_base_high:.1f}% < {HTC_CLOSE_FROM_BASE_HIGH_MIN_PCT}%)"

    close_from_base_close = (today_close - base_close) / base_close * 100 if base_close > 0 else -999
    if close_from_base_close < HTC_CLOSE_FROM_BASE_CLOSE_MIN_PCT:
        return f"종가 기준봉 종가 이탈 ({close_from_base_close:.1f}% < {HTC_CLOSE_FROM_BASE_CLOSE_MIN_PCT}%)"

    if post_closes:
        min_close = min(c for c in post_closes if c > 0) if any(c > 0 for c in post_closes) else 0
        if min_close > 0 and base_close > 0:
            min_c_pct = (min_close - base_close) / base_close * 100
            if min_c_pct < HTC_LOWEST_CLOSE_FROM_BASE_CLOSE_MIN_PCT:
                return f"중간일 최저 종가 과다 이탈 ({min_c_pct:.1f}% < {HTC_LOWEST_CLOSE_FROM_BASE_CLOSE_MIN_PCT}%)"

    avg_tv = sum(post_tvs) / len(post_tvs) if post_tvs else 0
    tv_ratio_avg   = avg_tv   / base_tv if base_tv > 0 else 0
    tv_ratio_today = today_tv / base_tv if base_tv > 0 else 0

    if tv_ratio_avg > HTC_POST_AVG_TV_RATIO_MAX:
        return f"평균 TV 수축 부족 ({tv_ratio_avg*100:.1f}% > {HTC_POST_AVG_TV_RATIO_MAX*100:.0f}%)"
    if tv_ratio_today > HTC_TODAY_TV_RATIO_MAX:
        return f"오늘 TV 수축 부족 ({tv_ratio_today*100:.1f}% > {HTC_TODAY_TV_RATIO_MAX*100:.0f}%)"
    if today_tv < HTC_MIN_TODAY_TV_EOK * 100_000_000:
        return f"TV 최소 미달 ({today_tv/1e8:.0f}억 < {HTC_MIN_TODAY_TV_EOK}억)"

    max_h = max(post_highs) if post_highs else 0
    min_l = min(post_lows)  if post_lows  else 0
    range_pct = (max_h - min_l) / max_h * 100 if max_h > 0 else 0
    max_c = max(post_closes) if post_closes else 0
    min_c = min(c for c in post_closes if c > 0) if any(c > 0 for c in post_closes) else 0
    close_range_pct = (max_c - min_c) / max_c * 100 if max_c > 0 else 0

    if range_pct > HTC_RANGE_MAX_PCT:
        return f"고저 변동폭 과다 ({range_pct:.1f}% > {HTC_RANGE_MAX_PCT}%)"
    if close_range_pct > HTC_CLOSE_RANGE_MAX_PCT:
        return f"종가 변동폭 과다 ({close_range_pct:.1f}% > {HTC_CLOSE_RANGE_MAX_PCT}%)"

    # 중간일 구조 붕괴
    base_body_mid = (base_open + base_close) / 2
    for i in post_idx[1:]:
        d = sub.iloc[i]
        d_close = float(d.get("close", 0) or 0)
        d_open  = float(d.get("open",  0) or 0)
        d_chg   = float(d.get("change_pct", d.get("change", 0)) or 0)
        d_tv    = float(d.get("trading_value", 0) or 0)
        if base_high > 0 and d_close > 0:
            if (d_close - base_high) / base_high * 100 < HTC_STRUCTURE_BREAK_FROM_BASE_HIGH_PCT:
                return f"중간일 기준봉 고가 이탈 ({(d_close-base_high)/base_high*100:.1f}% < {HTC_STRUCTURE_BREAK_FROM_BASE_HIGH_PCT}%)"
        if d_close > 0 and base_body_mid > 0 and d_close < base_body_mid:
            return f"중간일 몸통 중심선 하회 (종가 {d_close:,.0f} < 기준봉 몸통중심 {base_body_mid:,.0f})"
        if d_close < d_open and d_chg <= -5.0 and d_tv >= base_tv * 0.5:
            return f"중간일 장대음봉 발생"

    return "통과"


# ── 메인 진단 ────────────────────────────────────────────────────────
def run():
    # 1. 기준봉 종목 수집
    signals_stocks = _load_signals_base_stocks()
    all_stocks = signals_stocks + MANUAL_BASE_STOCKS
    # 중복 제거 (code+date 기준)
    seen = set()
    unique_stocks = []
    for item in all_stocks:
        key = (item[0], item[1])
        if key not in seen:
            seen.add(key)
            unique_stocks.append(item)

    print(f"\n{'='*60}")
    print(f" 기준봉 종목 진단 리포트")
    print(f"{'='*60}")
    print(f"총 기준봉 발생: {len(unique_stocks)}건 ({len(set(c for c,_ in unique_stocks))}개 종목)")

    # 2. 각 종목별 탈락 사유 수집
    kh_reasons:  Counter = Counter()
    p3_reasons:  Counter = Counter()
    htc_reasons: Counter = Counter()

    kh_pass  = []
    p3_pass  = []
    htc_pass = []

    no_data_count  = 0
    total_checks   = 0
    pool_eligible  = 0   # obs 풀 진입 가능 여부 (기준봉 이후 D+1~D+5)

    import re as _re
    from datetime import datetime, timedelta

    for code, signal_date in unique_stocks:
        df = _fetch_ohlcv(code)
        if df.empty or len(df) < 5:
            no_data_count += 1
            continue

        # signal_date 이후 D+1~D+10 체크
        date_dotted = signal_date.replace("-", ".")
        base_local_idx = None
        for i in range(len(df)):
            d = str(df.iloc[i].get("date", ""))
            if d == date_dotted:
                base_local_idx = i
                break

        if base_local_idx is None:
            # 날짜 매칭 실패시 skip
            no_data_count += 1
            continue

        # D+1 ~ D+10: df 인덱스는 base_local_idx-1 (전날) down to 0
        for d_offset in range(1, min(6, base_local_idx + 1)):
            today_idx = base_local_idx - d_offset
            if today_idx < 0:
                break

            pool_eligible += 1
            total_checks  += 1

            kh_r  = _trace_kh(df,  today_idx)
            p3_r  = _trace_p3(df,  today_idx)
            htc_r = _trace_htc(df, today_idx)

            if kh_r  != "통과": kh_reasons[kh_r]   += 1
            else:               kh_pass.append((code, signal_date, d_offset))

            if p3_r  != "통과": p3_reasons[p3_r]   += 1
            else:               p3_pass.append((code, signal_date, d_offset))

            if htc_r != "통과": htc_reasons[htc_r] += 1
            else:               htc_pass.append((code, signal_date, d_offset))

    print(f"데이터 조회 성공: {len(unique_stocks) - no_data_count}개 종목")
    print(f"총 일별 체크 횟수: {total_checks}건")
    print(f"KH 통과: {len(kh_pass)}건 / P3 통과: {len(p3_pass)}건 / HTC 통과: {len(htc_pass)}건")

    # ── 3. KH 탈락 사유 ──────────────────────────────────────────────
    print(f"\n{'─'*55}")
    print(f" [KH 김형준기법] 탈락 사유 Top5  (총 {sum(kh_reasons.values())}건 탈락)")
    print(f"{'─'*55}")
    for i, (reason, cnt) in enumerate(kh_reasons.most_common(5), 1):
        pct = cnt / total_checks * 100
        print(f"  {i}위 ({cnt}건, {pct:.1f}%): {reason}")
    if kh_pass:
        print(f"\n  ★ KH 통과 {len(kh_pass)}건:")
        for code, sdate, offset in kh_pass[:5]:
            print(f"     {code} / 기준봉:{sdate} / D+{offset}")

    # ── 4. 고가횡보형 탈락 사유 ──────────────────────────────────────
    print(f"\n{'─'*55}")
    print(f" [고가횡보형] 탈락 사유 Top5  (총 {sum(p3_reasons.values())}건 탈락)")
    print(f"{'─'*55}")
    for i, (reason, cnt) in enumerate(p3_reasons.most_common(5), 1):
        pct = cnt / total_checks * 100
        print(f"  {i}위 ({cnt}건, {pct:.1f}%): {reason}")
    if p3_pass:
        print(f"\n  ★ 고가횡보형 통과 {len(p3_pass)}건:")
        for code, sdate, offset in p3_pass[:5]:
            print(f"     {code} / 기준봉:{sdate} / D+{offset}")

    # ── 5. 고가수축형 탈락 사유 ──────────────────────────────────────
    print(f"\n{'─'*55}")
    print(f" [고가수축형] 탈락 사유 Top5  (총 {sum(htc_reasons.values())}건 탈락)")
    print(f"{'─'*55}")
    for i, (reason, cnt) in enumerate(htc_reasons.most_common(5), 1):
        pct = cnt / total_checks * 100
        print(f"  {i}위 ({cnt}건, {pct:.1f}%): {reason}")
    if htc_pass:
        print(f"\n  ★ 고가수축형 통과 {len(htc_pass)}건:")
        for code, sdate, offset in htc_pass[:5]:
            print(f"     {code} / 기준봉:{sdate} / D+{offset}")

    # ── 6. 진단 요약 ─────────────────────────────────────────────────
    print(f"\n{'─'*55}")
    print(f" [진단 요약]")
    print(f"{'─'*55}")
    total_kh_fail  = sum(kh_reasons.values())
    total_p3_fail  = sum(p3_reasons.values())
    total_htc_fail = sum(htc_reasons.values())

    no_base_kh  = kh_reasons.get("기준봉 없음 (최근 10일 내 장대양봉+3배 미충족)", 0)
    no_base_p3  = p3_reasons.get("기준봉 없음 (최근 5일 내 장대양봉+3배 미충족)", 0)
    no_base_htc = htc_reasons.get("기준봉 없음 (최근 5일 내 장대양봉+3배 미충족)", 0)

    print(f"  KH:  기준봉 없어서 탈락 {no_base_kh/total_checks*100:.1f}% / 조건 탈락 {(total_kh_fail-no_base_kh)/total_checks*100:.1f}%")
    print(f"  P3:  기준봉 없어서 탈락 {no_base_p3/total_checks*100:.1f}% / 조건 탈락 {(total_p3_fail-no_base_p3)/total_checks*100:.1f}%")
    print(f"  HTC: 기준봉 없어서 탈락 {no_base_htc/total_checks*100:.1f}% / 조건 탈락 {(total_htc_fail-no_base_htc)/total_checks*100:.1f}%")


if __name__ == "__main__":
    run()
