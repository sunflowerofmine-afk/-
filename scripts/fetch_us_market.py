# scripts/fetch_us_market.py
"""전일 미국 시장 지수 및 뉴스 수집"""
import logging
import sys
from pathlib import Path

import requests
from lxml import etree

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)

_SYMBOLS = {
    "S&P500":        "^GSPC",
    "나스닥":         "^IXIC",
    "다우":           "^DJI",
    "VIX":           "^VIX",
    "필라델피아반도체": "^SOX",
    "달러/원":        "USDKRW=X",
}

_RSS_URLS = [
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
]


def fetch_indices() -> dict[str, dict]:
    """yfinance로 전일 미국 주요 지수 조회."""
    import yfinance as yf
    result = {}
    for name, symbol in _SYMBOLS.items():
        try:
            hist = yf.Ticker(symbol).history(period="5d")
            if len(hist) >= 2:
                prev = float(hist["Close"].iloc[-2])
                last = float(hist["Close"].iloc[-1])
                result[name] = {"value": last, "chg_pct": (last - prev) / prev * 100}
            elif len(hist) == 1:
                result[name] = {"value": float(hist["Close"].iloc[0]), "chg_pct": None}
        except Exception as e:
            logger.warning(f"{name}({symbol}) 조회 실패: {e}")
    return result


def fetch_headlines(max_items: int = 8) -> list[str]:
    """RSS에서 미국 시장 뉴스 헤드라인 수집."""
    headlines = []
    parser = etree.XMLParser(recover=True)
    for url in _RSS_URLS:
        if len(headlines) >= max_items:
            break
        try:
            resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            root = etree.fromstring(resp.content, parser)
            for t in root.xpath("//item/title/text()"):
                t = t.strip()
                if t and len(headlines) < max_items:
                    headlines.append(t)
        except Exception as e:
            logger.warning(f"RSS 수집 실패 ({url}): {e}")
    return headlines


# ── 섹터 → 관련 미국주식 매핑 ─────────────────────────────
# (키워드 리스트, [(ticker, 약칭)])
_SECTOR_US_MAP: list[tuple[list[str], list[tuple[str, str]]]] = [
    (["반도체", "HBM", "DDR", "낸드", "DRAM", "메모리", "시스템반도체", "MLCC", "적층"],
     [("NVDA", "NVDA"), ("AMD", "AMD"), ("MU", "MU")]),
    (["AI", "인공지능", "LLM", "ChatGPT", "GPU"],
     [("NVDA", "NVDA"), ("MSFT", "MSFT"), ("GOOGL", "GOOGL")]),
    (["양자", "퀀텀"],
     [("IONQ", "IonQ"), ("RGTI", "Rigetti"), ("IBM", "IBM")]),
    (["우주", "위성", "발사체", "누리호"],
     [("RKLB", "RocketLab"), ("LMT", "Lockheed"), ("NOC", "Northrop")]),
    (["방산", "무기", "레이더", "미사일", "K방산"],
     [("LMT", "Lockheed"), ("RTX", "Raytheon"), ("NOC", "Northrop")]),
    (["2차전지", "배터리", "리튬", "전기차", "ESS"],
     [("TSLA", "Tesla"), ("ALB", "Albemarle"), ("QS", "QuantumScape")]),
    (["바이오", "제약", "mRNA", "진단", "코로나", "백신", "항암", "신약", "의료기기"],
     [("MRNA", "Moderna"), ("BNTX", "BioNTech"), ("XBI", "XBI ETF")]),
    (["원전", "핵융합", "우라늄", "SMR"],
     [("CEG", "Constellation"), ("CCJ", "Cameco"), ("NEE", "NextEra")]),
    (["전선", "전력인프라", "변압기", "송전", "배전", "케이블"],
     [("VST", "Vistra"), ("ETR", "Entergy"), ("AES", "AES")]),
    (["태양광", "풍력", "신재생"],
     [("ENPH", "Enphase"), ("FSLR", "FirstSolar"), ("SEDG", "SolarEdge")]),
    (["로봇", "자동화", "물류로봇", "피지컬AI"],
     [("ISRG", "Intuitive"), ("PATH", "UiPath"), ("TER", "Teradyne")]),
    (["게임", "콘텐츠", "메타버스"],
     [("RBLX", "Roblox"), ("EA", "EA"), ("TTWO", "Take-Two")]),
    (["엔터", "음악", "스트리밍", "K-pop", "드라마"],
     [("SPOT", "Spotify"), ("NFLX", "Netflix")]),
    (["자율주행", "카메라모듈", "라이다"],
     [("TSLA", "Tesla"), ("MBLY", "Mobileye"), ("GOOGL", "Waymo")]),
    (["전력반도체", "SiC", "GaN", "IGBT"],
     [("ON", "ON Semi"), ("WOLF", "Wolfspeed"), ("STM", "STMicro")]),
    (["철강", "소재", "알루미늄"],
     [("X", "US Steel"), ("NUE", "Nucor"), ("CLF", "Cliffs")]),
    (["화학", "석유화학"],
     [("DOW", "Dow"), ("LYB", "LyondellBasell")]),
    (["조선", "선박", "해양"],
     [("HII", "Huntington"), ("GD", "General Dynamics")]),
    (["바이오시밀러", "항체", "ADC", "세포치료"],
     [("ABBV", "AbbVie"), ("AMGN", "Amgen"), ("REGN", "Regeneron")]),
    (["디스플레이", "OLED", "LCD"],
     [("OLED", "Uni. Display"), ("AAPL", "Apple")]),
]


def _map_sector(sector: str) -> list[tuple[str, str]]:
    for keywords, tickers in _SECTOR_US_MAP:
        if any(kw in sector for kw in keywords):
            return tickers
    return []


def fetch_candidate_related(candidates: list[dict]) -> list[dict]:
    """후보 종목 섹터별 관련 미국주식 조회.
    반환: [{"sector": str, "kr_names": [str], "stocks": [{"ticker","name","chg_pct"}]}]
    """
    import yfinance as yf

    # 교집합 종목 우선, 섹터별 그룹화
    seen_sectors: dict[str, dict] = {}
    for c in sorted(candidates, key=lambda x: not x.get("in_inter", False)):
        sector = c.get("sector", "").strip()
        if not sector:
            continue
        us_tickers = _map_sector(sector)
        if not us_tickers:
            continue
        if sector not in seen_sectors:
            seen_sectors[sector] = {"kr_names": [], "tickers": us_tickers}
        seen_sectors[sector]["kr_names"].append(c.get("name", ""))

    if not seen_sectors:
        return []

    # 필요한 전체 ticker 일괄 조회
    all_tickers = list({t for grp in seen_sectors.values() for t, _ in grp["tickers"]})
    ticker_chg: dict[str, float | None] = {}
    for ticker in all_tickers:
        try:
            hist = yf.Ticker(ticker).history(period="5d")
            if len(hist) >= 2:
                prev = float(hist["Close"].iloc[-2])
                last = float(hist["Close"].iloc[-1])
                ticker_chg[ticker] = (last - prev) / prev * 100
            elif len(hist) == 1:
                ticker_chg[ticker] = None
        except Exception as e:
            logger.warning(f"{ticker} 조회 실패: {e}")

    return [
        {
            "sector": sector,
            "kr_names": grp["kr_names"],
            "stocks": [
                {"ticker": t, "name": n, "chg_pct": ticker_chg.get(t)}
                for t, n in grp["tickers"]
            ],
        }
        for sector, grp in seen_sectors.items()
    ]
