# scripts/fetch_index_data.py
"""코스피/코스닥 지수 일봉 수집 + 5일선·추세 기반 국면 판정.

백테스트(backtest_regime_largecap)에서 검증된 국면 정의:
  강세 = 종가 > 5일선 AND 5일선 상승추세
  약세 = 종가 < 5일선 AND 5일선 하락추세
  혼조 = 그 외 (변곡 구간 — 5-6월 다수가 바닥 반등)

종베 승률은 코스닥 국면이 결정(혼조 76% / 약세 39%). 코스피는 변별력 약함.
단 코스피 강세 & 코스닥 약세 디커플링 시 = 대형주 모드 신호.
"""
import re
import logging
from statistics import mean

import requests
from bs4 import BeautifulSoup
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import HEADERS, REQUEST_TIMEOUT

logger = logging.getLogger(__name__)
INDEX_URL = "https://finance.naver.com/sise/sise_index_day.naver"


def fetch_index_daily(code: str, pages: int = 3) -> dict:
    """code: KOSPI/KOSDAQ. {YYYY-MM-DD: 종가} (최근 약 pages*10거래일)."""
    out = {}
    for p in range(1, pages + 1):
        try:
            r = requests.get(f"{INDEX_URL}?code={code}&page={p}",
                             headers=HEADERS, timeout=REQUEST_TIMEOUT)
            r.encoding = "euc-kr"
            s = BeautifulSoup(r.text, "lxml")
            for tr in s.select("table.type_1 tr"):
                td = tr.select("td")
                if len(td) < 2:
                    continue
                d = td[0].text.strip()
                c = td[1].text.strip().replace(",", "")
                if re.match(r"\d{4}\.\d{2}\.\d{2}", d) and c:
                    out[d.replace(".", "-")] = float(c)
        except Exception as e:
            logger.warning(f"[{code}] 지수 일봉 수집 실패 p{p}: {e}")
    return out


def classify_regime(idx: dict) -> tuple[str, dict]:
    """최신일 기준 국면 판정. (regime, detail) 반환.
    detail: {close, ma5, above_ma5, ma5_rising, ma60, above_ma60, ma60_gap_pct}
    데이터 부족 시 ("?", {}).
    """
    dates = sorted(idx.keys())
    if len(dates) < 8:
        return "?", {}
    closes = [idx[d] for d in dates]
    i = len(dates) - 1
    ma5      = mean(closes[i-4:i+1])
    ma5_prev = mean(closes[i-7:i-2])
    above  = closes[i] > ma5
    rising = ma5 > ma5_prev
    if above and rising:
        regime = "강세"
    elif (not above) and (not rising):
        regime = "약세"
    else:
        regime = "혼조"
    detail = {
        "date": dates[i], "close": round(closes[i], 2), "ma5": round(ma5, 2),
        "above_ma5": above, "ma5_rising": rising,
    }
    # 60일선 — 돌팬티가 실제로 쓰는 기준선("코스피 두 번째 60일선 터치, 언제든 무너질 수 있는 구간", 7/7)
    if len(closes) >= 60:
        ma60 = mean(closes[i-59:i+1])
        detail.update({
            "ma60": round(ma60, 2),
            "above_ma60": closes[i] > ma60,
            "ma60_gap_pct": round((closes[i] - ma60) / ma60 * 100, 2),
        })
    return regime, detail


# 국면별 종베 행동 가이드 (backtest_regime_largecap 근거)
REGIME_GUIDE = {
    "강세": "과열 주의 — 신선한 종목만, 풀매수 자제 (강세장 승률 50%, 종가=고점 위험)",
    "혼조": "적극 매수 — 교집합(상승+거래대금 둘다 강함)/10-12점/급등주 유효 (바닥 반등 변곡점, 비슷한 장 승률 76%)",
    "약세": "보수 — 10-12점만, 13점 과열주 금지 (승률 39%, 시초가 갭하락 주의)",
    "?":    "국면 판정 불가 (데이터 부족)",
}


def get_market_regime() -> dict:
    """코스피·코스닥 국면 + 디커플링 + 가이드 일괄 반환. 알림/파이프라인용.

    반환:
      kosdaq_regime, kospi_regime, kosdaq_detail, kospi_detail,
      decoupled_largecap(bool), guide(str)
    """
    # 60일선 계산에 60거래일 이상 필요 (페이지당 약 6행 → 12페이지 ≈ 72일)
    kosdaq = fetch_index_daily("KOSDAQ", pages=12)
    kospi  = fetch_index_daily("KOSPI",  pages=12)
    kd_reg, kd_det = classify_regime(kosdaq)
    kp_reg, kp_det = classify_regime(kospi)

    # 디커플링: 코스피 강세 & 코스닥 약세 → 대형주 모드
    decoupled = (kp_reg == "강세" and kd_reg in ("약세", "혼조"))

    return {
        "kosdaq_regime": kd_reg,
        "kospi_regime":  kp_reg,
        "kosdaq_detail": kd_det,
        "kospi_detail":  kp_det,
        "decoupled_largecap": decoupled,
        "guide": REGIME_GUIDE.get(kd_reg, ""),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(get_market_regime(), ensure_ascii=False, indent=2))
