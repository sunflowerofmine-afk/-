# scripts/largecap_observer.py
"""대형주 주도주 관찰 레이어 (2026-06-30 백테스트로 필터 재정렬).

코스피 시총 상위 N 중 '신고가 근접 + 거래대금 + 당일 양봉' 종목을 관찰 풀로 제공.
검증(시총상위50, 4~6월 2950표본): 신고가근접+거래대금+양봉 → D+1 시가 67%/종가 58%
  (봇 중소형 53%/39% 대비 우위). 외인·기관 동시매수는 단독효과 미미(63.5 vs 63.7) → 보조 태그.
  '두 트레이더 대화(손절남)' 주도주 정의와 정합. [[reference_two_traders]]

매수 신호가 아니라 '관찰 정보'다(시스템 철학: 정보 제공, 판단은 사용자).
급등 필터를 거치지 않으므로 기존 종베 후보풀의 사각지대(안 오른 추세 대형주)를 메운다.
주의: 약세장 분리검증 미완 → 신고가근접 필터가 약세장 잡주를 자동 배제하나 과최적화 경계.
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
from config.settings import (
    HEADERS, REQUEST_TIMEOUT, REQUEST_DELAY, LARGECAP_TOP_N,
    LARGECAP_NEAR_HIGH_PCT, LARGECAP_MIN_TV_EOK,
)
from scripts.fetch_stock_data import fetch_chart_data
from scripts.fetch_supply_data import fetch_supply

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
    """코스피 상위 종목 중 '신고가근접 + 거래대금 + 당일양봉' 종목 리스트 반환.

    게이트(B안, 2026-06-30): 당일양봉 AND 거래대금 LARGECAP_MIN_TV_EOK억+
        AND (신고가근접 -LARGECAP_NEAR_HIGH_PCT% 이내 OR 외인·기관 동시순매수).
        백테스트: 신고가근접 67%/58%, 거래대금+동시매수(신고가無) 66%/54% — 둘 다 검증.
        외인기관 동시매수 단독은 미미하나 거래대금 결합 시 유효 → OR 경로로 채택.
    각 항목: {code, name, close, ma5, ma5_gap_pct, change_pct, trading_value,
              near_high_pct, near_high, dual_buy}
    신고가에 가까운 순(near_high_pct 작은 순) 정렬.
    """
    from config.settings import EXCLUDE_KEYWORDS
    top = fetch_kospi_top(top_n)
    # ETF/ETN/리츠/스팩 제외 (시총상위에 ETF 다수 혼입)
    top = [(c, n) for c, n in top if not any(kw in n for kw in EXCLUDE_KEYWORDS)]
    logger.info(f"대형주 관찰: 코스피 시총상위 {len(top)}종목 점검(ETF 제외 후)")
    min_tv_won = LARGECAP_MIN_TV_EOK * 100_000_000
    # 1차 게이트: 당일양봉 + 거래대금 (일봉만, 저비용). 신고가 여부는 플래그로 기록.
    prelim = []
    for code, name in top:
        try:
            df = fetch_chart_data(code)  # 약 250+거래일 (252일 신고가 계산)
            if df.empty or len(df) < 6:
                time.sleep(REQUEST_DELAY)
                continue
            df = df.reset_index(drop=True)  # 최신순 (iloc0=최신)
            close = float(df.iloc[0].get("close", 0) or 0)
            prev  = float(df.iloc[1].get("close", 0) or 0)
            tv    = float(df.iloc[0].get("trading_value", 0) or 0)
            hi252 = float(df["high"].iloc[:252].max()) if "high" in df else 0.0
            if close <= 0 or prev <= 0:
                time.sleep(REQUEST_DELAY); continue
            if close <= prev or tv < min_tv_won:   # 양봉 + 거래대금 (필수)
                time.sleep(REQUEST_DELAY); continue
            near_high_pct = (close - hi252) / hi252 * 100 if hi252 > 0 else -999
            ma5 = mean([float(df.iloc[i].get("close", 0) or 0) for i in range(5)])
            prelim.append({
                "code": code, "name": name,
                "close": close, "ma5": round(ma5, 1),
                "ma5_gap_pct": round((close - ma5) / ma5 * 100, 2),
                "change_pct": round((close - prev) / prev * 100, 2),
                "trading_value": tv,
                "near_high_pct": round(near_high_pct, 2),
                "near_high": bool(hi252 > 0 and near_high_pct >= -LARGECAP_NEAR_HIGH_PCT),
                "dual_buy": None,
            })
        except Exception as e:
            logger.debug(f"[{code}] 대형주 관찰 실패: {e}")
        time.sleep(REQUEST_DELAY)

    # 1차 통과(양봉+거래대금) 종목만 외인·기관 동시순매수 조회
    for r in prelim:
        try:
            sup = fetch_supply(r["code"])
            if getattr(sup, "status", "") == "ok":
                r["dual_buy"] = bool((sup.foreign_net or 0) > 0 and (sup.institution_net or 0) > 0)
            time.sleep(REQUEST_DELAY)
        except Exception:
            pass

    # 최종 게이트(B안): 신고가근접 OR 외인·기관 동시매수 (둘 다 백테스트 66~67%)
    result = [r for r in prelim if r["near_high"] or r["dual_buy"]]
    # 신고가에 가까운 순 (주도력 우선)
    result.sort(key=lambda x: x["near_high_pct"], reverse=True)
    logger.info(f"대형주 관찰(B안): 양봉+거래대금 {len(prelim)} → 신고가OR동시매수 {len(result)}종목"
                f" (신고가 {sum(1 for r in result if r['near_high'])}/동시매수 {sum(1 for r in result if r['dual_buy'])})")
    return result


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(observe(), ensure_ascii=False, indent=2))
