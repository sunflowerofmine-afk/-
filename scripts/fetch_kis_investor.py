# scripts/fetch_kis_investor.py
"""KIS OpenAPI — 투자자 유형별 순매수 세분화 (연기금/투신/사모/금융투자)

TR_ID: FHKST130040C0 (국내주식 기간별 투자자별 매매동향)
반환 단위: 주(株) — pipeline에서 종가 곱해 원 변환
실패 시 예외 발생 금지 — 빈 dict 반환
"""

import logging
import time
from datetime import datetime
from pathlib import Path
import sys

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import KIS_APP_KEY, KIS_APP_SECRET

logger = logging.getLogger(__name__)

_KIS_BASE      = "https://openapi.koreainvestment.com:9443"
_TOKEN_URL     = f"{_KIS_BASE}/oauth2/tokenP"
_INVESTOR_URL  = f"{_KIS_BASE}/uapi/domestic-stock/v1/quotations/investor"

_cached_token: dict = {"token": "", "expires_at": 0.0}


def _get_access_token() -> str:
    """Access token 발급 — 24시간 캐시"""
    now = time.time()
    if _cached_token["token"] and now < _cached_token["expires_at"] - 60:
        return _cached_token["token"]

    resp = requests.post(
        _TOKEN_URL,
        json={
            "grant_type": "client_credentials",
            "appkey":     KIS_APP_KEY,
            "appsecret":  KIS_APP_SECRET,
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data["access_token"]
    _cached_token["token"]      = token
    _cached_token["expires_at"] = now + 86400
    logger.info("KIS access token 발급 완료")
    return token


def fetch_investor_breakdown(code: str, date_str: str | None = None) -> dict:
    """
    KIS API로 종목별 투자자 유형 순매수 조회.

    Returns:
        {
            "pension_net":      float | None,  # 연기금 (주)
            "invest_trust_net": float | None,  # 투신
            "private_fund_net": float | None,  # 사모펀드
            "fin_invest_net":   float | None,  # 금융투자
        }
        실패 시 빈 dict 반환.
    """
    if not KIS_APP_KEY or not KIS_APP_SECRET:
        return {}

    today = date_str or datetime.now().strftime("%Y%m%d")

    try:
        token = _get_access_token()
        headers = {
            "authorization": f"Bearer {token}",
            "appkey":        KIS_APP_KEY,
            "appsecret":     KIS_APP_SECRET,
            "tr_id":         "FHKST130040C0",
        }
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD":         code,
            "FID_INPUT_DATE_1":       today,
            "FID_INPUT_DATE_2":       today,
            "FID_PERIOD_DIV_CODE":    "D",
        }
        resp = requests.get(_INVESTOR_URL, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("rt_cd") != "0":
            logger.debug(f"[{code}] KIS investor API 응답 오류: {data.get('msg1', '')}")
            return {}

        rows = data.get("output2") or []
        if not rows:
            return {}

        row = rows[0]  # 최신 1거래일

        def _v(key: str) -> float | None:
            val = row.get(key, "")
            try:
                v = float(val)
                return v if v != 0 else None
            except (ValueError, TypeError):
                return None

        result = {
            "pension_net":      _v("pnsn_fund_ntby_vol"),   # 연기금
            "invest_trust_net": _v("mrbn_ntby_vol"),         # 투신
            "private_fund_net": _v("samo_fund_ntby_vol"),    # 사모펀드
            "fin_invest_net":   _v("fnnc_invt_ntby_vol"),    # 금융투자
        }
        logger.info(
            f"[{code}] KIS 투자자 세분화 — "
            f"연기금:{result['pension_net']} 투신:{result['invest_trust_net']} "
            f"사모:{result['private_fund_net']} 금융투자:{result['fin_invest_net']}"
        )
        return result

    except Exception as e:
        logger.warning(f"[{code}] KIS investor 조회 실패: {e}")
        return {}
