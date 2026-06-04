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
업종/섹터: {sector}
오늘 등락률: {change_pct:+.2f}%
패턴: {pattern_type}

뉴스 제목:
{titles}

[일반 시황 뉴스 판단 — 최우선 확인]
뉴스 제목들이 이 종목({name}) 또는 이 종목의 업종과 직접 관련 없이,
코스피/코스닥 지수 동향, 삼성전자 등 대형주 이슈, 시총 순위, 개미 매매 동향, 기관·외인 전체 수급 등
일반 시황 뉴스만으로 구성되어 있다면 → 반드시 아래 출력:
{{"line": "재료: [기타] 재료 불명확 (단순수급)"}}

출력 형식 (반드시 아래 형식 그대로):
재료: [카테고리] 핵심내용 (성격)

규칙:
- 카테고리: 아래 목록 중 1~3개만 선택. 목록에 없으면 반드시 [기타] 사용:
  로봇, AI반도체, 피지컬AI, 2차전지, 방산, 우주항공, 바이오, M&A, HBM,
  전력반도체, 전선/전력인프라, 태양광, 원전, 조선, 리튬, 자율주행,
  물류/자동화, 철강/소재, 화학, 엔터, 게임, 제약, 기타
  복수 테마면 예) [AI반도체/방산]
  ※ 위 목록에 없는 전통 업종(건설, 금융, 섬유, 음식료 등)은 반드시 [기타] 사용
  ※ 업종/섹터가 명시된 경우 해당 업종으로 카테고리를 보정하세요
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
{{"line": "재료: [전선/전력인프라] 원자재 가격 보전 (개별호재)"}}
{{"line": "재료: [기타] 재료 불명확 (단순수급)"}}"""


def analyze_news(
    code: str,
    name: str,
    change_pct: float,
    pattern_type: str,
    news_titles: list[str],
    sector: str = "",
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
            sector=sector or "미상",
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


def summarize_market_flow(
    run_date: str,
    market_regime: str,
    adl: float | None,
    leading_sectors: list[dict],
    limit_up_names: list[str],
    candidates: list[dict],
) -> str:
    """
    오늘 시장 자금 흐름 요약 — TELEGRAM_CHAT_ID 전용 심층 브리핑.

    입력:
      run_date       : "2026-06-04"
      market_regime  : "강세" / "중립" / "약세"
      adl            : 0.0~1.0 (상승종목 비율)
      leading_sectors: [{"sector_name": ..., "ratio": ...}, ...]
      limit_up_names : 상한가 종목명 리스트
      candidates     : 핵심 후보 종목 dict 리스트 (name, sector, llm_summary 포함)
    """
    client = _get_client()
    if not client:
        return "[요약 불가 - Gemini 미설정]"

    sector_text = "\n".join(
        f"  {i+1}. {s.get('sector_name','?')} ({s.get('ratio',0):.1f}%)"
        for i, s in enumerate(leading_sectors[:5])
    ) or "  (없음)"

    limit_up_text = ", ".join(limit_up_names[:10]) if limit_up_names else "없음"

    cand_text = ""
    for c in candidates[:5]:
        name    = c.get("name", "")
        sector  = c.get("sector", "")
        news    = c.get("news")
        summary = getattr(news, "llm_summary", None) or ""
        cand_text += f"  - {name}({sector}): {summary}\n"
    if not cand_text:
        cand_text = "  (없음)\n"

    adl_str = f"{adl*100:.1f}%" if adl is not None else "?"

    prompt = f"""당신은 한국 주식 단기 매매 전문가입니다.
오늘({run_date}) 한국 장 마감 후 데이터입니다.

시장 장세: {market_regime} (ADL {adl_str})

거래대금 상위 섹터 (시장 비중):
{sector_text}

상한가 종목: {limit_up_text}

오늘 주목 종목 및 재료:
{cand_text}
위 데이터를 분석해 아래 형식으로 정확히 출력하세요.

---
오늘 시장 흐름 ({run_date}):
- 시장 전체: [강세/약세/혼조 + 한 줄 설명]
- 돈이 몰린 곳: [섹터명 나열]
- 빠진 곳: [섹터명 나열, 파악 불가 시 "데이터 없음"]

섹터별 해석:
[거래대금 상위 섹터 최대 3개에 대해 아래 형식 반복]

N. [섹터명]
   - 이유: [오른 배경, 뉴스·이슈 맥락]
   - 대장주: [해당 섹터 선도 종목, 파악 불가 시 "확인 필요"]
   - 후발주: [뒤따라 오른 종목, 파악 불가 시 "확인 필요"]
   - 지속성: [단발성 / 단기 (2~3일) / 구조적 흐름] + 근거 한 줄
   - 종베 관점: [내일 종베 시 주의할 점]
---

규칙:
- 사실 기반으로만. 뉴스·공시·섹터 데이터 근거 없는 추측 금지.
- 대장주/후발주는 위 종목 데이터에 있는 종목 우선. 없으면 "확인 필요".
- 지속성 판단 근거를 반드시 한 줄 포함.
- 형식을 정확히 지킬 것. 추가 설명·인사말 없이 바로 출력."""

    try:
        from google.genai import types as _gtypes
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=_gtypes.GenerateContentConfig(
                temperature=0.3,
                thinking_config=_gtypes.ThinkingConfig(thinking_budget=0),
            ),
        )
        return resp.text.strip()
    except Exception as e:
        logger.warning(f"시장 흐름 요약 실패: {e}")
        return f"[요약 실패: {e}]"


def summarize_us_market(indices: dict, headlines: list[str]) -> str:
    """미국 지수 + 뉴스 헤드라인 → 2~3줄 한국어 브리핑 요약."""
    client = _get_client()
    if not client:
        return "[요약 불가 - Gemini 미설정]"

    idx_text = ", ".join(
        f"{name} {d['chg_pct']:+.2f}%"
        for name, d in indices.items()
        if d.get("chg_pct") is not None
    )
    news_text = "\n".join(f"- {h}" for h in headlines) if headlines else "(뉴스 없음)"

    prompt = f"""당신은 한국 주식 단기 매매 전문가입니다.
아래 전일 미국 시장 데이터를 보고 오늘 한국 장 시가 대응에 필요한 핵심만 한국어로 요약하세요.

지수: {idx_text}

주요 뉴스:
{news_text}

출력 형식 (정확히 아래 형식 준수):
[요약] (핵심 이슈 1~2문장)
→ (한국 시장 시가 영향 한 줄)

규칙:
- 불확실한 예측 금지, 사실 기반으로만 작성
- 전체 3줄 이내"""

    try:
        from google.genai import types as _gtypes
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=_gtypes.GenerateContentConfig(
                temperature=0.3,
                thinking_config=_gtypes.ThinkingConfig(thinking_budget=0),
            ),
        )
        return resp.text.strip()
    except Exception as e:
        logger.warning(f"미국장 요약 실패: {e}")
        return "[요약] " + " / ".join(headlines[:2]) if headlines else "[요약 실패]"
