# scripts/fetch_dart.py
"""DART OpenAPI — 후보 종목 당일 공시 조회"""

import io
import json
import logging
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import requests

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DART_API_KEY, DATA_DIR, REQUEST_TIMEOUT

logger = logging.getLogger(__name__)

DART_BASE      = "https://opendart.fss.or.kr/api"
CORP_MAP_PATH  = DATA_DIR / "dart_corp_map.json"
CORP_MAP_MAX_AGE_DAYS = 7   # 7일마다 갱신

# ── 공시 제목 긍/부정 키워드 ────────────────────────────────
_POS_KW = ["수주", "계약", "공급", "수출", "매출", "흑자", "협약", "합병", "인수", "투자유치"]
_NEG_KW = ["횡령", "배임", "상장폐지", "관리종목", "감사의견", "자본잠식", "부도", "불성실", "검찰"]

# ── 표시할 공시 유형 (중요도 높은 것만) ────────────────────
_IMPORTANT_TYPES = {"A", "B", "C", "D", "I"}   # 정기/주요/발행/지분/거래소


def _is_corp_map_fresh() -> bool:
    if not CORP_MAP_PATH.exists():
        return False
    age = datetime.now() - datetime.fromtimestamp(CORP_MAP_PATH.stat().st_mtime)
    return age.days < CORP_MAP_MAX_AGE_DAYS


def _download_corp_map() -> dict[str, str]:
    """DART 전 종목 corp_code ↔ stock_code 매핑 다운로드.
    반환: {stock_code(6자리): corp_code(8자리)}
    """
    if not DART_API_KEY:
        logger.warning("DART_API_KEY 미설정 — corp_map 다운로드 불가")
        return {}
    try:
        resp = requests.get(
            f"{DART_BASE}/corpCode.xml",
            params={"crtfc_key": DART_API_KEY},
            timeout=30,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"DART corp_map 다운로드 실패: {e}")
        return {}

    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            xml_bytes = z.read(z.namelist()[0])
        root = ET.fromstring(xml_bytes)
    except Exception as e:
        logger.warning(f"DART corp_map 파싱 실패: {e}")
        return {}

    mapping: dict[str, str] = {}
    for item in root.findall("list"):
        corp_code  = (item.findtext("corp_code") or "").strip()
        stock_code = (item.findtext("stock_code") or "").strip()
        if stock_code and corp_code:
            mapping[stock_code] = corp_code

    logger.info(f"DART corp_map 로드: {len(mapping)}개 종목")
    return mapping


def _load_corp_map() -> dict[str, str]:
    """캐시된 corp_map 반환. 없거나 오래됐으면 재다운로드."""
    if _is_corp_map_fresh():
        try:
            return json.loads(CORP_MAP_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    mapping = _download_corp_map()
    if mapping:
        CORP_MAP_PATH.write_text(
            json.dumps(mapping, ensure_ascii=False), encoding="utf-8"
        )
    return mapping


def _classify(title: str) -> str:
    """공시 제목 → 긍정/부정/중립 분류."""
    if any(kw in title for kw in _NEG_KW):
        return "neg"
    if any(kw in title for kw in _POS_KW):
        return "pos"
    return "neutral"


def _fetch_disclosures(corp_code: str, date_str: str) -> list[dict]:
    """특정 corp_code의 당일 공시 목록 조회."""
    try:
        resp = requests.get(
            f"{DART_BASE}/list.json",
            params={
                "crtfc_key": DART_API_KEY,
                "corp_code":  corp_code,
                "bgn_de":     date_str,
                "end_de":     date_str,
                "page_count": 10,
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"DART 공시 조회 실패 (corp_code={corp_code}): {e}")
        return []

    if data.get("status") != "000":
        return []   # 조회 결과 없음 (정상)

    return data.get("list", [])


def _format_disclosure(item: dict, classify: str) -> str:
    """공시 1건 → 텔레그램 표시 문자열."""
    title = item.get("report_nm", "").strip()
    badge = "⚠️" if classify == "neg" else ("📈" if classify == "pos" else "📋")
    return f"{badge} {title}"


def fetch_dart_for_candidates(
    codes: list[str],
    date_str: str | None = None,
) -> dict[str, list[str]]:
    """후보 종목 코드 목록의 당일 공시를 일괄 조회.

    Args:
        codes: 종목코드 리스트 (예: ["005930", "000660"])
        date_str: 조회 날짜 YYYYMMDD (None이면 오늘)

    Returns:
        {종목코드: [표시 문자열, ...]}
        공시 없으면 빈 리스트.
    """
    if not DART_API_KEY:
        logger.info("DART_API_KEY 미설정 — 공시 조회 건너뜀")
        return {}

    if not codes:
        return {}

    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")

    corp_map = _load_corp_map()
    result: dict[str, list[str]] = {}

    for code in codes:
        corp_code = corp_map.get(code)
        if not corp_code:
            logger.debug(f"[DART] {code}: corp_code 매핑 없음")
            result[code] = []
            continue

        disclosures = _fetch_disclosures(corp_code, date_str)
        time.sleep(0.3)   # DART API 부하 방지

        lines = []
        for item in disclosures:
            pblntf_ty = item.get("pblntf_ty", "")
            if pblntf_ty not in _IMPORTANT_TYPES:
                continue
            title = item.get("report_nm", "")
            cls   = _classify(title)
            lines.append(_format_disclosure(item, cls))

        result[code] = lines
        if lines:
            logger.info(f"[DART] {code}: 공시 {len(lines)}건")
        else:
            logger.debug(f"[DART] {code}: 당일 공시 없음")

    return result
