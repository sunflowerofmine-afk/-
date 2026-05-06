# scripts/stats.py
"""누적 복기 통계 — 패턴별/스코어 구간별 승률 집계"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_SIGNALS_DIR = Path("data/signals")

_SCORE_BANDS = [
    ("0~5",   0,  5),
    ("6~9",   6,  9),
    ("10~13", 10, 13),
    ("14+",   14, 99),
]


def _score_band(score: int) -> str:
    for label, lo, hi in _SCORE_BANDS:
        if lo <= score <= hi:
            return label
    return "기타"


def _load_all_reviews() -> list[dict]:
    reviews = []
    for p in sorted(_SIGNALS_DIR.glob("*_review.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                reviews.extend(data)
        except Exception as e:
            logger.warning(f"복기 JSON 로드 실패 {p}: {e}")
    return reviews


def _calc_pattern_stats(reviews: list[dict]) -> dict:
    counts: dict[str, dict] = {}
    for r in reviews:
        if r.get("result") not in ("성공", "실패"):
            continue
        pat = r.get("pattern_type") or "없음"
        if pat not in counts:
            counts[pat] = {"total": 0, "success": 0}
        counts[pat]["total"] += 1
        if r["result"] == "성공":
            counts[pat]["success"] += 1
    return {
        pat: {
            "total":   v["total"],
            "success": v["success"],
            "rate":    round(v["success"] / v["total"] * 100, 1),
        }
        for pat, v in sorted(counts.items(), key=lambda x: -x[1]["total"])
    }


def _calc_score_stats(reviews: list[dict]) -> dict:
    counts: dict[str, dict] = {}
    for r in reviews:
        if r.get("result") not in ("성공", "실패"):
            continue
        score = r.get("total_score")
        if score is None:
            continue
        band = _score_band(int(score))
        if band not in counts:
            counts[band] = {"total": 0, "success": 0}
        counts[band]["total"] += 1
        if r["result"] == "성공":
            counts[band]["success"] += 1

    result = {}
    for label, _, _ in _SCORE_BANDS:
        if label in counts:
            v = counts[label]
            result[label] = {
                "total":   v["total"],
                "success": v["success"],
                "rate":    round(v["success"] / v["total"] * 100, 1),
            }
    return result


def run() -> dict:
    reviews = _load_all_reviews()
    measured = [r for r in reviews if r.get("result") in ("성공", "실패")]
    if not measured:
        return {}
    return {
        "total_measured": len(measured),
        "pattern":        _calc_pattern_stats(reviews),
        "score_band":     _calc_score_stats(reviews),
    }
