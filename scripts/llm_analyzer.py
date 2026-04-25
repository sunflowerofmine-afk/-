# scripts/llm_analyzer.py
"""Gemini API를 이용한 뉴스 재료 1줄 요약"""

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import GEMINI_API_KEY, GEMINI_MODEL

logger = logging.getLogger(__name__)

_DANGER_KEYWORDS = [
    "횡령", "배임", "상장폐지", "관리종목", "적자전환",
    "검찰", "수사", "기소", "대량매도",
]

_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    if not GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY 미설정 — LLM 분석 비활성화")
        return None
    try:
        from google import genai
        _client = genai.Client(api_key=GEMINI_API_KEY)
        logger.info(f"Gemini 클라이언트 초기화 완료: {GEMINI_MODEL}")
    except Exception as e:
        logger.warning(f"Gemini 초기화 실패: {e}")
    return _client


def _check_danger(titles: list[str]) -> str:
    """악재 키워드 감지 → '⚠️ 악재주의: 키워드' 반환. 없으면 빈 문자열."""
    text = " ".join(titles)
    found = [kw for kw in _DANGER_KEYWORDS if kw in text]
    if found:
        return f"⚠️ 악재주의: {'/'.join(found)}"
    return ""


_PROMPT_TEMPLATE = """당신은 한국 주식 단기 매매 전문가입니다.
아래 종목의 뉴스를 분석해 종가베팅 관점의 재료를 정확히 1줄로 출력하세요.

종목: {name} ({code})
오늘 등락률: {change_pct:+.2f}%
패턴: {pattern_type}

뉴스 제목:
{titles}

출력 형식 (반드시 아래 형식 그대로):
재료: [카테고리] 핵심내용 (성격)

규칙:
- 카테고리: 아래 목록 중 1~3개만 선택. 목록에 없으면 [기타]:
  로봇, AI반도체, 2차전지, 방산, 우주항공, 바이오, M&A, HBM, 전력반도체, 태양광, 원전, 조선, 리튬, 자율주행, 엔터, 게임, 제약, 기타
  복수 테마면 예) [AI반도체/방산]
- 성격은 아래 4개 중 정확히 하나만 사용:
  · (개별호재): 해당 종목만의 명확한 개별 호재
  · (섹터동조): 동일 테마 여러 종목이 함께 움직임
  · (단순수급): 재료 불명확, 수급/기술적 매수
  · (위험): CB·유증·감자·횡령·수사·공시 등 리스크
- 뉴스 원문 반복·기사 제목 나열 금지. 핵심만 10자 이내로 압축.
- 불명확하거나 판단 불가: 재료: [기타] 재료 불명확 (단순수급)
- 전체 출력은 반드시 1줄

JSON으로만 출력 (코드블록·설명 없이):
{{"line": "재료: [카테고리] 핵심내용 (성격)"}}

예시:
{{"line": "재료: [AI반도체] 수주 확대 (개별호재)"}}
{{"line": "재료: [방산] 수출 계약 (개별호재)"}}
{{"line": "재료: [AI반도체/방산] 테마 동반 상승 (섹터동조)"}}
{{"line": "재료: [기타] 재료 불명확 (단순수급)"}}"""


def analyze_news(
    code: str,
    name: str,
    change_pct: float,
    pattern_type: str,
    news_titles: list[str],
) -> str | None:
    """
    뉴스 제목 목록을 Gemini로 분석해 1줄 재료 문자열 반환.
    예: "재료: [AI반도체] 수요 증가 (섹터동조)"
    악재 키워드 발견 시 ⚠️ 접두어 추가 (자동 탈락 아닌 경고).
    실패 시 None — 파이프라인 중단 금지.
    """
    if not news_titles:
        return None

    danger_prefix = _check_danger(news_titles)

    client = _get_client()
    line = None

    if client is not None:
        titles_text = "\n".join(f"- {t}" for t in news_titles)
        prompt = _PROMPT_TEMPLATE.format(
            name=name,
            code=code,
            change_pct=change_pct,
            pattern_type=pattern_type,
            titles=titles_text,
        )

        try:
            from google.genai import types as _gtypes
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=_gtypes.GenerateContentConfig(
                    temperature=0.2,
                    thinking_config=_gtypes.ThinkingConfig(thinking_budget=0),
                ),
            )
            text = resp.text.strip()

            start = text.find("{")
            end   = text.rfind("}") + 1
            if start == -1 or end == 0:
                logger.warning(f"[{code}] LLM 응답에 JSON 없음: {text[:100]}")
            else:
                result = json.loads(text[start:end])
                line = str(result.get("line", "")).strip() or None

        except Exception as e:
            logger.warning(f"[{code}] LLM 분석 실패: {e}")

    if danger_prefix and line:
        line = f"{danger_prefix} | {line}"
    elif danger_prefix:
        line = danger_prefix

    if line:
        logger.info(f"[{code}] LLM 완료: {line}")
    return line
