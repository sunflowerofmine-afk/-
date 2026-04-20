# scripts/fetch_news.py
"""네이버 금융 종목 뉴스 수집 및 점수화"""

import sys
import logging
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import HEADERS, REQUEST_TIMEOUT, NEWS_SCORE_ENABLED
from scripts.models import NewsData

logger = logging.getLogger(__name__)

NEWS_URL = "https://finance.naver.com/item/news_news.naver"

# 점수화 키워드
_POSITIVE_3 = ["수주", "계약", "실적", "영업이익", "흑자", "정부", "승인", "허가", "선정", "공급"]
_POSITIVE_2 = ["매출", "성장", "확대", "신규", "MOU", "협약", "투자", "수출"]
_NEGATIVE   = ["적자", "소송", "벌금", "하락", "취소", "철회", "조사", "위반"]

KEYWORD_MAP = {
    "정책": ["정부", "부처", "정책", "규제", "지원", "법안", "예산", "보조금", "세제"],
    "실적": ["실적", "영업이익", "매출", "흑자", "적자", "어닝", "순이익", "EPS"],
    "수주": ["수주", "계약", "납품", "공급", "MOU", "협약", "수출"],
    "테마": ["AI", "인공지능", "반도체", "2차전지", "배터리", "바이오", "로봇", "전기차"],
}


def classify_keyword(title: str) -> str:
    for kw, terms in KEYWORD_MAP.items():
        if any(t in title for t in terms):
            return kw
    return "기타"


def _score_title(title: str) -> int:
    """단일 뉴스 제목 점수화 (0~3)"""
    if any(kw in title for kw in _NEGATIVE):
        return 0
    if any(kw in title for kw in _POSITIVE_3):
        return 3
    if any(kw in title for kw in _POSITIVE_2):
        return 2
    return 1


def _calc_news_score(titles: list[str]) -> int:
    """뉴스 목록 전체 점수 (0~3, 최고점 기준)"""
    if not titles:
        return 0
    return min(3, max(_score_title(t) for t in titles))


MAX_NEWS_TITLE_LEN = 50   # 제목 최대 표시 길이
MAX_NEWS_ITEMS    = 5    # 수집 개수 (LLM 분석용, 표시는 2개로 제한)


def fetch_news(code: str, max_items: int = MAX_NEWS_ITEMS) -> NewsData:
    """
    종목 뉴스 수집 및 점수화.
    - 중복 제거: 제목 strip 기준
    - 최대 2개 출력
    - 50자 초과 시 자르기
    반환: NewsData 객체
    실패 시 score=0, status="failed", titles=[] NewsData 반환 (예외 발생 금지).
    """
    result = NewsData(code=code)

    if not NEWS_SCORE_ENABLED:
        result.status = "disabled"
        return result

    try:
        url = f"{NEWS_URL}?code={code}&page=1"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.encoding = "euc-kr"
        except UnicodeDecodeError:
            resp.encoding = "cp949"

        soup = BeautifulSoup(resp.text, "lxml")
        table = soup.select_one("table.type5")

        if table is None:
            result.status = "empty"
            return result

        seen_titles: set[str] = set()
        for tr in table.select("tr"):
            cols = tr.select("td")
            if len(cols) < 2:
                continue
            title_tag = cols[0].select_one("a")
            if title_tag is None:
                continue
            title_raw = title_tag.text.strip()
            if not title_raw:
                continue
            # 중복 제거
            if title_raw in seen_titles:
                continue
            seen_titles.add(title_raw)
            # 50자 초과 시 자르기
            title = title_raw[:MAX_NEWS_TITLE_LEN]
            result.titles.append(title)
            result.timestamps.append(cols[1].text.strip() if len(cols) > 1 else "")
            result.keyword_tags.append(classify_keyword(title))
            if len(result.titles) >= max_items:
                break

        result.score  = _calc_news_score(result.titles)
        result.status = "ok" if result.titles else "empty"

    except Exception as e:
        logger.warning(f"[{code}] 뉴스 수집 실패: {e}")
        result.status = "failed"
        result.titles = []

    return result
