# scripts/stats.py
"""누적 복기 통계 — 패턴별/스코어 구간별 승률 + 멀티데이 수익률 집계"""

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


def _calc_multiday_stats(reviews: list[dict]) -> dict:
    """멀티데이 수익률 집계: 패턴별 평균, 결과타입 비율, 교집합 비교."""

    def _avg_by_pattern(entries: list[dict], field: str) -> dict:
        groups: dict[str, list] = {}
        for e in entries:
            v = e.get(field)
            if v is None:
                continue
            pat = e.get("pattern_type") or "없음"
            groups.setdefault(pat, []).append(float(v))
        return {
            pat: {"count": len(vals), "mean": round(sum(vals) / len(vals), 2)}
            for pat, vals in sorted(groups.items(), key=lambda x: -len(x[1]))
        }

    d1_valid = [r for r in reviews if r.get("d1_open_pct") is not None]
    d3_valid = [r for r in reviews if r.get("d3_high_pct") is not None]
    d5_valid = [r for r in reviews if r.get("mfe") is not None and r.get("d5_high_pct") is not None]

    # 결과 타입별 비율 (final 우선, 없으면 interim)
    type_counts: dict[str, int] = {}
    _TYPES = ("즉시성공형", "눌림후재상승형", "스윙전환가능형", "과열소멸형", "실패형")
    for r in d1_valid:
        rtype = r.get("final_result_type") or r.get("interim_result_type")
        if rtype in _TYPES:
            type_counts[rtype] = type_counts.get(rtype, 0) + 1
    total_typed = sum(type_counts.values())
    result_type_counts = {
        t: {
            "count": type_counts.get(t, 0),
            "pct":   round(type_counts.get(t, 0) / total_typed * 100, 1) if total_typed > 0 else 0.0,
        }
        for t in _TYPES
    } if total_typed > 0 else {}

    # 교집합 vs 비교집합 D+1 시가 비교
    inter_comparison: dict = {}
    inter_d1  = [r["d1_open_pct"] for r in d1_valid if r.get("in_inter") and r.get("d1_open_pct") is not None]
    ninter_d1 = [r["d1_open_pct"] for r in d1_valid if not r.get("in_inter") and r.get("d1_open_pct") is not None]
    inter_d3  = [r["d3_high_pct"] for r in d3_valid if r.get("in_inter") and r.get("d3_high_pct") is not None]
    if inter_d1:
        inter_comparison["inter_d1_mean"]  = round(sum(inter_d1)  / len(inter_d1),  2)
        inter_comparison["inter_d1_count"] = len(inter_d1)
    if inter_d3:
        inter_comparison["inter_d3_mean"]  = round(sum(inter_d3)  / len(inter_d3),  2)
        inter_comparison["inter_d3_count"] = len(inter_d3)
    if ninter_d1:
        inter_comparison["ninter_d1_mean"]  = round(sum(ninter_d1) / len(ninter_d1), 2)
        inter_comparison["ninter_d1_count"] = len(ninter_d1)

    return {
        "d1_count":           len(d1_valid),
        "d3_count":           len(d3_valid),
        "d5_count":           len(d5_valid),
        "d1_open_by_pattern": _avg_by_pattern(d1_valid, "d1_open_pct"),
        "d3_mfe_by_pattern":  _avg_by_pattern(d3_valid, "d3_high_pct"),
        "d5_mfe_by_pattern":  _avg_by_pattern(d5_valid, "mfe"),
        "result_type_counts": result_type_counts,
        "inter_comparison":   inter_comparison,
    }


def run() -> dict:
    reviews = _load_all_reviews()
    measured = [r for r in reviews if r.get("result") in ("성공", "실패")]
    if not measured:
        return {}

    result: dict = {
        "total_measured": len(measured),
        "pattern":        _calc_pattern_stats(reviews),
        "score_band":     _calc_score_stats(reviews),
    }

    # 멀티데이 통계는 D+1 데이터 있는 항목이 1개 이상일 때만 포함
    multiday = _calc_multiday_stats(reviews)
    if multiday.get("d1_count", 0) > 0:
        result["multiday"] = multiday

    return result
