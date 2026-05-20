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
_INVESTOR_URL  = f"{_KIS_BASE}/uapi/domestic-stock/v1/quotations/inquire-investor"

_cached_token: dict = {"token": "", "expires_at": 0.0}
_circuit: dict = {"failures": 0, "disabled_until": 0.0}  # 연속 실패 시 일시 비활성화
_CIRCUIT_THRESHOLD = 3   # 연속 N회 실패 시 차단
_CIRCUIT_COOLDOWN  = 300 # 5분간 비활성화


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
        timeout=5,
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

    # 회로차단기 — 연속 실패 시 5분간 건너뜀
    now_ts = time.time()
    if _circuit["failures"] >= _CIRCUIT_THRESHOLD and now_ts < _circuit["disabled_until"]:
        logger.debug(f"[{code}] KIS circuit open — 건너뜀 ({int(_circuit['disabled_until']-now_ts)}초 남음)")
        return {}

    today = date_str or datetime.now().strftime("%Y%m%d")

    try:
        token = _get_access_token()
        headers = {
            "authorization": f"Bearer {token}",
            "appkey":        KIS_APP_KEY,
            "appsecret":     KIS_APP_SECRET,
            "tr_id":         "FHKST01010300",
        }
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD":         code,
        }
        resp = requests.get(_INVESTOR_URL, headers=headers, params=params, timeout=5)
        resp.raise_for_status()
        data = resp.json()

        if data.get("rt_cd") != "0":
            logger.debug(f"[{code}] KIS investor API 응답 오류: {data.get('msg1', '')}")
            return {}

        outputs = data.get("output") or []
        if isinstance(outputs, list):
            row = outputs[0] if outputs else None
        else:
            row = outputs  # dict로 온 경우 그대로 사용
        if not row:
            return {}

        def _v(key: str) -> float | None:
            val = row.get(key, "")
            try:
                v = float(val)
                return v if v != 0 else None
            except (ValueError, TypeError):
                return None

        result = {
            "pension_net":      _v("pnsn_fund_ntby_qty"),   # 연기금
            "invest_trust_net": _v("mrbn_ntby_qty"),         # 투신
            "private_fund_net": _v("samo_fund_ntby_qty"),    # 사모펀드
            "fin_invest_net":   _v("fnnc_invt_ntby_qty"),    # 금융투자
        }
        _circuit["failures"] = 0  # 성공 시 초기화
        logger.info(
            f"[{code}] KIS 투자자 세분화 — "
            f"연기금:{result['pension_net']} 투신:{result['invest_trust_net']} "
            f"사모:{result['private_fund_net']} 금융투자:{result['fin_invest_net']}"
        )
        return result

    except Exception as e:
        _circuit["failures"] += 1
        if _circuit["failures"] >= _CIRCUIT_THRESHOLD:
            _circuit["disabled_until"] = time.time() + _CIRCUIT_COOLDOWN
            logger.warning(f"KIS investor 연속 {_circuit['failures']}회 실패 — 5분간 비활성화: {e}")
        else:
            logger.warning(f"[{code}] KIS investor 조회 실패 ({_circuit['failures']}/{_CIRCUIT_THRESHOLD}): {e}")
        return {}
