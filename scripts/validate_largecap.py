# scripts/validate_largecap.py
"""코스피 시총 탑50 종가베팅 시뮬 vs 봇 실제 신호 비교 (일회성 분석).

가설(사용자): 5-6월 강세는 코스피 대형주 중심. 안 올라도 대형주에 타는 게 맞았는가?
방식: 코스피 시총 탑50을 매일 종가 진입 → D+1 시가/종가 매도. 추세 조건별 분리.
비교: 봇 실제 신호 132개 (D+1 시초가 +1.19%, 승률 57.5%).
기간: 2026-05-04 ~ 2026-06-12 (봇 신호 기간과 동일).
"""
import sys
import time
from pathlib import Path
from statistics import mean

import requests
from bs4 import BeautifulSoup
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import HEADERS, REQUEST_TIMEOUT, REQUEST_DELAY
from scripts.fetch_stock_data import fetch_daily_history

START, END = "2026.03.24", "2026.06.12"  # 3월말 약세~6월까지 확장 (국면 의존성 검증)


def fetch_kospi_top50():
    """네이버 시총순 1페이지 = 코스피 시총 상위 50. [(code, name)] 반환."""
    url = "https://finance.naver.com/sise/sise_market_sum.naver?sosok=0&page=1"
    r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    r.encoding = "euc-kr"
    s = BeautifulSoup(r.text, "lxml")
    out = []
    for tr in s.select("table.type_2 tr"):
        c = tr.select("td")
        if len(c) < 10:
            continue
        a = c[1].select_one("a")
        if not a:
            continue
        href = a.get("href", "")
        import re
        m = re.search(r"code=(\w+)", href)
        if m:
            out.append((m.group(1), a.text.strip()))
    return out[:50]


def simulate(df: pd.DataFrame):
    """일봉 df(최신 index 0)로 기간 내 매일 종가진입→D+1 시가/종가 수익 계산.
    추세 조건: 진입일 종가가 5일선 위인지, 당일 양봉인지.
    반환: 거래 리스트 [{date, d1_open_pct, d1_close_pct, above_ma5, up_candle}]
    """
    df = df.sort_values("date").reset_index(drop=True)  # 과거→최신
    df["ma5"] = df["close"].rolling(5).mean()
    trades = []
    for i in range(len(df) - 1):
        d = df.loc[i, "date"]
        if not (START <= d <= END):
            continue
        close = df.loc[i, "close"]
        nxt_open = df.loc[i + 1, "open"]
        nxt_close = df.loc[i + 1, "close"]
        if not close or pd.isna(nxt_open) or nxt_open <= 0:
            continue
        ma5 = df.loc[i, "ma5"]
        prev_close = df.loc[i - 1, "close"] if i > 0 else close
        trades.append({
            "date": d,
            "d1_open_pct": (nxt_open - close) / close * 100,
            "d1_close_pct": (nxt_close - close) / close * 100,
            "above_ma5": bool(ma5 and close > ma5),
            "up_candle": bool(close > prev_close),
        })
    return trades


def stat(trades, key="d1_open_pct"):
    if not trades:
        return None
    vals = [t[key] for t in trades]
    wins = sum(1 for v in vals if v > 0)
    return {"n": len(vals), "avg": mean(vals), "winrate": wins / len(vals) * 100}


def line(label, st):
    if st is None:
        return f"  {label:30s} | 표본 0"
    return f"  {label:30s} | 승률 {st['winrate']:5.1f}% | D+1시가 평균 {st['avg']:+5.2f}% (n={st['n']})"


def main():
    top50 = fetch_kospi_top50()
    print(f"코스피 탑50 수집: {len(top50)}종목")
    all_trades = []
    for i, (code, name) in enumerate(top50):
        try:
            df = fetch_daily_history(code, pages=7)  # 약 70일
            if df.empty:
                continue
            t = simulate(df)
            for x in t:
                x["code"] = code
                x["name"] = name
            all_trades.extend(t)
        except Exception as e:
            print(f"  [{code}] 실패: {e}")
        time.sleep(REQUEST_DELAY)
        if (i + 1) % 10 == 0:
            print(f"  {i+1}/50 ...")

    out = []
    out.append("=" * 92)
    out.append("코스피 시총 탑50 종가베팅 시뮬 vs 봇 실제 신호 — 5-6월 (2026-05-04~06-12)")
    out.append("대형주를 '매일 종가 진입 → D+1 시가 매도'로 샀을 때. 봇 신호 기준: 승률 57.5% / D+1시가 +1.19%")
    out.append("=" * 92)
    out.append(f"\n총 대형주 거래 시뮬: {len(all_trades)}건 (탑50 × 기간 거래일)")

    out.append("\n[대형주 종베 — 진입 조건별]")
    out.append(line("탑50 전체 (무조건 종베)", stat(all_trades)))
    out.append(line("5일선 위만", stat([t for t in all_trades if t["above_ma5"]])))
    out.append(line("5일선 위 + 당일 양봉만", stat([t for t in all_trades if t["above_ma5"] and t["up_candle"]])))
    out.append(line("5일선 아래 (역추세)", stat([t for t in all_trades if not t["above_ma5"]])))

    out.append("\n[D+1 종가까지 보유 시 (참고)]")
    out.append(line("탑50 전체 → D+1 종가", stat(all_trades, "d1_close_pct")))
    out.append(line("5일선 위 → D+1 종가", stat([t for t in all_trades if t["above_ma5"]], "d1_close_pct")))

    # 월별 분리 — 국면 의존성 검증 (코스피: 3월말 약세, 4-5월 강세, 6월 약세)
    out.append("\n[월별 — 5일선 위 대형주 종베 승률 추이 (국면 의존성)]")
    for mlabel, lo, hi in [("3월말", "2026.03.24", "2026.03.31"),
                           ("4월",   "2026.04.01", "2026.04.30"),
                           ("5월",   "2026.05.01", "2026.05.31"),
                           ("6월",   "2026.06.01", "2026.06.12")]:
        grp = [t for t in all_trades if lo <= t["date"] <= hi and t["above_ma5"]]
        out.append(line(f"{mlabel} (5일선 위 대형주)", stat(grp)))

    text = "\n".join(out)
    open("data/validate_largecap_result.txt", "w", encoding="utf-8").write(text)
    print("저장: data/validate_largecap_result.txt")


if __name__ == "__main__":
    main()
