# scripts/fetch_program_data.py
"""KRX 프로그램 매매 종목별 거래실적 수집 (2차/수동 실행 시 호출)"""

import logging
import re
import time

import requests

logger = logging.getLogger(__name__)

_KRX_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
_HEADERS  = {
    "User-Agent":   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer":      "https://data.krx.co.kr/",
    "Accept":       "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}
_EOK = 100_000_000
_MIL = 1_000_000   # KRX money=3: 백만원 단위


def _extract_code(raw: str) -> str:
    """ISU_SRT_CD '005930' 또는 ISU_CD 'KR7005930003' → '005930'"""
    raw = raw.strip()
    if len(raw) == 12 and raw.startswith("KR"):
        return raw[3:9]
    return raw[:6]


def _parse_val(val) -> float:
    """콤마 포함 문자열 or 숫자 → 백만원 단위 float → 원 단위 변환"""
    if val is None:
        return 0.0
    s = str(val).strip().replace(",", "").replace("+", "")
    if not s or s == "-":
        return 0.0
    try:
        return float(s) * _MIL
    except ValueError:
        return 0.0


def _get_net(row: dict) -> float:
    """프로그램 순매수 금액(원). 직접 필드 우선, 없으면 매수-매도."""
    for k in ("PROG_NETBUY_TRDVAL", "NETBUY_TRDVAL", "NET_TRDVAL"):
        if k in row:
            return _parse_val(row[k])
    buy  = next((_parse_val(row[k]) for k in ("PROG_BUY_TRDVAL",  "BUY_TRDVAL")  if k in row), 0.0)
    sell = next((_parse_val(row[k]) for k in ("PROG_SELL_TRDVAL", "SELL_TRDVAL") if k in row), 0.0)
    return buy - sell


def fetch_program_data(date_str: str) -> dict[str, float]:
    """
    KRX 프로그램 매매 종목별 거래실적.
    date_str: 'YYYYMMDD' 형식.
    반환: {종목코드(6자리): 프로그램_순매수_억원} — 음수=순매도.
    실패 시 빈 dict 반환 (파이프라인 중단 금지).
    """
    result: dict[str, float] = {}

    for mkt_id in ("STK", "KSQ"):     # KOSPI, KOSDAQ
        try:
            resp = requests.post(
                _KRX_URL,
                data={
                    "bld":         "dbms/MDC/STAT/standard/MDCSTAT30001",
                    "locale":      "ko_KR",
                    "trdDd":       date_str,
                    "mktId":       mkt_id,
                    "share":       "2",
                    "money":       "3",
                    "csvxls_isNo": "false",
                },
                headers=_HEADERS,
                timeout=10,
            )
            data = resp.json()
        except Exception as e:
            logger.warning(f"프로그램 수급 요청 실패 [{mkt_id}]: {e}")
            continue

        rows = data.get("output", [])
        if not rows:
            logger.debug(f"프로그램 수급 빈 응답 [{mkt_id}] (비거래일 또는 장중)")
            continue

        logger.debug(f"프로그램 수급 응답 필드 [{mkt_id}]: {list(rows[0].keys())}")

        ok = 0
        for row in rows:
            raw = str(row.get("ISU_SRT_CD") or row.get("ISU_CD", ""))
            code = _extract_code(raw)
            if not re.match(r"^\d{6}$", code):
                continue
            net_won = _get_net(row)
            result[code] = round(net_won / _EOK, 1)
            ok += 1

        logger.info(f"프로그램 수급 [{mkt_id}]: {ok}개")
        time.sleep(0.3)

    logger.info(f"프로그램 수급 총 {len(result)}개")
    return result
