# scripts/largecap_observer.py
"""대형주 추세추종 관찰 레이어 (backtest_regime_largecap 검증 기반).

코스피 시총 상위 N 중 '5일선 위 + 당일 양봉'인 종목을 관찰 풀로 제공.
검증: 코스피 강세 국면에서 이 조건 종베 승률 69.4%(D+1 시초가 +1.65%).
  단 코스피 약세 국면엔 28% 자살골 → pipeline에서 코스피 강세일 때만 호출할 것.

매수 신호가 아니라 '관찰 정보'다(시스템 철학: 정보 제공, 판단은 사용자).
급등 필터를 거치지 않으므로 기존 종베 후보풀의 사각지대(안 오른 추세 대형주)를 메운다.
"""
import re
import time
import logging
from statistics import mean

import requests
from bs4 import BeautifulSoup
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import HEADERS, REQUEST_TIMEOUT, REQUEST_DELAY, LARGECAP_TOP_N
from scripts.fetch_stock_data import fetch_daily_history

logger = logging.getLogger(__name__)
_MKT_SUM_URL = "https://finance.naver.com/sise/sise_market_sum.naver"


def fetch_kospi_top(n: int = LARGECAP_TOP_N) -> list[tuple[str, str]]:
    """네이버 시총순 코스피 상위 n = [(code, name)]. 1페이지=50종목."""
    out = []
    pages = (n // 50) + 1
    for p in range(1, pages + 1):
        try:
            r = requests.get(f"{_MKT_SUM_URL}?sosok=0&page={p}", headers=HEADERS, timeout=REQUEST_TIMEOUT)
            r.encoding = "euc-kr"
            s = BeautifulSoup(r.text, "lxml")
            for tr in s.select("table.type_2 tr"):
                c = tr.select("td")
                if len(c) < 10:
                    continue
                a = c[1].select_one("a")
                if not a:
                    continue
                m = re.search(r"code=(\w+)", a.get("href", ""))
                if m:
                    out.append((m.group(1), a.text.strip()))
        except Exception as e:
            logger.warning(f"코스피 시총상위 수집 실패 p{p}: {e}")
    return out[:n]


def observe(top_n: int = LARGECAP_TOP_N) -> list[dict]:
    """코스피 상위 종목 중 '5일선 위 + 당일 양봉' 종목 리스트 반환.

    각 항목: {code, name, close, ma5, ma5_gap_pct, change_pct, trading_value}
    5일선 이격이 작을수록(추세 초입) 우선 — 과확장 회피.
    """
    top = fetch_kospi_top(top_n)
    logger.info(f"대형주 관찰: 코스피 시총상위 {len(top)}종목 점검")
    result = []
    for code, name in top:
        try:
            df = fetch_daily_history(code, pages=2)  # 약 20거래일 (5일선 충분)
            if df.empty or len(df) < 6:
                continue
            df = df.sort_values("date").reset_index(drop=True)  # 과거→최신
            closes = df["close"].tolist()
            close = float(closes[-1])
            prev  = float(closes[-2])
            ma5   = mean(closes[-5:])
            if close <= ma5 or close <= prev:   # 5일선 위 + 당일 양봉 동시
                continue
            row = df.iloc[-1]
            result.append({
                "code": code, "name": name,
                "close": close, "ma5": round(ma5, 1),
                "ma5_gap_pct": round((close - ma5) / ma5 * 100, 2),
                "change_pct": round((close - prev) / prev * 100, 2),
                "trading_value": float(row.get("trading_value", 0) or 0),
            })
        except Exception as e:
            logger.debug(f"[{code}] 대형주 관찰 실패: {e}")
        time.sleep(REQUEST_DELAY)
    # 5일선 이격 작은 순(추세 초입 우선)
    result.sort(key=lambda x: x["ma5_gap_pct"])
    logger.info(f"대형주 관찰: 5일선 위+당일양봉 {len(result)}종목")
    return result


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(observe(), ensure_ascii=False, indent=2))
