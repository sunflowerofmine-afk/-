# scripts/backtest_twotop_oversold.py
"""투탑(삼성전자·SK하이닉스) 과매도 반등 백테스트.

규칙: 당일 등락률 ≤ -N% (급락일) → 당일 종가 매수 → D+1 시가 / D+1 종가 매도.
고수(돌팬티·준돌) 2026-07-02~03 실전 매매의 규칙화 검증.
주의: KRX 정규장 일봉 기준 — 고수가 실제 쓰는 NXT 야간·동시호가 정보는 미반영.

실행: python -m scripts.backtest_twotop_oversold
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.fetch_stock_data import fetch_daily_history

STOCKS = {"005930": "삼성전자", "000660": "SK하이닉스"}
PAGES = 90  # ≒ 900거래일 (약 3.6년)
THRESHOLDS = [-5.0, -8.0, -10.0]
CUM2_THRESHOLD = -12.0  # 2일 누적 급락 변형


def _load(code: str):
    df = fetch_daily_history(code, pages=PAGES)
    if df.empty:
        return None
    df = df.sort_values("date").reset_index(drop=True)  # 과거→최신
    df["chg_pct"] = df["close"].pct_change() * 100
    return df


def _events(df, cond_idx):
    """cond_idx: 조건 충족일 인덱스 리스트 → (진입종가, D+1시가, D+1종가, D+1저가) 성과."""
    out = []
    for i in cond_idx:
        if i + 1 >= len(df):
            continue
        entry = df.loc[i, "close"]
        nxt = df.loc[i + 1]
        out.append({
            "date": df.loc[i, "date"],
            "chg": df.loc[i, "chg_pct"],
            "d1_open_pct":  (nxt["open"]  - entry) / entry * 100,
            "d1_close_pct": (nxt["close"] - entry) / entry * 100,
            "d1_low_pct":   (nxt["low"]   - entry) / entry * 100,
        })
    return out


def _report(label, evs):
    if not evs:
        print(f"  {label}: 표본 0건")
        return
    n = len(evs)
    def stat(key):
        vals = [e[key] for e in evs]
        win = sum(1 for v in vals if v > 0)
        return f"승률 {win}/{n} ({win/n*100:.0f}%) 평균 {sum(vals)/n:+.2f}%"
    worst_low = min(e["d1_low_pct"] for e in evs)
    print(f"  {label}: {n}건")
    print(f"    D+1 시가 매도: {stat('d1_open_pct')}")
    print(f"    D+1 종가 매도: {stat('d1_close_pct')}")
    print(f"    D+1 저가 최악: {worst_low:+.2f}%")
    for e in evs:
        print(f"      {e['date']} (당일 {e['chg']:+.1f}%) → D+1시 {e['d1_open_pct']:+.2f}% / D+1종 {e['d1_close_pct']:+.2f}%")


def main():
    all_evs = {t: [] for t in THRESHOLDS}
    all_cum = []
    for code, name in STOCKS.items():
        df = _load(code)
        if df is None:
            print(f"{name}({code}) 데이터 수집 실패")
            continue
        print(f"\n=== {name} ({code}) — {df['date'].iloc[0]} ~ {df['date'].iloc[-1]}, {len(df)}일 ===")
        for t in THRESHOLDS:
            idx = df.index[df["chg_pct"] <= t].tolist()
            evs = _events(df, idx)
            _report(f"당일 {t:.0f}% 이하", evs)
            all_evs[t].extend(evs)
        cum2 = df["chg_pct"] + df["chg_pct"].shift(1)
        idx = df.index[cum2 <= CUM2_THRESHOLD].tolist()
        evs = _events(df, idx)
        _report(f"2일 누적 {CUM2_THRESHOLD:.0f}% 이하", evs)
        all_cum.extend(evs)

    print("\n=== 두 종목 합산 ===")
    for t in THRESHOLDS:
        _report(f"당일 {t:.0f}% 이하", all_evs[t])
    _report(f"2일 누적 {CUM2_THRESHOLD:.0f}% 이하", all_cum)


if __name__ == "__main__":
    main()
