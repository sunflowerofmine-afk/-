"""
fetch_data.py
네이버 증권 코스피/코스닥 전 종목 데이터 수집
"""

import sys
import time
import logging
from datetime import datetime
from pathlib import Path

import requests
import pandas as pd
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import HEADERS, MARKETS, RAW_DIR

# ── 로깅 설정 ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

BASE_URL = "https://finance.naver.com/sise/sise_market_sum.naver"


def _get_last_page(market_code: int) -> int:
    """해당 시장의 마지막 페이지 번호 반환"""
    url = f"{BASE_URL}?sosok={market_code}&page=1"
    resp = requests.get(url, headers=HEADERS, timeout=10)
    resp.encoding = "euc-kr"

    soup = BeautifulSoup(resp.text, "lxml")
    pager = soup.select_one("td.pgRR > a")  # 맨 끝 페이지 링크
    if pager is None:
        return 1

    href = pager["href"]  # e.g. "?sosok=0&page=34"
    last = int(href.split("page=")[-1])
    return last


def _fetch_page(market_code: int, page: int) -> pd.DataFrame:
    """단일 페이지 테이블 파싱 → DataFrame 반환"""
    url = f"{BASE_URL}?sosok={market_code}&page={page}"
    resp = requests.get(url, headers=HEADERS, timeout=10)
    resp.encoding = "euc-kr"

    soup = BeautifulSoup(resp.text, "lxml")
    table = soup.select_one("table.type_2")
    if table is None:
        return pd.DataFrame()

    rows = []
    for tr in table.select("tr"):
        cols = tr.select("td")
        if len(cols) < 7:
            continue

        name_tag = cols[1].select_one("a")
        if name_tag is None:
            continue

        def clean(text: str) -> str:
            return text.strip().replace(",", "").replace("+", "").replace("%", "")

        rows.append({
            "종목명":   name_tag.text.strip(),
            "현재가":   clean(cols[2].text),
            "등락률":   clean(cols[4].text),
            "거래량":   clean(cols[5].text),
            "거래대금": clean(cols[6].text),
        })

    return pd.DataFrame(rows)


def fetch_market(market_name: str, market_code: int) -> pd.DataFrame:
    """시장 전체 페이지 수집 → 단일 DataFrame 반환"""
    logger.info(f"[{market_name}] 마지막 페이지 확인 중...")
    last_page = _get_last_page(market_code)
    logger.info(f"[{market_name}] 총 {last_page}페이지 수집 시작")

    frames = []
    for page in range(1, last_page + 1):
        try:
            df = _fetch_page(market_code, page)
            if not df.empty:
                frames.append(df)
            if page % 5 == 0:
                logger.info(f"[{market_name}] {page}/{last_page} 페이지 완료")
            time.sleep(0.3)  # 서버 부하 방지
        except Exception as e:
            logger.warning(f"[{market_name}] {page}페이지 실패: {e}")
            time.sleep(1)
            continue

    if not frames:
        logger.error(f"[{market_name}] 수집된 데이터 없음")
        return pd.DataFrame()

    result = pd.concat(frames, ignore_index=True)

    # 숫자 컬럼 변환
    for col in ["현재가", "등락률", "거래량", "거래대금"]:
        result[col] = pd.to_numeric(result[col], errors="coerce")

    result.dropna(subset=["종목명", "현재가"], inplace=True)
    result.reset_index(drop=True, inplace=True)

    logger.info(f"[{market_name}] 총 {len(result)}개 종목 수집 완료")
    return result


def save_raw(df: pd.DataFrame, market_name: str, timestamp: str) -> Path:
    """raw/ 폴더에 CSV 저장, 파일명: YYYYMMDD_HHMM_KOSPI.csv"""
    filename = f"{timestamp}_{market_name}.csv"
    path = RAW_DIR / filename
    df.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info(f"저장 완료: {path}")
    return path


def run() -> dict[str, Path]:
    """전체 시장 수집 실행, 저장된 파일 경로 dict 반환"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    saved = {}

    for market_name, market_code in MARKETS.items():
        df = fetch_market(market_name, market_code)
        if df.empty:
            continue
        path = save_raw(df, market_name, timestamp)
        saved[market_name] = path

    return saved


if __name__ == "__main__":
    run()
