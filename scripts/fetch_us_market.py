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
