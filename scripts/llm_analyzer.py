# scripts/llm_analyzer.py
"""Gemini API를 이용한 뉴스 재료 1줄 요약"""

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import GEMINI_API_KEY, GEMINI_MODEL

logger = logging.getLogger(__name__)

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


_PROMPT_TEMPLATE = """당신은 한국 주식 단기 매매 전문가입니다.
아래 종목의 뉴스를 보고 종가베팅 관점에서 1줄로 요약하세요.

종목: {name} ({code})
오늘 등락률: {change_pct:+.2f}%
패턴: {pattern_type}

뉴스 제목:
{titles}

규칙:
- 뉴스/재료 구분 없이 하나로 합산
- 중복 기사는 1개로 통합
- 형식: 재료: [키워드] 핵심이유 (분류)
- 분류는 반드시 신규재료/섹터확산/없음/위험 중 정확히 하나
  · 근거 불명확 → (없음), 형식: 재료: (없음)
  · CB/유증/감자/횡령/공시 → (위험)
  · 여러 종목 함께 움직임 → (섹터확산)
  · 명확한 개별 재료 → (신규재료)
- 전체 15~30자 이내

JSON으로만 출력 (코드블록·설명 없이):
{{"line": "재료: [키워드] 핵심이유 (분류)"}}

예시:
{{"line": "재료: [AI/반도체] 수요 증가 (섹터확산)"}}"""


def analyze_news(
    code: str,
    name: str,
    change_pct: float,
    pattern_type: str,
    news_titles: list[str],
) -> str | None:
    """
    뉴스 제목 목록을 Gemini로 분석해 1줄 재료 문자열 반환.
    예: "재료: [AI/반도체] 수요 증가 (섹터확산)"
    실패 시 None — 파이프라인 중단 금지.
    """
    if not news_titles:
        return None

    client = _get_client()
    if client is None:
        return None

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
            return None

        result = json.loads(text[start:end])
        line = str(result.get("line", "")).strip()
        if not line:
            return None

        logger.info(f"[{code}] LLM 완료: {line}")
        return line

    except Exception as e:
        logger.warning(f"[{code}] LLM 분석 실패: {e}")
        return None
