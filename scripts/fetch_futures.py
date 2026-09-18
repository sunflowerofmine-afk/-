# scripts/fetch_futures.py
"""미국 선물 실시간 조회 — 국면 판정의 '선행' 입력.

기존 fetch_us_market은 전일 종가(후행)만 준다. 돌팬티가 실제로 보는 건
장중~저녁까지 계속 움직이는 '미 선물'이다:
  "오후 8시까지의 흐름과 미 선물, 유가 추이를 종합적으로 확인한 후 최종 매수 여부를 결정" (7/1)
  "미 선물 지수가 강하게 받쳐주지 못하고 있습니다" (7/8)

봇의 국면 판정은 전부 후행(ADL·5일선·집중도 = 오늘 결과)이라 '내일'을 보는 축이 없었다.
이 모듈이 그 축을 채운다. 매수 신호가 아니라 국면 판정 재료.
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)

# 나스닥 선물이 반도체 대형주(삼전·SKH) 국내 흐름과 가장 직결
_SYMBOLS = {
    "나스닥선물": "NQ=F",
    "S&P선물":   "ES=F",
    "VIX":       "^VIX",
}

# 판정 임계 (잠정 — 표본 누적 후 조정)
_STRONG_PCT = 0.5    # 선물 이 값 이상 = 우호
_WEAK_PCT   = -0.5   # 선물 이 값 이하 = 비우호
_VIX_HIGH   = 25.0   # VIX 이 값 이상 = 공포 구간


def fetch_futures() -> dict:
    """{나스닥선물: {value, chg_pct}, ...} + risk_appetite 판정.

    risk_appetite: "우호" | "중립" | "비우호" | None(조회 실패)
    실패해도 예외를 던지지 않는다 (파이프라인 중단 금지).
    """
    import yfinance as yf
    out: dict = {}
    for name, sym in _SYMBOLS.items():
        try:
            t = yf.Ticker(sym)
            # 선물은 24시간 거래 — 1분봉으로 '현재값' 확보, 전일 종가 대비 등락
            hist = t.history(period="2d", interval="1h")
            if hist.empty or len(hist) < 2:
                continue
            last = float(hist["Close"].iloc[-1])
            prev_close = float(t.history(period="5d")["Close"].iloc[-2])
            out[name] = {
                "value": round(last, 2),
                "chg_pct": round((last - prev_close) / prev_close * 100, 2),
            }
        except Exception as e:
            logger.warning(f"{name}({sym}) 선물 조회 실패: {e}")

    nq  = (out.get("나스닥선물") or {}).get("chg_pct")
    vix = (out.get("VIX") or {}).get("value")
    if nq is None:
        out["risk_appetite"] = None
    elif nq <= _WEAK_PCT or (vix is not None and vix >= _VIX_HIGH):
        out["risk_appetite"] = "비우호"
    elif nq >= _STRONG_PCT:
        out["risk_appetite"] = "우호"
    else:
        out["risk_appetite"] = "중립"

    if out:
        logger.info(f"미선물: {out}")
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(fetch_futures())
