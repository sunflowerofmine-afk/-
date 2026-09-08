"""알림 출력 스모크 — 모든 분기를 한 번씩 렌더해 본다. 네트워크 없이 돈다.

왜 필요한가: `import` 성공은 아무것도 보장하지 않는다. 파이썬은 함수 안의
전역 이름을 **호출 시점에** 찾기 때문에, 정의가 사라진 이름이 있어도 import는
통과하고 그 함수가 실제로 불릴 때 죽는다. 2026-09-08 15:35 대시보드가 그렇게
죽었다(`_OBS_TAG_COLOR`). 그날 14:25·04:51 실행은 멀쩡했다 — 관찰 후보가
있는 날에만 그 분기를 타기 때문이다.

그래서 **분기마다 한 번씩 실제로 렌더**한다. 데이터가 있는 날/없는 날을 모두 태운다.

    python -m scripts.smoke_render        # 실패하면 exit 1
    python -m scripts.smoke_render -v     # 렌더 결과도 출력
"""

from __future__ import annotations

import argparse
import sys
import traceback

_MARKET = {"kospi_total_tv_eok": 298000, "kosdaq_total_tv_eok": 64000}

_BASE_EXTRA = {
    "tv_1500_count": 6, "market_regime": "약세", "market_subtype": "",
    "market_adl": 0.38, "market_direction": "", "limit_up_count": 0,
    "limit_up_names": [], "kospi_level": 6980.2, "kospi_chg": -0.8,
    "kosdaq_level": 818.3, "kosdaq_chg": -1.5,
    "index_regime": {"kospi_regime": "혼조", "kosdaq_regime": "약세"},
    "core_count": 0, "top5_concentration_pct": 46.8, "risk_appetite": "중립",
    "buy_review_count": 0, "largecap_count": 0, "twotop_count": 0, "macro": {},
}

_CAND = [{"name": "한화오션", "code": "042660", "change_pct": 6.31,
          "trading_value": 241_000_000_000, "nxt_dominant": True,
          "patterns": {"pattern_type_label": "당일돌파형"}}]

_SECTORS = [{"sector_name": "반도체와반도체장비", "market_ratio_pct": 18.4,
             "tv_eok": 12000, "change_pct": 2.1}]


def _cases():
    """(이름, run_type, extra 덮어쓸 값, 후보, 섹터) — 분기를 고루 태운다."""
    full = dict(_BASE_EXTRA, limit_up_count=3, limit_up_names=["삼부토건", "우진", "이화공영"],
                core_count=1, buy_review_count=1, macro={"usdkrw": 1346.0, "usdkrw_chg": -1.0},
                market_direction="상승")
    yield "1차 · 후보 있음", "1차", dict(full), _CAND, _SECTORS
    yield "1차 · 빈 날", "1차", dict(_BASE_EXTRA), [], []
    yield "2차 15:35 · 대형주 지연", "2차", dict(full, largecap_deferred=True), _CAND, _SECTORS
    yield "2차 17:50 · 대형주 정상", "2차", dict(full, largecap_count=3), _CAND, _SECTORS
    yield "2차 · 투탑 과매도", "2차", dict(full, twotop_count=2, largecap_deferred=True), _CAND, _SECTORS
    yield "수동 19:30", "수동", dict(full, largecap_count=2), _CAND, _SECTORS
    yield "2차 · 전부 빈 값", "2차", dict(_BASE_EXTRA), [], []


def _check_market_summary(verbose: bool) -> list[str]:
    from scripts.notifier import format_market_summary
    fails = []
    for name, rt, extra, cands, secs in _cases():
        try:
            msg = format_market_summary(
                _MARKET, "2026-09-09 15:35", rt, extra=extra,
                leading_sectors=secs,
                pattern_counts={"당일돌파형": len(cands)} if cands else {},
                core_candidates=cands,
            )
            assert msg and "종가베팅" in msg, "본문이 비었다"
            # 1차만 잠정 표기가 붙어야 한다 — 2026-09-08 시각 오표기 회귀 방지
            if rt == "1차":
                assert "잠정" in msg, "1차인데 '(마감 전 잠정)' 표기가 없다"
            else:
                assert "잠정" not in msg, "1차가 아닌데 잠정 표기가 붙었다"
            # 마감 전은 1차뿐이다 — 19:30이 '종가 진입 준비'로 나가던 회귀 방지
            if rt != "1차":
                assert "종가 진입 준비" not in msg, "마감 후 실행에 '종가 진입 준비' 문구"
            if verbose:
                print(f"\n----- {name} -----\n{msg}")
        except Exception as e:
            fails.append(f"[시장요약] {name}: {e}")
            if verbose:
                traceback.print_exc()
    return fails


def _check_dashboard_sections(verbose: bool) -> list[str]:
    """대시보드 섹션 — 데이터가 있어야만 타는 분기를 강제로 태운다."""
    from scripts._dashboard_sections import (
        _section_recent_base_pool, compute_daily_gate, compute_largecap_gate,
    )
    fails = []
    obs = [
        {"종목명": "A", "종목코드": "000001", "pattern_type_label": "재돌파형",
         "intraday_gap_pct": -1.2, "close_from_base_high_pct": -3.4, "today_tv_ratio": 1.8},
        {"종목명": "B", "종목코드": "000002", "pattern_type_label": "고가수축형",
         "intraday_gap_pct": -7.0, "close_from_base_high_pct": -9.1, "today_tv_ratio": 0.6},
    ]
    for label, arg in (("관찰 후보 있음", obs), ("관찰 후보 없음", [])):
        try:
            html = _section_recent_base_pool(arg)
            if arg:
                assert "obs-notice" in html, "관찰 후보 섹션이 비었다"
            if verbose:
                print(f"[대시보드] {label}: {len(html)}자")
        except Exception as e:
            fails.append(f"[대시보드] {label}: {e}")

    try:
        for args in ((0, None, None, None, None, None), (3, "강세", 0.6, 38.0, "우호", 2)):
            assert compute_daily_gate(*args)[0]
        for kw in ({}, {"deferred": True}, {"largecap_ran": False}):
            assert compute_largecap_gate(0, 0, **kw)[0]
        assert compute_largecap_gate(3, 0)[0] and compute_largecap_gate(0, 2)[0]
    except Exception as e:
        fails.append(f"[게이트] {e}")
    return fails


def _check_morning(verbose: bool) -> list[str]:
    """아침 브리핑 개장 전 분기 — 네트워크를 타지 않는 쪽만."""
    fails = []
    try:
        import pandas as pd
        from scripts import morning_briefing as mb
        df = pd.DataFrame([{"종목코드": "042660", "종목명": "한화오션",
                            "signal_price": 95000, "sector": "조선",
                            "pattern_type_label": "당일돌파형", "in_inter": True}])
        orig = mb._is_pre_open
        mb._is_pre_open = lambda: True          # 08:00 이전 분기 강제
        try:
            msg = mb.build_message(df, "2026-09-08")
            assert "08:00" in msg, "개장 전 문구가 없다"
            assert "시가" not in msg.split("📊")[0], "개장 전인데 시가를 표시한다"
            if verbose:
                print(f"\n----- 아침 브리핑(개장 전) -----\n{msg}")
        finally:
            mb._is_pre_open = orig
    except Exception as e:
        fails.append(f"[아침] {e}")
        if verbose:
            traceback.print_exc()
    return fails


def main() -> int:
    ap = argparse.ArgumentParser(description="알림·대시보드 출력 스모크")
    ap.add_argument("-v", "--verbose", action="store_true", help="렌더 결과도 출력")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    fails = (_check_market_summary(args.verbose)
             + _check_dashboard_sections(args.verbose)
             + _check_morning(args.verbose))

    if fails:
        print(f"\n스모크 실패 {len(fails)}건")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("스모크 통과 — 모든 분기 렌더 성공")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
