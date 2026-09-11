# scripts/fetch_macro.py
"""네이버 마켓인덱스에서 환율(USD/KRW)·WTI 수집 — 알림 [거시] 줄용.

돌팬티 루틴(미선물·유가·환율 확인) 중 환율·유가 커버. 미선물 장중은 별도 과제.
실패해도 예외 없이 빈 dict 반환 (파이프라인 중단 금지).
"""
import logging
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)
# 2026-09-11까지 finance.naver.com/marketindex/ HTML(head_info 블록)을 긁었다.
# 네이버가 그 페이지를 stock.naver.com으로 리다이렉트하면서 JSON API로 전환.
from scripts.naver_api import fetch_market_index, to_float

_ITEMS = {
    # 키 접두어: (category, reutersCode)
    "usdkrw": ("exchange", "FX_USDKRW"),   # 하나은행 고시 USD/KRW
    "wti":    ("energy",   "CLcv1"),       # NYMEX WTI 근월물
}


def _parse_detail(d: dict) -> tuple[float, float] | None:
    """productDetail result에서 (값, 부호 있는 변화량). fluctuations는 부호가 붙어 온다."""
    value  = to_float(d.get("closePrice"))
    change = to_float(d.get("fluctuations"))
    if value is None or change is None:
        return None
    ftype = str((d.get("fluctuationsType") or {}).get("name") or "").upper()
    if ftype == "FALLING" and change > 0:
        change = -change
    return value, change


def fetch_macro() -> dict:
    """{"usdkrw", "usdkrw_chg", "wti", "wti_chg"} 반환. 일부/전체 실패 시 해당 키 없음."""
    out: dict = {}
    for key, (category, code) in _ITEMS.items():
        try:
            parsed = _parse_detail(fetch_market_index(category, code))
            if parsed:
                out[key], out[f"{key}_chg"] = parsed
        except Exception as e:
            logger.warning(f"거시 지표 수집 실패 ({key}, 무시): {e}")
    if out:
        logger.info(f"거시 지표: {out}")
    return out
