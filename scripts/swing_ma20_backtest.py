"""
scripts/swing_ma20_backtest.py
기준봉 이후 MA20 유지/MA10 청산 스윙 백테스트 + 진입 기준별 필터 분석.

진입: D+1 시가  |  MA10 청산: 종가 < MA10  |  MA20 이탈: 종가 < MA20
최대 추적: 20거래일

사용법:
    python -m scripts.swing_ma20_backtest
"""

import re
import sys
import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import SIGNALS_DIR

logging.basicConfig(level=logging.WARNING)

MAX_TRACK_DAYS = 20  # 최대 추적 거래일

# ── 수동 기준봉 입력 ─────────────────────────────────────────────────────
# signals 파일이 없을 때 직접 입력. (code, signal_date, name)
# signal_date = 기준봉(당일돌파형) 발생 날짜
MANUAL_BASE_STOCKS: list[tuple[str, str, str]] = [
    # ── 4월 하순 (진단 스크립트 KH/P3/HTC 통과 종목) ──
    ("042700", "2026-04-27", "한미반도체"),
    ("460860", "2026-04-27", "460860"),
    ("006340", "2026-04-29", "006340"),
    ("096770", "2026-04-29", "SK이노베이션"),
    ("011170", "2026-04-29", "011170"),
    # ── 5월 22~28 당일돌파형 ──
    ("356680", "2026-05-22", "엑스게이트"),
    ("036930", "2026-05-22", "주성엔지니어링"),
    ("009150", "2026-05-22", "삼성전기"),
    ("086520", "2026-05-22", "에코프로"),
    ("247540", "2026-05-22", "에코프로비엠"),
    ("009150", "2026-05-26", "삼성전기"),
    ("011070", "2026-05-26", "LG이노텍"),
    ("042660", "2026-05-26", "한화오션"),
    ("007660", "2026-05-26", "이수페타시스"),
    ("010170", "2026-05-26", "대한광통신"),
    ("307950", "2026-05-27", "현대오토에버"),
    ("064400", "2026-05-27", "LG씨엔에스"),
    ("009420", "2026-05-27", "한올바이오파마"),
    ("001820", "2026-05-28", "삼화콘덴서"),
    ("373220", "2026-05-28", "LG에너지솔루션"),
    ("009150", "2026-05-28", "삼성전기"),
    ("066970", "2026-05-28", "엘앤에프"),
]


# ── 메타데이터 ────────────────────────────────────────────────────────────

@dataclass
class SignalMeta:
    in_inter: bool              # 교집합(상승률 Top20 AND 거래대금 Top20)
    trading_value_eok: float    # 거래대금 (억)
    change_pct: float           # 등락률 (%)
    supply_label: str           # 수급 라벨 (★쌍매수, ★기관매수 …)
    checklist_pass: int         # 필수조건 통과 수 (3 or 4)
    total_score: int            # 총점
    sector: str                 # 섹터명
    sector_comovement: bool     # 당일 동일 섹터 ≥2 종목 신호 여부


# ── 데이터 수집 ──────────────────────────────────────────────────────────

def _fetch_ohlcv(code: str) -> pd.DataFrame:
    """yfinance 6개월 OHLCV (시간순, index 0 = 가장 과거) + MA5/MA10/MA20 계산."""
    for suffix in [".KS", ".KQ"]:
        try:
            ticker = yf.Ticker(code + suffix)
            df = ticker.history(period="6mo", auto_adjust=True)
            if df.empty or len(df) < 25:
                continue
            df = df.rename(columns={
                "Open": "open", "High": "high", "Low": "low",
                "Close": "close", "Volume": "volume",
            })
            df["ma5"]  = df["close"].rolling(5,  min_periods=4).mean()
            df["ma10"] = df["close"].rolling(10, min_periods=8).mean()
            df["ma20"] = df["close"].rolling(20, min_periods=15).mean()
            df["date"] = df.index.strftime("%Y.%m.%d")
            return df.reset_index(drop=True)
        except Exception:
            continue
    return pd.DataFrame()


def _load_signals_stocks() -> list[tuple[str, str, str, Optional[SignalMeta]]]:
    """signals 전체에서 당일돌파형 + 메타데이터 수집 (중복 제거)."""
    # 1단계: 전체 rows 수집 + 섹터 동반 카운트
    all_rows: list[tuple[str, str, str, dict]] = []
    sector_daily: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for f in sorted(SIGNALS_DIR.glob("*_signals.csv")):
        m = re.match(r"^(\d{4}-\d{2}-\d{2})_", f.name)
        if not m:
            continue
        date_str = m.group(1)
        try:
            df = pd.read_csv(f, dtype={"종목코드": str})
            if "pattern_type_label" not in df.columns:
                continue
            mask = df["pattern_type_label"] == "당일돌파형"
            for _, row in df[mask].iterrows():
                code = str(row.get("종목코드", "")).zfill(6)
                sector = str(row.get("sector", "") or "")
                if sector:
                    sector_daily[date_str][sector] += 1
                name = str(row.get("종목명", code))
                all_rows.append((code, date_str, name, dict(row)))
        except Exception:
            continue

    # 2단계: 중복 제거 + SignalMeta 생성
    seen: set[tuple[str, str]] = set()
    result: list[tuple[str, str, str, Optional[SignalMeta]]] = []

    for code, date_str, name, row in all_rows:
        key = (code, date_str)
        if key in seen:
            continue
        seen.add(key)

        sector = str(row.get("sector", "") or "")
        sector_co = bool(sector and sector_daily[date_str].get(sector, 0) >= 2)

        try:
            in_inter = str(row.get("in_inter", "")).strip().lower() in ("true", "1", "yes")
            raw_tv   = float(row.get("거래대금", 0) or 0)
            tv_eok   = raw_tv / 1e8 if raw_tv > 1e6 else raw_tv   # 원→억 (이미 억이면 그대로)
            chg      = float(row.get("등락률", 0) or 0)
            supply   = str(row.get("supply_label", "") or "")
            ck       = int(float(row.get("checklist_pass", 3) or 3))
            score    = int(float(row.get("total_score", 0) or 0))
        except Exception:
            in_inter, tv_eok, chg, supply, ck, score = False, 0.0, 0.0, "", 3, 0

        meta = SignalMeta(
            in_inter=in_inter,
            trading_value_eok=tv_eok,
            change_pct=chg,
            supply_label=supply,
            checklist_pass=ck,
            total_score=score,
            sector=sector,
            sector_comovement=sector_co,
        )
        result.append((code, date_str, name, meta))

    return result


# ── 개별 종목 분석 ───────────────────────────────────────────────────────

@dataclass
class SwingResult:
    code: str
    name: str
    signal_date: str
    entry_price: float
    ma20_gap_at_entry: float   # 기준봉 종가 vs MA20 이격 %
    # MA20 추적
    ma20_streak: int
    ma20_max_gain_pct: float
    ma20_exit_pct: float
    ma20_status: str
    # MA10 청산
    ma10_days: int
    ma10_max_gain_pct: float
    ma10_exit_pct: float
    ma10_status: str
    # D+1 특성 (필터 기준 5, 6)
    d1_open_gap_pct: float     # D+1 시가 vs 기준봉 종가 (%)
    d1_close_above_base: bool  # D+1 종가 > 기준봉 종가
    d1_above_ma5: bool         # D+1 종가 >= MA5


def _analyze(code: str, name: str, signal_date: str, df: pd.DataFrame) -> Optional[SwingResult]:
    """기준봉 이후 MA20 유지 + MA10 청산 이중 추적."""
    date_dotted = signal_date.replace("-", ".")
    base_idx = None
    for i in range(len(df)):
        if str(df.iloc[i]["date"]) == date_dotted:
            base_idx = i
            break
    if base_idx is None or base_idx + 1 >= len(df):
        return None

    # 기준봉 데이터
    base_close = float(df.iloc[base_idx].get("close") or 0)
    base_ma20  = float(df.iloc[base_idx].get("ma20") or 0)
    ma20_gap   = (base_close - base_ma20) / base_ma20 * 100 if base_ma20 > 0 else 0.0

    # D+1 시가 진입
    entry_row   = df.iloc[base_idx + 1]
    entry_price = float(entry_row.get("open") or 0)
    if entry_price <= 0:
        entry_price = float(entry_row.get("close") or 0)
    if entry_price <= 0:
        return None

    # D+1 특성 계산
    d1_close  = float(entry_row.get("close") or 0)
    d1_ma5    = float(entry_row.get("ma5") or 0)
    d1_open_gap_pct    = (entry_price - base_close) / base_close * 100 if base_close > 0 else 0.0
    d1_close_above_base = d1_close > base_close if d1_close > 0 else False
    d1_above_ma5        = (d1_close >= d1_ma5) if (d1_close > 0 and d1_ma5 > 0) else False

    # ── MA20 추적 ──────────────────────────────────
    ma20_streak, ma20_max_high, ma20_last_close = 0, entry_price, entry_price
    ma20_status = "MA20유지중"
    for d in range(1, MAX_TRACK_DAYS + 1):
        i = base_idx + d
        if i >= len(df):
            break
        row   = df.iloc[i]
        close = float(row.get("close") or 0)
        high  = float(row.get("high")  or 0)
        ma20  = float(row.get("ma20")  or 0)
        if close <= 0 or ma20 <= 0:
            ma20_status = "데이터부족"
            break
        ma20_max_high   = max(ma20_max_high, high)
        ma20_last_close = close
        if close < ma20:
            ma20_status = f"MA20이탈D+{d}"
            break
        ma20_streak += 1

    # ── MA10 청산 추적 ──────────────────────────────
    ma10_days, ma10_max_high, ma10_last_close = 0, entry_price, entry_price
    ma10_status = "유지중"
    for d in range(1, MAX_TRACK_DAYS + 1):
        i = base_idx + d
        if i >= len(df):
            break
        row   = df.iloc[i]
        close = float(row.get("close") or 0)
        high  = float(row.get("high")  or 0)
        ma10  = float(row.get("ma10")  or 0)
        if close <= 0 or ma10 <= 0:
            ma10_status = "데이터부족"
            break
        ma10_max_high   = max(ma10_max_high, high)
        ma10_last_close = close
        if close < ma10:
            ma10_status = f"MA10청산D+{d}"
            break
        ma10_days += 1
        if d == MAX_TRACK_DAYS:
            ma10_status = "만료(20일)"

    return SwingResult(
        code=code, name=name, signal_date=signal_date,
        entry_price=entry_price,
        ma20_gap_at_entry=ma20_gap,
        ma20_streak=ma20_streak,
        ma20_max_gain_pct=(ma20_max_high - entry_price) / entry_price * 100,
        ma20_exit_pct=(ma20_last_close - entry_price) / entry_price * 100,
        ma20_status=ma20_status,
        ma10_days=ma10_days,
        ma10_max_gain_pct=(ma10_max_high - entry_price) / entry_price * 100,
        ma10_exit_pct=(ma10_last_close - entry_price) / entry_price * 100,
        ma10_status=ma10_status,
        d1_open_gap_pct=d1_open_gap_pct,
        d1_close_above_base=d1_close_above_base,
        d1_above_ma5=d1_above_ma5,
    )


# ── 통계 헬퍼 ────────────────────────────────────────────────────────────

def _summary(label: str, exits: list[float], max_gains: list[float], days: list[int]) -> None:
    """청산 전략 요약 출력 헬퍼."""
    n = len(exits)
    if n == 0:
        return
    win      = sum(1 for x in exits if x > 0)
    avg_exit = sum(exits) / n
    avg_max  = sum(max_gains) / n
    avg_days = sum(days) / n
    pos_exits = [x for x in exits if x > 0]
    neg_exits = [x for x in exits if x <= 0]
    avg_win  = sum(pos_exits) / len(pos_exits) if pos_exits else 0
    avg_loss = sum(neg_exits) / len(neg_exits) if neg_exits else 0
    print(f"\n  [{label}]  n={n}")
    print(f"    승률        : {win}/{n} = {win/n*100:.0f}%")
    print(f"    평균 실현수익: {avg_exit:+.1f}%   (익절평균 {avg_win:+.1f}%  손절평균 {avg_loss:+.1f}%)")
    print(f"    평균 최대수익: {avg_max:+.1f}%   (실현 전 최고점)")
    print(f"    평균 보유일  : {avg_days:.1f}일")
    if avg_loss != 0:
        rr = abs(avg_win / avg_loss)
        print(f"    손익비      : {rr:.2f}  (기댓값 = {win/n*avg_win + (1-win/n)*avg_loss:+.1f}%)")


def _grp(label: str, results: list[SwingResult]) -> str:
    """필터 분석용 한 줄 요약: n / 승률 / 평균실현 / 기댓값."""
    closed = [r for r in results if "MA10청산" in r.ma10_status or "만료" in r.ma10_status]
    n, nc  = len(results), len(closed)
    if nc == 0:
        return f"  {label:<22}: {n:3d}개 (청산{nc:2d})  -- 데이터 부족 --"
    exits  = [r.ma10_exit_pct for r in closed]
    win    = sum(1 for x in exits if x > 0)
    wr     = win / nc
    avg_e  = sum(exits) / nc
    pos    = [x for x in exits if x > 0]
    neg    = [x for x in exits if x <= 0]
    avg_w  = sum(pos) / len(pos) if pos else 0.0
    avg_l  = sum(neg) / len(neg) if neg else 0.0
    ev     = wr * avg_w + (1 - wr) * avg_l
    return (f"  {label:<22}: {n:3d}개(청산{nc:2d})  "
            f"승률={wr*100:4.0f}%  평균={avg_e:+5.1f}%  기댓값={ev:+5.1f}%")


# ── 필터 분석 ─────────────────────────────────────────────────────────────

def _filter_analysis(
    pairs: list[tuple[SwingResult, SignalMeta]],
) -> None:
    """진입 기준별 MA10 청산 성과 비교."""
    results = [r for r, _ in pairs]
    metas   = [m for _, m in pairs]

    def _section(title: str) -> None:
        print(f"\n  {'─'*60}")
        print(f"  {title}")
        print(f"  {'─'*60}")

    print("\n" + "=" * 65)
    print(" [진입 기준별 필터 분석]  MA10 청산 전략 기준  (signals 종목)")
    print("=" * 65)
    print("  형식: n개(청산N)  승률  평균실현  기댓값")
    print("  ※ 청산 = MA10청산 + 만료(20일) / 유지중은 제외")

    # ── 기준1: 교집합 여부 ────────────────────────────────────────
    _section("기준1: 교집합(상승률 Top20 AND 거래대금 Top20)")
    inter     = [r for r, m in pairs if m.in_inter]
    non_inter = [r for r, m in pairs if not m.in_inter]
    print(_grp("교집합 (True)", inter))
    print(_grp("비교집합 (False)", non_inter))

    # ── 기준2: 거래대금 구간 ─────────────────────────────────────
    _section("기준2: 거래대금 구간 (억 단위)")
    tv_bands = [
        ("1500~3000억",  lambda m: 1500 <= m.trading_value_eok < 3000),
        ("3000~5000억",  lambda m: 3000 <= m.trading_value_eok < 5000),
        ("5000~1만억",   lambda m: 5000 <= m.trading_value_eok < 10000),
        ("1만억 이상",   lambda m: m.trading_value_eok >= 10000),
    ]
    for lbl, fn in tv_bands:
        grp = [r for r, m in pairs if fn(m)]
        print(_grp(lbl, grp))

    # ── 기준3: 등락률 구간 ───────────────────────────────────────
    _section("기준3: 당일 등락률 구간")
    chg_bands = [
        ("10~15%",  lambda m: 10 <= m.change_pct < 15),
        ("15~20%",  lambda m: 15 <= m.change_pct < 20),
        ("20~25%",  lambda m: 20 <= m.change_pct < 25),
        ("25~30%",  lambda m: 25 <= m.change_pct <= 30),
    ]
    for lbl, fn in chg_bands:
        grp = [r for r, m in pairs if fn(m)]
        print(_grp(lbl, grp))

    # ── 기준4: 섹터 동반 상승 ────────────────────────────────────
    _section("기준4: 섹터 동반 상승 (당일 동일 섹터 >=2 신호)")
    sect_y = [r for r, m in pairs if m.sector_comovement]
    sect_n = [r for r, m in pairs if not m.sector_comovement]
    print(_grp("섹터 동반 상승 (있음)", sect_y))
    print(_grp("단독 섹터 신호 (없음)", sect_n))

    # ── 기준5: D+1 시가 갭 ───────────────────────────────────────
    _section("기준5: D+1 시가 갭 (vs 기준봉 종가)")
    gap_up   = [r for r in results if r.d1_open_gap_pct >= 0]
    gap_down = [r for r in results if r.d1_open_gap_pct <  0]
    print(_grp("갭업   (시가>기준봉종가)", gap_up))
    print(_grp("갭다운 (시가<기준봉종가)", gap_down))
    # 세분화
    gap_lt5  = [r for r in results if 0 <= r.d1_open_gap_pct < 5]
    gap_5p   = [r for r in results if r.d1_open_gap_pct >= 5]
    gap_dn5  = [r for r in results if -5 <= r.d1_open_gap_pct < 0]
    gap_dn5m = [r for r in results if r.d1_open_gap_pct < -5]
    print(_grp("  갭업 0~5% 미만", gap_lt5))
    print(_grp("  갭업 5%+", gap_5p))
    print(_grp("  갭다운 0~-5%", gap_dn5))
    print(_grp("  갭다운 -5% 초과", gap_dn5m))

    # D+1 종가 vs 기준봉 종가
    cl_up   = [r for r in results if r.d1_close_above_base]
    cl_down = [r for r in results if not r.d1_close_above_base]
    print(_grp("  D+1 종가>기준봉종가", cl_up))
    print(_grp("  D+1 종가<=기준봉종가", cl_down))

    # ── 기준6: D+1 MA5 위 여부 ───────────────────────────────────
    _section("기준6: D+1 종가 >= MA5")
    ma5_y = [r for r in results if r.d1_above_ma5]
    ma5_n = [r for r in results if not r.d1_above_ma5]
    print(_grp("MA5 위 (종가>=MA5)", ma5_y))
    print(_grp("MA5 아래 (종가<MA5)", ma5_n))

    # ── 추가 기준7: 수급 ★ 여부 ─────────────────────────────────
    _section("추가기준7: 수급 강도 (★ 여부)")
    star_y = [r for r, m in pairs if "★" in m.supply_label]
    star_n = [r for r, m in pairs if "★" not in m.supply_label]
    print(_grp("★ 수급 (★쌍매수/★기관/★외인)", star_y))
    print(_grp("비★ 수급", star_n))

    # ── 추가 기준8: checklist_pass ───────────────────────────────
    _section("추가기준8: 필수조건 통과 수")
    ck4 = [r for r, m in pairs if m.checklist_pass >= 4]
    ck3 = [r for r, m in pairs if m.checklist_pass == 3]
    print(_grp("4개 전체 통과", ck4))
    print(_grp("3개 통과", ck3))

    # ── 추가 기준9: total_score ──────────────────────────────────
    _section("추가기준9: 총점 구간")
    sc_hi  = [r for r, m in pairs if m.total_score >= 11]
    sc_mid = [r for r, m in pairs if 9 <= m.total_score <= 10]
    sc_lo  = [r for r, m in pairs if m.total_score <= 8]
    print(_grp("11점 이상 (상위)", sc_hi))
    print(_grp("9~10점 (중위)", sc_mid))
    print(_grp("8점 이하 (하위)", sc_lo))

    # ── 복합 필터: 교집합 + ★ ────────────────────────────────────
    _section("복합: 교집합 AND ★ 수급")
    combo_y = [r for r, m in pairs if m.in_inter and "★" in m.supply_label]
    combo_n = [r for r, m in pairs if not (m.in_inter and "★" in m.supply_label)]
    print(_grp("교집합+★ (동시 충족)", combo_y))
    print(_grp("나머지", combo_n))

    # ── 복합 필터: MA20 이격 (기존) ─────────────────────────────
    _section("참고: MA20 이격 구간 (기준봉 종가 vs MA20)")
    ma20_bands = [
        ("10% 이하",  lambda r: r.ma20_gap_at_entry <= 10),
        ("11~20%",    lambda r: 10 < r.ma20_gap_at_entry <= 20),
        ("21~30%",    lambda r: 20 < r.ma20_gap_at_entry <= 30),
        ("30% 초과",  lambda r: r.ma20_gap_at_entry > 30),
    ]
    ma10_closed = [r for r in results if "MA10청산" in r.ma10_status or "만료" in r.ma10_status]
    for lbl, fn in ma20_bands:
        grp = [r for r in ma10_closed if fn(r)]
        if not grp:
            continue
        wins  = sum(1 for r in grp if r.ma10_exit_pct > 0)
        avg_r = sum(r.ma10_exit_pct for r in grp) / len(grp)
        avg_d = sum(r.ma10_days for r in grp) / len(grp)
        print(f"  {lbl:<22}: {len(grp):3d}개  "
              f"승률={wins/len(grp)*100:4.0f}%  평균실현={avg_r:+5.1f}%  평균일={avg_d:.1f}일")


# ── 메인 ────────────────────────────────────────────────────────────────

def main() -> None:
    sig_stocks = _load_signals_stocks()  # (code, date, name, meta|None)

    # MANUAL 중복 제거 후 합산
    existing = {(c, d) for c, d, _, _ in sig_stocks}
    manual_extra: list[tuple[str, str, str, Optional[SignalMeta]]] = []
    for code, date, name in MANUAL_BASE_STOCKS:
        if (code, date) not in existing:
            manual_extra.append((code, date, name, None))
            existing.add((code, date))
    all_stocks = sig_stocks + manual_extra

    sig_count = len(sig_stocks)
    print(f"\n분석 대상: {len(all_stocks)}개 기준봉 종목 (signals {sig_count}개 + 수동 {len(manual_extra)}개)")

    results: list[SwingResult] = []
    metas:   list[Optional[SignalMeta]] = []
    for code, signal_date, name, meta in sorted(all_stocks, key=lambda x: x[1]):
        df = _fetch_ohlcv(code)
        if df.empty:
            continue
        r = _analyze(code, name, signal_date, df)
        if r is not None:
            results.append(r)
            metas.append(meta)

    if not results:
        print("분석 결과 없음.")
        return

    n = len(results)

    # ── 개별 결과 테이블 ─────────────────────────────────────────
    print("\n" + "=" * 100)
    print(" 기준봉 스윙 백테스트  |  진입: D+1 시가  |  비교: MA20 이탈 vs MA10 청산")
    print("=" * 100)
    hdr = (f"{'코드':<8}{'종목명':<10}{'기준봉':<12}{'MA20이격':>8}"
           f"  {'-- MA10청산 --':^25}  {'-- MA20이탈 --':^25}")
    print(hdr)
    sub = (f"{'':38}"
           f"  {'보유일':>5} {'최대%':>7} {'실현%':>7} {'상태':<14}"
           f"  {'보유일':>5} {'최대%':>7} {'실현%':>7} {'상태'}")
    print(sub)
    print("-" * 100)
    for r in sorted(results, key=lambda x: x.signal_date):
        print(
            f"{r.code:<8}{r.name:<10}{r.signal_date:<12}{r.ma20_gap_at_entry:>+7.0f}%"
            f"  {r.ma10_days:>5}일 {r.ma10_max_gain_pct:>+6.1f}% {r.ma10_exit_pct:>+6.1f}%"
            f"  {r.ma10_status:<14}"
            f"  {r.ma20_streak:>5}일 {r.ma20_max_gain_pct:>+6.1f}% {r.ma20_exit_pct:>+6.1f}%"
            f"  {r.ma20_status}"
        )

    # ── MA10 청산 전략 통계 ───────────────────────────────────────
    ma10_closed  = [r for r in results if "MA10청산" in r.ma10_status or "만료" in r.ma10_status]
    ma10_holding = [r for r in results if r.ma10_status == "유지중"]
    ma20_broken  = [r for r in results if "MA20이탈" in r.ma20_status]
    ma20_holding = [r for r in results if r.ma20_status == "MA20유지중"]

    print("\n" + "=" * 70)
    print(" [전략 비교 요약]  진입: D+1 시가")
    print("=" * 70)
    print(f"  전체 케이스: {n}개")

    _summary(
        "MA10 청산 (종가 < 10일선)",
        [r.ma10_exit_pct for r in ma10_closed],
        [r.ma10_max_gain_pct for r in ma10_closed],
        [r.ma10_days for r in ma10_closed],
    )
    if ma10_holding:
        print(f"    * 현재 MA10 유지중 {len(ma10_holding)}개 (미청산, 통계 제외)")

    _summary(
        "MA20 이탈 청산 (비교 기준)",
        [r.ma20_exit_pct for r in ma20_broken],
        [r.ma20_max_gain_pct for r in ma20_broken],
        [r.ma20_streak for r in ma20_broken],
    )
    if ma20_holding:
        print(f"    * 현재 MA20 유지중 {len(ma20_holding)}개 (미청산, 통계 제외)")

    # ── MA10 청산 손실 케이스 분석 ────────────────────────────────
    ma10_loss = [r for r in ma10_closed if r.ma10_exit_pct <= 0]
    print(f"\n  [MA10 청산 손실 케이스 분석]  총 {len(ma10_loss)}개")
    if ma10_loss:
        avg_loss_day = sum(r.ma10_days for r in ma10_loss) / len(ma10_loss)
        print(f"    평균 청산일: D+{avg_loss_day:.1f}")
        d1 = sum(1 for r in ma10_loss if r.ma10_days <= 1)
        d3 = sum(1 for r in ma10_loss if 1 < r.ma10_days <= 3)
        d5 = sum(1 for r in ma10_loss if 3 < r.ma10_days <= 5)
        d10= sum(1 for r in ma10_loss if r.ma10_days > 5)
        print(f"    손실 분포: D+1이하={d1}  D+2~3={d3}  D+4~5={d5}  D+6이상={d10}")
        worst = sorted(ma10_loss, key=lambda x: x.ma10_exit_pct)[:5]
        print(f"    최대 손실 5개:")
        for r in worst:
            print(f"      {r.code} {r.name} ({r.signal_date})  {r.ma10_exit_pct:+.1f}%  D+{r.ma10_days}")

    # ── 필터 분석 (signals 메타 있는 종목만) ─────────────────────
    pairs_with_meta = [(r, m) for r, m in zip(results, metas) if m is not None]
    if pairs_with_meta:
        _filter_analysis(pairs_with_meta)
    else:
        print("\n  [필터 분석] signals 메타 없음 — 수동 입력만 존재")


if __name__ == "__main__":
    main()
