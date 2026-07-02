# scripts/fetch_macro.py
"""네이버 마켓인덱스에서 환율(USD/KRW)·WTI 수집 — 알림 [거시] 줄용.

돌팬티 루틴(미선물·유가·환율 확인) 중 환율·유가 커버. 미선물 장중은 별도 과제.
실패해도 예외 없이 빈 dict 반환 (파이프라인 중단 금지).
"""
import logging
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import HEADERS, REQUEST_TIMEOUT

logger = logging.getLogger(__name__)
_URL = "https://finance.naver.com/marketindex/"


def _parse_head(el) -> tuple[float, float] | None:
    """head_info 블록에서 (값, 부호 있는 변화량) 추출."""
    info = el.select_one(".head_info")
    if not info:
        return None
    try:
        value  = float(info.select_one(".value").text.replace(",", ""))
        change = float(info.select_one(".change").text.replace(",", ""))
    except (AttributeError, ValueError):
        return None
    blind = " ".join(b.text for b in info.select(".blind"))
    if "하락" in blind:
        change = -change
    return value, change


def fetch_macro() -> dict:
    """{"usdkrw", "usdkrw_chg", "wti", "wti_chg"} 반환. 일부/전체 실패 시 해당 키 없음."""
    out: dict = {}
    try:
        r = requests.get(_URL, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.encoding = "euc-kr"
        s = BeautifulSoup(r.text, "lxml")
        usd = s.select_one("#exchangeList a.head.usd")
        if usd:
            parsed = _parse_head(usd)
            if parsed:
                out["usdkrw"], out["usdkrw_chg"] = parsed
        wti = s.select_one("#oilGoldList a.head.wti")
        if wti:
            parsed = _parse_head(wti)
            if parsed:
                out["wti"], out["wti_chg"] = parsed
        if out:
            logger.info(f"거시 지표: {out}")
    except Exception as e:
        logger.warning(f"거시 지표 수집 실패 (무시): {e}")
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(fetch_macro())
