# scripts/stats.py
"""누적 복기 통계 — 패턴별/스코어 구간별 승률 + 멀티데이 수익률 집계"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_SIGNALS_DIR = Path("data/signals")
_CACHED_STATS_PATH = Path("reports/cumulative_stats.json")

_SCORE_BANDS = [
    ("0~5",   0,  5),
    ("6~9",   6,  9),
    ("10~13", 10, 13),
    ("14+",   14, 99),
]

_CHG_BANDS = [
    ("+10~15%", 10, 15),
    ("+15~20%", 15, 20),
    ("+20~25%", 20, 25),
    ("+25~30%", 25, 30),
]


# ── 헬퍼 ───────────────────────────────────────────────────────────

def _score_band(score: int) -> str:
    for label, lo, hi in _SCORE_BANDS:
        if lo <= score <= hi:
            return label
    return "기타"


def _median(vals: list[float]) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 == 1 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def _sample_label(n: int) -> str:
    """표본 수 경고 레이블."""
    if n < 5:
        return "데이터부족"
    if n < 20:
        return "참고용"
    return "관찰가능"


def _group_stat(vals: list[float]) -> dict | None:
    """평균/중앙값/승률/n/표본레이블 계산."""
    if not vals:
        return None
    n    = len(vals)
    mean = round(sum(vals) / n, 2)
    med  = _median(vals)
    win  = round(sum(1 for v in vals if v > 0) / n * 100, 1)
    return {
        "n":            n,
        "mean":         mean,
        "median":       round(med, 2) if med is not None else None,
        "win_rate":     win,
        "sample_label": _sample_label(n),
    }


# ── 데이터 로드 ─────────────────────────────────────────────────────

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


# ── 기존 통계 (변경 없음) ────────────────────────────────────────────

def _stock_entry(r: dict) -> dict:
    """드릴다운용 종목 1건 요약 (D+1 갭 수익률 기준)."""
    return {
        "date":   r.get("signal_date", ""),
        "name":   r.get("name", ""),
        "code":   r.get("code", ""),
        "pct":    float(r.get("gap_pct") or 0),
        "result": r.get("result", ""),
    }


def _calc_pattern_stats(reviews: list[dict]) -> dict:
    counts: dict[str, dict] = {}
    for r in reviews:
        if r.get("result") not in ("성공", "실패"):
            continue
        pat = r.get("pattern_type") or "없음"
        if pat not in counts:
            counts[pat] = {"total": 0, "success": 0, "stocks": []}
        counts[pat]["total"] += 1
        if r["result"] == "성공":
            counts[pat]["success"] += 1
        counts[pat]["stocks"].append(_stock_entry(r))
    return {
        pat: {
            "total":   v["total"],
            "success": v["success"],
            "rate":    round(v["success"] / v["total"] * 100, 1),
            "stocks":  v["stocks"],
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
            counts[band] = {"total": 0, "success": 0, "stocks": []}
        counts[band]["total"] += 1
        if r["result"] == "성공":
            counts[band]["success"] += 1
        counts[band]["stocks"].append(_stock_entry(r))

    result = {}
    for label, _, _ in _SCORE_BANDS:
        if label in counts:
            v = counts[label]
            result[label] = {
                "total":   v["total"],
                "success": v["success"],
                "rate":    round(v["success"] / v["total"] * 100, 1),
                "stocks":  v["stocks"],
            }
    return result


def _calc_multiday_stats(reviews: list[dict]) -> dict:
    """멀티데이 수익률 집계: 패턴별 평균, 결과타입 비율, 교집합 비교."""

    def _avg_by_pattern(entries: list[dict], field: str) -> dict:
        groups: dict[str, list] = {}
        stocks: dict[str, list] = {}
        for e in entries:
            v = e.get(field)
            if v is None:
                continue
            pat = e.get("pattern_type") or "없음"
            groups.setdefault(pat, []).append(float(v))
            stocks.setdefault(pat, []).append({
                "code": e.get("code", ""),
                "name": e.get("name", ""),
                "date": e.get("signal_date", ""),
                "pct":  round(float(v), 2),
            })
        result = {}
        for pat, vals in sorted(groups.items(), key=lambda x: -len(x[1])):
            result[pat] = {
                "count":  len(vals),
                "mean":   round(sum(vals) / len(vals), 2),
                "stocks": sorted(stocks[pat], key=lambda x: x["date"], reverse=True),
            }
        return result

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

    # 교집합 vs 비교집합 D+1 시가 비교 (기존 필드 유지)
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


# ── 신규: 교집합/비교집합 상세 통계 ────────────────────────────────

def _calc_inter_full_stats(reviews: list[dict]) -> dict:
    """교집합/비교집합 분리 통계: 평균/중앙값/승률/MFE/MAE."""

    def _st(entries: list[dict], field: str) -> dict | None:
        vals = [float(r[field]) for r in entries if r.get(field) is not None]
        return _group_stat(vals)

    inter  = [r for r in reviews if r.get("in_inter")]
    ninter = [r for r in reviews if not r.get("in_inter")]

    def _build(grp: list[dict]) -> dict:
        return {
            "d1_open":  _st(grp, "d1_open_pct"),
            "d1_close": _st(grp, "d1_close_pct"),
            "d3_close": _st(grp, "d3_close_pct"),
            "mfe":      _st(grp, "mfe"),
            "mae":      _st(grp, "mae"),
        }

    return {
        "inter":  _build(inter),
        "ninter": _build(ninter),
    }


# ── 신규: 상승률 구간별 통계 ────────────────────────────────────────

def _calc_change_band_stats(reviews: list[dict]) -> list[dict]:
    """signal-day 등락률 구간별 성과 통계."""
    results = []
    for label, lo, hi in _CHG_BANDS:
        band = [
            r for r in reviews
            if r.get("signal_change_pct") is not None
            and lo <= float(r["signal_change_pct"]) < hi
        ]
        n = len(band)

        def _st(field: str, entries: list[dict] = band) -> dict | None:
            vals = [float(r[field]) for r in entries if r.get(field) is not None]
            return _group_stat(vals) if vals else None

        results.append({
            "label":        label,
            "n":            n,
            "sample_label": _sample_label(n),
            "d1_open":      _st("d1_open_pct"),
            "d3_close":     _st("d3_close_pct"),
            "mfe":          _st("mfe"),
            "mae":          _st("mae"),
        })
    return results


# ── 캐시 I/O ─────────────────────────────────────────────────────────

def _load_cached_stats() -> dict:
    """reports/cumulative_stats.json 에서 캐시 로드 (GitHub Actions 폴백용)."""
    try:
        if _CACHED_STATS_PATH.exists():
            return json.loads(_CACHED_STATS_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"누적 통계 캐시 로드 실패: {e}")
    return {}


def _save_cached_stats(data: dict) -> None:
    """reports/cumulative_stats.json 에 캐시 저장."""
    try:
        _CACHED_STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHED_STATS_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(f"누적 통계 캐시 저장: {_CACHED_STATS_PATH}")
    except Exception as e:
        logger.warning(f"누적 통계 캐시 저장 실패: {e}")


# ── 메인 ────────────────────────────────────────────────────────────

def run() -> dict:
    reviews = _load_all_reviews()
    measured = [r for r in reviews if r.get("result") in ("성공", "실패")]

    cached = _load_cached_stats()
    cached_total = cached.get("total_measured", 0)

    if len(measured) < cached_total:
        # 복원된 review.json이 캐시보다 적음 (GitHub Actions 부분 복원 케이스)
        # 캐시 우선 사용 — 덮어쓰지 않음
        logger.info(f"캐시 우선 사용: measured={len(measured)} < cached={cached_total}")
        return cached

    if not measured:
        return cached

    result: dict = {
        "total_measured": len(measured),
        "pattern":        _calc_pattern_stats(reviews),
        "score_band":     _calc_score_stats(reviews),
    }

    # 멀티데이 통계 (D+1 데이터 있는 항목이 1개 이상일 때)
    multiday = _calc_multiday_stats(reviews)
    if multiday.get("d1_count", 0) > 0:
        result["multiday"] = multiday

    # 교집합/비교집합 상세 통계 (D+1 데이터 있는 항목 기준)
    d1_reviews = [r for r in reviews if r.get("d1_open_pct") is not None]
    if d1_reviews:
        result["inter_full_stats"] = _calc_inter_full_stats(d1_reviews)

    # 상승률 구간별 통계 (signal_change_pct가 있는 항목 기준)
    chg_reviews = [r for r in reviews if r.get("signal_change_pct") is not None and r.get("d1_open_pct") is not None]
    if chg_reviews:
        result["change_band_stats"] = _calc_change_band_stats(chg_reviews)

    # GitHub Actions에서 읽을 수 있도록 캐시 저장
    _save_cached_stats(result)
    return result


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = run()
    print(f"누적 측정: {result.get('total_measured', 0)}개")
    for pat, v in result.get("pattern", {}).items():
        print(f"  {pat}: {v['total']}개 / {v['rate']}%")
