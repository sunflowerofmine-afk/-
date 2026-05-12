# scripts/pipeline.py
"""전체 파이프라인 메인 모듈"""

import sys
import logging
import argparse
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import (
    LOG_DIR, SIGNALS_DIR, MIN_TRADING_VALUE_EOK,
    ENABLE_NEWS_FETCH, ENABLE_SUPPLY_FETCH, USE_LLM_NEWS,
    REQUEST_DELAY,
    REPORTS_DIR, ENABLE_DASHBOARD, ENABLE_GITHUB_PAGES_LINK, GITHUB_PAGES_BASE_URL,
    TV_RATIO_WATCH_MIN, TV_RATIO_P2P3_MIN,
    ENABLE_SECTOR_FETCH, SECTOR_TOP_N,
    ENABLE_NXT_FETCH,
    MARKET_REGIME_BULL_ADL, MARKET_REGIME_BEAR_ADL, MARKET_REGIME_BULL_TV1500,
    CANDIDATES_MAX_BULL, CANDIDATES_MAX_NEUTRAL, CANDIDATES_MAX_BEAR, CANDIDATES_MAX_CONCENTRATED_BEAR,
    KH_CRAWL_MIN_TV_EOK,
)
from scripts.market_calendar import get_now_kst, is_trading_day, get_run_type
from scripts.storage import save_raw, save_processed, save_signals
from scripts import fetch_market_data
from scripts.fetch_stock_data import fetch_chart_data
from scripts.fetch_supply_data import fetch_supply
from scripts.fetch_news import fetch_news
from scripts.indicators import (
    is_big_candle, is_first_big_candle, is_ma_cluster,
    is_volume_peak, is_trading_value_peak, calc_all_ma,
    calc_52w_high,
)
from scripts.pattern_detector import detect_patterns
from scripts import ranking as rnk
from scripts.ranking import filter_excluded_stocks
from scripts.models import ProcessedData, SupplyData, NewsData
from scripts.scoring import calc_score, build_checklist
from scripts import notifier as ntf
from scripts.dashboard import generate_dashboard_html, build_dashboard_links, generate_index_html

def _build_recent_base_pool(
    signals_dir: Path,
    run_date: str,
    filtered_df: pd.DataFrame,
    exclude_codes: set,
    obs_min_tv_won: float,
    lookback_dates: int = 5,
) -> dict:
    """
    과거 signals.csv(당일돌파형)에서 기준봉 발생 종목을 추출해 오늘 관찰 후보 {code: base_date} 반환.
    run_date 이전 최대 lookback_dates개 거래일 파일 탐색.
    obs_min_tv_won 미만 거래대금, 상한가(≥29.5%), exclude_codes는 제외.
    """
    import re as _re_rbp

    code_to_date: dict[str, str] = {}
    seen_dates: set[str] = set()

    for f in sorted(signals_dir.glob("*_signals.csv"), reverse=True):
        m = _re_rbp.match(r"^(\d{4}-\d{2}-\d{2})_\d{4}_signals\.csv$", f.name)
        if not m:
            continue
        file_date = m.group(1)
        if file_date >= run_date:
            continue
        if file_date not in seen_dates and len(seen_dates) >= lookback_dates:
            break
        seen_dates.add(file_date)
        try:
            df = pd.read_csv(f, dtype={"종목코드": str})
            if "pattern_type_label" not in df.columns or "종목코드" not in df.columns:
                continue
            mask = df["pattern_type_label"] == "당일돌파형"
            for code in df.loc[mask, "종목코드"].dropna().astype(str):
                if code not in code_to_date:
                    code_to_date[code] = file_date
        except Exception:
            continue

    if not code_to_date:
        return {}

    tv_map_loc  = filtered_df.set_index("종목코드")["거래대금"].to_dict()
    chg_map_loc = filtered_df.set_index("종목코드")["등락률"].to_dict()

    result = {}
    for code, base_date in code_to_date.items():
        if code in exclude_codes:
            continue
        if float(tv_map_loc.get(code, 0)) < obs_min_tv_won:
            continue
        if float(chg_map_loc.get(code, 0)) >= 29.5:
            continue
        if filtered_df[filtered_df["종목코드"] == code].empty:
            continue
        result[code] = base_date

    return result


def _save_obs_pool(obs_candidates: list, report_date: str, reports_dir: Path) -> None:
    """관찰 풀 후보를 CSV로 저장 (reports/recent_base_pool_YYYY-MM-DD.csv)."""
    rows = []
    for c in obs_candidates:
        rows.append({
            "종목명":                       c["name"],
            "종목코드":                     c["code"],
            "시장":                         c["market"],
            "등락률":                       c["change_pct"],
            "거래대금":                     c["trading_value"],
            "signal_price":                c["signal_price"],
            "sector":                      c.get("sector", ""),
            "source_pool":                 "recent_base_pool",
            "observation_only":            True,
            "pattern_type_label":          c.get("pattern_type_label", "없음"),
            "is_htc_candidate":        c.get("is_htc_candidate", False),
            "is_high_range_candidate": c.get("is_high_range_candidate", False),
            "kim_hyungjun_flag":       c.get("kim_hyungjun_flag", False),
            "base_candle_date":            c.get("base_candle_date"),
            "base_candle_offset":          c.get("base_candle_offset"),
            "today_tv_ratio":              c.get("today_tv_ratio"),
            "close_from_base_high_pct":    c.get("close_from_base_high_pct"),
            "above_ma5":                   c.get("above_ma5"),
            "supply_label":                c.get("supply_label", ""),
            "note":                        "최근 기준봉 이후 관찰 후보 (매수 신호 아님)",
        })
    if not rows:
        return
    import pandas as _pd_obs
    df = _pd_obs.DataFrame(rows)
    path = reports_dir / f"recent_base_pool_{report_date}.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")


def _calc_kh_supply_ok(supply) -> bool | None:
    """KH 수급 조건: 기관 당일 또는 5일 누적 순매수. supply 없으면 None."""
    if supply is None:
        return None
    if not hasattr(supply, "status") or supply.status != "ok":
        return None
    inst_1d = (supply.institution_net    or 0) > 0
    inst_5d = (supply.institution_net_5d or 0) > 0
    frgn_1d = (supply.foreign_net        or 0) > 0
    return inst_1d or inst_5d or (inst_1d and frgn_1d)


def _setup_logging(timestamp_str: str):
    date_str = timestamp_str.split("_")[0]
    log_file = LOG_DIR / f"{date_str}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )



def _short_sector(name: str) -> str:
    return name.split("와")[0] if "와" in name else name


def _calc_market_type(gainers_records: list, market_regime: str,
                      leading_sectors: list | None = None) -> str:
    """
    장세 유형 텍스트.
    판단 우선순위: 섹터 거래대금 비중(TV%) > 상승률 Top20 개수.
    정보 출력 목적 — 자동 판단 아님.
    """
    # 1. 거래대금 비중 기반 (우선)
    if leading_sectors:
        top   = leading_sectors[0]
        ratio = top.get("market_ratio_pct") or 0
        short = _short_sector(top.get("sector_name", ""))
        if ratio >= 25:
            if len(leading_sectors) >= 2:
                r2 = leading_sectors[1].get("market_ratio_pct") or 0
                s2 = _short_sector(leading_sectors[1].get("sector_name", ""))
                if r2 >= 10:
                    return f"섹터집중 ({short} {ratio:.1f}% · {s2} {r2:.1f}%)"
            return f"섹터집중 ({short} {ratio:.1f}%)"
        if ratio >= 10:
            return f"약섹터집중 ({short} {ratio:.1f}%)"

    # 2. 상승률 Top20 개수 기반 (폴백)
    from collections import Counter
    sectors = [r.get("sector", "") for r in gainers_records if r.get("sector")]
    if not sectors:
        return ""
    total     = len(gainers_records)
    counter   = Counter(sectors)
    top_items = counter.most_common(3)
    top_sector, top_count = top_items[0]
    top_ratio = top_count / total
    if top_ratio >= 0.30:
        if len(top_items) >= 2 and top_items[1][1] >= 3:
            s2, n2 = top_items[1]
            return f"테마주 장세 ({top_sector} {top_count}개·{s2} {n2}개)"
        return f"테마주 장세 ({top_sector} {top_count}개)"
    if top_ratio >= 0.20:
        return f"약테마 ({top_sector} {top_count}개 중심)"
    if market_regime == "강세":
        return "수급 장세 (전 섹터 분산)"
    return "혼조 (특정 주도 없음)"


def _calc_market_regime(all_df: pd.DataFrame, tv_1500_count: int) -> tuple[str, float]:
    """전종목 데이터 기반 시장 수용성 판단 (ADL + 1500억 종목 수).
    강세: ADL > 0.55 AND 1500억↑ >= 3
    약세: ADL < 0.40
    중립: 그 외
    반환: (regime, adl) — adl은 상승종목 비율 0~1.
    """
    try:
        chg = all_df["등락률"].dropna()
        total = int((chg != 0).sum())
        if total == 0:
            return "중립", 0.0
        adl = float((chg > 0).sum()) / float(total)
        if adl > MARKET_REGIME_BULL_ADL and tv_1500_count >= MARKET_REGIME_BULL_TV1500:
            return "강세", adl
        if adl < MARKET_REGIME_BEAR_ADL:
            return "약세", adl
        return "중립", adl
    except Exception as e:
        logging.getLogger(__name__).warning(f"시장 상태 판단 실패: {e}")
        return "중립", 0.0


def _calc_market_subtype(market_regime: str, kospi_chg: float | None) -> str:
    """약세 시 세부 장세 유형 판단.
    자금집중형: 지수 강세지만 ADL 약세 → 대형주/주도섹터에 자금 쏠림
    전체하락형: 지수도 하락, ADL도 약세 → 전반적 위험
    혼조형: 그 외
    강세/중립은 빈 문자열 반환.
    """
    if market_regime != "약세" or kospi_chg is None:
        return ""
    if kospi_chg >= 1.5:
        return "자금집중형"
    if kospi_chg <= -1.0:
        return "전체하락형"
    return "혼조형"


def _enrich_candidates(codes: list[str], all_df: pd.DataFrame, run_type: str) -> dict:
    """
    상위 후보 종목에 대해 히스토리, 지표, 패턴, 수급, 뉴스 수집.
    반환: {code: {indicators, patterns, supply, news, regular_close_price}}
    """
    enriched = {}

    for code in codes:
        enr = {"indicators": {}, "patterns": {}, "supply": SupplyData(code=code), "news": NewsData(code=code), "regular_close_price": None}
        row = all_df[all_df["종목코드"] == code]
        if row.empty:
            enriched[code] = enr
            continue

        row = row.iloc[0]
        tv  = float(row.get("거래대금", 0))

        # 일별 히스토리 수집 (최소 100일 → MA60/60일 최고값 계산 가능)
        daily_df = fetch_chart_data(code)
        time.sleep(0.2)

        if not daily_df.empty:
            daily_df_ma = calc_all_ma(daily_df)
            row0 = daily_df_ma.iloc[0]

            # 전종목 API는 현재가/등락률만 제공 → 시가를 역산해서 캔들 판단
            _chg   = float(row.get("등락률", 0))
            _close = float(row.get("현재가", 0))
            _open  = _close / (1 + _chg / 100) if _chg != -100 else _close

            # daily_df.iloc[0]에 실제 OHLCV가 있으면 사용 (2차/수동: 종가 확정)
            _d0_high = float(row0.get("high", 0) or 0)
            _d0_low  = float(row0.get("low",  0) or 0)
            _d0_open = float(row0.get("open", 0) or 0)
            today_high = _d0_high if _d0_high > 0 else _close
            today_low  = _d0_low  if _d0_low  > 0 else _close
            today_open = _d0_open if _d0_open > 0 else _open

            bc = is_big_candle(
                open_=today_open,
                high=today_high,
                low=today_low,
                close=_close,
                change_pct=_chg,
                trading_value=tv,
            )
            fbc  = is_first_big_candle(daily_df, today_idx=0)
            mac  = is_ma_cluster(
                ma5=row0.get("ma5", 0), ma10=row0.get("ma10", 0),
                ma20=row0.get("ma20", 0), ma60=row0.get("ma60"),
            )
            vpk  = is_volume_peak(daily_df, today_idx=0)
            tvpk = is_trading_value_peak(daily_df, today_idx=0, today_tv=tv)

            # 52주 신고가 (#11)
            _52w = calc_52w_high(daily_df, today_close=_close, today_idx=0)

            # 오늘 장대양봉(big_candle=True) AND 최근 60일 내 장대양봉 없음(first_big_candle=True)
            first_bc_flag = bc.get("big_candle", False) and fbc.get("first_big_candle", False)

            # ProcessedData 모델로 저장
            processed = ProcessedData(
                code=code,
                ma5=row0.get("ma5"),  ma10=row0.get("ma10"),
                ma20=row0.get("ma20"), ma60=row0.get("ma60"),
                ma_cluster_flag=       mac["cluster"],
                volume_peak_60d=       vpk,
                trading_value_peak_60d=tvpk,
                candle_body_ratio=     bc.get("body_ratio", 0.0),
                upper_shadow_ratio=    bc.get("upper_tail_ratio", 0.0),
                big_candle_flag=       bc.get("big_candle", False),
                loose_big_candle_flag= bc.get("loose_big_candle", False),
                first_big_candle_flag= first_bc_flag,
                data_ok=               fbc.get("data_ok", False),
                high_52w=              _52w.get("high_52w", 0.0),
                near_high_52w=         _52w.get("near_high_52w", False),
            )
            enr["indicators"] = {
                **bc, **fbc,
                "ma_cluster":    mac["cluster"],
                "ma_details":    mac,
                "vol_peak":      vpk,
                "tv_peak":       tvpk,
                "high_52w":      _52w.get("high_52w", 0.0),
                "near_high_52w": _52w.get("near_high_52w", False),
            }
            enr["processed"] = processed

            # regular_close_price: 2차/수동 실행 시 정규장 종가 저장 (NXT 제외)
            if run_type in ("2차", "수동"):
                try:
                    _rc = float(daily_df.iloc[0].get("close", 0) or 0)
                    enr["regular_close_price"] = _rc if _rc > 0 else None
                except (TypeError, ValueError):
                    pass

            pat = detect_patterns(
                code=code,
                today_open=today_open,
                today_high=today_high,
                today_low=today_low,
                today_close=_close,
                today_change_pct=_chg,
                today_tv=tv,
                daily_df=daily_df,
                near_high_52w=processed.near_high_52w,
            )
            enr["patterns"] = pat

        # 수급 (주수 × KRX 종가 → 원화 변환)
        # daily_df 종가 우선 사용 — NXT merge로 현재가가 덮어써진 경우 오차 방지
        if ENABLE_SUPPLY_FETCH:
            try:
                sup = fetch_supply(code)
                _price = (
                    float(daily_df.iloc[0]["close"])
                    if not daily_df.empty
                    else float(row.get("현재가", 0))
                )
                if sup.status == "ok" and _price > 0:
                    if sup.institution_net is not None:
                        sup.institution_net = sup.institution_net * _price
                    if sup.foreign_net is not None:
                        sup.foreign_net = sup.foreign_net * _price
                    if sup.institution_net_5d is not None:
                        sup.institution_net_5d = sup.institution_net_5d * _price
                    if sup.foreign_net_5d is not None:
                        sup.foreign_net_5d = sup.foreign_net_5d * _price
                enr["supply"] = sup
                time.sleep(REQUEST_DELAY)
            except Exception as e:
                logging.getLogger(__name__).warning(f"[{code}] 수급 예외: {e}")

        # 뉴스 → NewsData 모델
        news_obj = NewsData(code=code)
        if ENABLE_NEWS_FETCH:
            try:
                news_obj = fetch_news(code)
                time.sleep(REQUEST_DELAY)
            except Exception as e:
                logging.getLogger(__name__).warning(f"[{code}] 뉴스 예외: {e}")
        enr["news"] = news_obj

        enriched[code] = enr

    return enriched


def run(preview: bool = False):
    if preview:
        ntf.set_preview_mode(True)
    now = get_now_kst()
    timestamp_str = now.strftime("%Y-%m-%d_%H%M")
    _setup_logging(timestamp_str)
    logger = logging.getLogger(__name__)
    run_time = now.strftime("%Y-%m-%d %H:%M")
    run_type = get_run_type(now)

    logger.info(f"=== 파이프라인 시작: {run_time} KST ({run_type}) ===")

    # ── 비거래일 체크 ────────────────────────────────────────
    import os as _os
    _force = _os.getenv("FORCE_RUN", "").lower() in ("1", "true", "yes")
    if not _force and not is_trading_day(now.date()):
        msg = (
            f"<b>[종가베팅 봇]</b>\n"
            f"{run_time} KST\n"
            f"오늘은 비거래일(주말/공휴일)입니다. 수집을 건너뜁니다."
        )
        ntf.send_message(msg)
        logger.info("비거래일 → 종료")
        return

    # ── 1. 전 종목 수집 ──────────────────────────────────────
    logger.info("전 종목 데이터 수집 시작...")
    raw_data = fetch_market_data.run()
    index_levels = fetch_market_data.fetch_index_levels()
    logger.info(f"지수 수준: KOSPI={index_levels.get('kospi_level')} KOSDAQ={index_levels.get('kosdaq_level')}")

    if not raw_data:
        ntf.send_message(f"<b>[오류]</b> {run_time} KST\n데이터 수집 실패")
        logger.error("수집 실패 → 종료")
        return

    # raw 저장
    for market_name, df in raw_data.items():
        save_raw(df, market_name, timestamp_str)

    # 전체 합치기
    all_df = pd.concat(raw_data.values(), ignore_index=True)

    # ── 1-1. NXT 데이터 합산 (2차/수동 실행 시) ─────────────────
    if run_type in ("2차", "수동") and ENABLE_NXT_FETCH:
        try:
            from scripts.fetch_nxt_data import fetch_nxt_quant, merge_nxt_into_df
            logger.info("NXT 거래상위 수집 시작...")
            nxt_dict = fetch_nxt_quant()
            if nxt_dict:
                all_df = merge_nxt_into_df(all_df, nxt_dict)
            else:
                logger.warning("NXT 수집 결과 없음 — KRX 데이터만 사용")
        except Exception as e:
            logger.warning(f"NXT 수집 실패 (무시, KRX만 사용): {e}")

    # ── 2. 제외 필터 + 1차 가격 필터 (raw 저장 이후 적용) ──────
    filtered_df = filter_excluded_stocks(all_df)
    filtered_df = rnk.apply_price_filter(filtered_df)

    # ── 3. 랭킹 계산 ────────────────────────────────────────
    market_totals = rnk.calc_market_total(
        raw_data.get("KOSPI", pd.DataFrame()),
        raw_data.get("KOSDAQ", pd.DataFrame()),
    )
    gainers      = rnk.get_top_gainers(filtered_df)
    top_tv       = rnk.get_top_trading_value(filtered_df)
    intersection = rnk.get_intersection(gainers, top_tv)

    # processed 저장
    save_processed(gainers,      "top_gainers",  timestamp_str)
    save_processed(top_tv,       "top_tv",        timestamp_str)
    save_processed(intersection, "intersection",  timestamp_str)

    logger.info(
        f"랭킹 계산 완료 - 상승률Top{len(gainers)} / "
        f"거래대금Top{len(top_tv)} / 교집합{len(intersection)}"
    )

    # ── 4. 섹터 데이터 수집 ──────────────────────────────────────
    sector_result: dict = {"overview": pd.DataFrame(), "top_sectors": [], "code_to_sector": {}}
    if ENABLE_SECTOR_FETCH:
        try:
            from scripts import fetch_sector_data as _fsd
            sector_result = _fsd.run(top_n=SECTOR_TOP_N)
        except Exception as e:
            logger.warning(f"섹터 수집 실패 (무시): {e}")

    code_to_sector: dict = sector_result.get("code_to_sector", {})

    # 주도섹터별 top stocks 구성 (filtered_df에서 구성종목 필터 + 거래대금 기준 정렬)
    _total_market_tv_eok = (
        market_totals.get("kospi_total_tv_eok", 0) + market_totals.get("kosdaq_total_tv_eok", 0)
    )
    _min_tv_won_sec  = MIN_TRADING_VALUE_EOK * 100_000_000
    _gainer_codes    = set(gainers["종목코드"].astype(str)) if not gainers.empty else set()
    _tv20_codes      = set(top_tv["종목코드"].astype(str))  if not top_tv.empty  else set()
    leading_sectors = []
    for sec in sector_result.get("top_sectors", []):
        sec_codes = set(sec.get("stock_codes", []))
        if not sec_codes or filtered_df.empty:
            continue
        sec_df = filtered_df[filtered_df["종목코드"].isin(sec_codes)].copy()
        if sec_df.empty:
            continue
        top_stocks = (
            sec_df.nlargest(5, "거래대금")
            [["종목명", "종목코드", "현재가", "등락률", "거래대금"]]
            .to_dict("records")
        )
        pos_df = sec_df[sec_df["등락률"] > 0]
        avg_chg = float(pos_df["등락률"].mean()) if not pos_df.empty else 0.0
        sec_tv_eok = round(float(sec_df["거래대금"].sum()) / 1e8, 0)
        market_ratio_pct = round(sec_tv_eok / _total_market_tv_eok * 100, 1) if _total_market_tv_eok > 0 else None
        sec_codes_str = set(sec_df["종목코드"].astype(str))
        leading_sectors.append({
            "sector_name":        sec["sector_name"],
            "change_pct":         avg_chg,
            "tv_eok":             sec_tv_eok,
            "market_ratio_pct":   market_ratio_pct,
            "top_stocks":         top_stocks,
            "tv1500_count":       int((sec_df["거래대금"] >= _min_tv_won_sec).sum()),
            "gainer_top20_count": sum(1 for c in sec_codes_str if c in _gainer_codes),
            "tv_top20_count":     sum(1 for c in sec_codes_str if c in _tv20_codes),
        })

    # gainers_top20, trading_value_top20에 sector 태그 추가
    def _add_sector(records: list) -> list:
        for r in records:
            r["sector"] = code_to_sector.get(str(r.get("종목코드", "")), "")
        return records

    gainers_top20_records = _add_sector(gainers.to_dict("records") if not gainers.empty else [])
    tv_top20_records      = _add_sector(top_tv.to_dict("records")  if not top_tv.empty  else [])

    # ── 6. report_data 기본 구조 (1차/2차 공통) ──────────────
    report_date   = now.strftime("%Y-%m-%d")
    snapshot_time = {"1차": "1450", "2차": "1750"}.get(run_type, timestamp_str.split("_")[1])

    _min_tv_won = MIN_TRADING_VALUE_EOK * 100_000_000
    tv_1500_count = int((filtered_df["거래대금"] >= _min_tv_won).sum()) if not filtered_df.empty else 0
    gainers_tv_1500_count = int((gainers["거래대금"] >= _min_tv_won).sum()) if not gainers.empty else 0

    # 상한가 집계 (#1/#18): 등락률 29.5% 이상
    _limit_up_df = filtered_df[filtered_df["등락률"] >= 29.5] if not filtered_df.empty else pd.DataFrame()
    limit_up_count = len(_limit_up_df)
    _limit_up_top = _limit_up_df.nlargest(10, "거래대금") if not _limit_up_df.empty else pd.DataFrame()
    limit_up_list = (
        _limit_up_top[["종목명", "종목코드", "시장", "등락률", "거래대금"]].to_dict("records")
        if not _limit_up_top.empty else []
    )
    limit_up_list = _add_sector(limit_up_list)
    limit_up_names = [r["종목명"] for r in limit_up_list[:5]]

    market_regime, _market_adl = _calc_market_regime(all_df, tv_1500_count)
    market_type    = _calc_market_type(gainers_top20_records, market_regime, leading_sectors)
    _kospi_chg     = index_levels.get("kospi_chg")
    market_subtype = _calc_market_subtype(market_regime, _kospi_chg)
    logger.info(f"시장 상태: {market_regime}{' · ' + market_subtype if market_subtype else ''} | 장세: {market_type}")

    # ── 전일 복기 ───────────────────────────────────────────────────────────
    review_results = []
    try:
        from scripts import review as _review
        review_results = _review.run(now.date(), _kospi_chg)
    except Exception as e:
        logger.warning(f"복기 실패 (무시): {e}")

    cumulative_stats = {}
    try:
        from scripts import stats as _stats
        cumulative_stats = _stats.run()
    except Exception as e:
        logger.warning(f"누적 통계 실패 (무시): {e}")

    report_data = {
        "metadata": {
            "date":          report_date,
            "snapshot_time": snapshot_time,
            "run_time":      run_time,
            "run_type":      run_type,
        },
        "market_summary": {
            "kospi_tv_eok":           market_totals.get("kospi_total_tv_eok",  0),
            "kosdaq_tv_eok":          market_totals.get("kosdaq_total_tv_eok", 0),
            "tv_1500_count":          tv_1500_count,
            "gainers_tv_1500_count":  gainers_tv_1500_count,
            "gainers_count":          len(gainers),
            "tv_count":               len(top_tv),
            "intersection_count":     len(intersection) if not intersection.empty else 0,
            "core_count":             0,
            "market_regime":          market_regime,
            "market_adl":             _market_adl,
            "market_subtype":         market_subtype,
            "market_type":            market_type,
            "kospi_level":            index_levels.get("kospi_level"),
            "kosdaq_level":           index_levels.get("kosdaq_level"),
            "kospi_chg":              _kospi_chg,
            "limit_up_count":         limit_up_count,
            "limit_up_list":          limit_up_list,
        },
        "gainers_top20":          gainers_top20_records,
        "trading_value_top20":    tv_top20_records,
        "intersection_candidates": intersection.to_dict("records") if not intersection.empty else [],
        "core_candidates":        [],
        "rejected_candidates":    [],
        "leading_sectors":        leading_sectors,
        "sector_calendar":        {},
        "review_results":         review_results,
        "cumulative_stats":       cumulative_stats,
    }

    # 섹터 캘린더 업데이트 (2차/수동에서만 확정 데이터로 기록)
    if leading_sectors:
        import json as _json
        _cal_path = REPORTS_DIR / "sector_calendar.json"
        _cal: dict = {}
        if _cal_path.exists():
            try:
                _cal = _json.loads(_cal_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        _cal[report_date] = [s["sector_name"] for s in leading_sectors[:4]]
        try:
            _cal_path.write_text(_json.dumps(_cal, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"sector_calendar.json 저장 실패: {e}")
        report_data["sector_calendar"] = _cal

    # ── 7. 전체 후보 분석 (1차/2차/수동 공통) ───────────────────
    # 1차(14:50): 장중 데이터 기준 → 종가 매수 후보 압축
    # 2차(17:50): 종가 확정 후 → 추가 진입 / 다음날 선행 후보 탐색
    top20_gainers = filtered_df[filtered_df["등락률"] > 0].nlargest(20, "등락률")
    top20_tv      = filtered_df.nlargest(20, "거래대금")
    candidate_codes = list(set(
        list(top20_gainers["종목코드"].dropna()) +
        list(top20_tv["종목코드"].dropna())
    ))

    key_candidates = []
    rejected_list  = []
    MIN_TV_WON            = MIN_TRADING_VALUE_EOK * 100_000_000
    inter_codes           = set(intersection["종목코드"].dropna() if not intersection.empty else [])
    leading_sector_names  = {s["sector_name"] for s in leading_sectors}

    # ── TV / 상한가 1차 필터: 크롤링 전에 적용해 불필요한 수집 방지 ─────
    tv_map  = filtered_df.set_index("종목코드")["거래대금"].to_dict()
    chg_map = filtered_df.set_index("종목코드")["등락률"].to_dict()
    crawl_codes = []
    for code in candidate_codes:
        tv  = float(tv_map.get(code, 0))
        chg = float(chg_map.get(code, 0))
        row = filtered_df[filtered_df["종목코드"] == code]
        name = row.iloc[0].get("종목명", "") if not row.empty else ""
        if chg >= 29.5:
            rejected_list.append({"code": code, "name": name,
                                   "reason": "상한가 (진입 불가)",
                                   "trading_value": tv, "change_pct": chg})
        elif tv < MIN_TV_WON:
            rejected_list.append({"code": code, "name": name,
                                   "reason": f"거래대금 부족 ({tv/1e8:.0f}억)",
                                   "trading_value": tv, "change_pct": chg})
        else:
            crawl_codes.append(code)

    logger.info(f"후보 종목 {len(crawl_codes)}개 지표 수집 시작 (TV필터 후, 원본 {len(candidate_codes)}개) [{run_type}]...")
    enriched = _enrich_candidates(crawl_codes, filtered_df, run_type)

    # ── KH 전용 추가 크롤링 (B안: Top40 중 TV≥300억 + 상한가 아닌 + crawl_codes 제외) ──
    _KH_MIN_TV_WON = KH_CRAWL_MIN_TV_EOK * 100_000_000
    _crawl_code_set = set(crawl_codes)
    kh_extra_codes = [
        code for code in candidate_codes
        if code not in _crawl_code_set
        and float(chg_map.get(code, 0)) < 29.5
        and float(tv_map.get(code, 0)) >= _KH_MIN_TV_WON
    ]
    kh_extra_enriched: dict = {}
    if kh_extra_codes:
        logger.info(f"KH 전용 추가 수집: {len(kh_extra_codes)}개 (TV≥{KH_CRAWL_MIN_TV_EOK}억)")
        kh_extra_enriched = _enrich_candidates(kh_extra_codes, filtered_df, run_type)

    # ── 프로그램 수급 (2차/수동: 장후 확정치) ───────────────────────
    prog_data: dict = {}
    if run_type != "1차":
        try:
            from scripts.fetch_program_data import fetch_program_data
            prog_data = fetch_program_data(report_date.replace("-", ""))
        except Exception as e:
            logger.warning(f"프로그램 수급 수집 실패 (무시): {e}")

    for code in crawl_codes:
        row = filtered_df[filtered_df["종목코드"] == code]
        if row.empty:
            continue
        row  = row.iloc[0]
        tv   = float(row.get("거래대금", 0))
        name = row.get("종목명", "")

        enr       = enriched.get(code, {})
        processed = enr.get("processed", ProcessedData(code=code))
        supply    = enr.get("supply",    SupplyData(code=code))
        news      = enr.get("news",      NewsData(code=code))
        pat       = enr.get("patterns",  {})

        in_inter      = code in inter_codes
        has_pattern   = pat.get("pattern_summary", "없음") != "없음"
        struct_broken = pat.get("structure_broken_flag", False)
        tv_ratio      = pat.get("tv_ratio")

        # 구조 붕괴 제외
        if struct_broken:
            rejected_list.append({"code": code, "name": name, "reason": "구조 붕괴",
                                   "trading_value": tv, "change_pct": float(row.get("등락률", 0))})
            continue

        # 거래대금 급감 제외 (패턴 타입별 임계값 분리)
        # 당일돌파형: 강한 거래대금 지속 필요 → 0.2 유지
        # 고가횡보형/고가수축형: 적은 거래대금 = 건강한 물량 소화 → 0.05로 완화
        _pattern_label = pat.get("pattern_type_label", "없음")
        _tv_min = TV_RATIO_WATCH_MIN if _pattern_label == "당일돌파형" else TV_RATIO_P2P3_MIN
        if tv_ratio is not None and tv_ratio < _tv_min:
            rejected_list.append({"code": code, "name": name,
                                   "reason": f"거래대금 급감 (ratio {tv_ratio:.2f})",
                                   "trading_value": tv, "change_pct": float(row.get("등락률", 0))})
            continue

        # 교집합 또는 패턴 조건
        if not in_inter and not has_pattern:
            rejected_list.append({"code": code, "name": name, "reason": "패턴 없음 + 교집합 아님",
                                   "trading_value": tv, "change_pct": float(row.get("등락률", 0))})
            continue

        checklist = build_checklist(code, tv, processed, supply)
        score     = calc_score(code=code, trading_value=tv, processed=processed,
                               supply=supply, news=news, in_intersection=in_inter,
                               patterns=pat)
        supply_ok = checklist.supply_ok

        _sector = code_to_sector.get(code, "")
        _regular_close = enr.get("regular_close_price")
        _signal_px     = float(row.get("현재가", 0))
        _entry_ref     = _regular_close if _regular_close else _signal_px
        if _regular_close:
            _price_src = "regular_close_price"
        elif run_type in ("2차", "수동"):
            _price_src = "signal_price"
        else:
            _price_src = "signal_price (장중)"
        key_candidates.append({
            "name":             name,
            "code":             code,
            "market":           row.get("시장", ""),
            "change_pct":       float(row.get("등락률", 0)),
            "trading_value":    tv,
            "signal_price":     float(row.get("현재가", 0)),
            "indicators":       enr.get("indicators", {}),
            "patterns":         pat,
            "supply":           supply,
            "news":             news,
            "score":            score,
            "checklist":        checklist,
            "in_inter":         in_inter,
            "has_pattern":      has_pattern,
            "supply_ok":        supply_ok,
            "near_high_52w":    processed.near_high_52w,
            "sector":           _sector,
            "is_leading_sector":             bool(_sector) and _sector in leading_sector_names,
            "prog_net_eok":                  prog_data.get(code),
            "regular_close_price":           _regular_close,
            "regular_close_price_available": bool(_regular_close),
            "entry_reference_price":         _entry_ref,
            "price_source":                  _price_src,
        })

    # 정렬: 교집합 > 패턴타입 > score > supply_ok > 거래대금 > 상승률
    _PATTERN_TYPE_ORDER = {"당일돌파형": 0, "고가수축형": 1, "고가횡보형": 2, "없음": 3}

    def _priority(item):
        pat        = item.get("patterns", {})
        sc         = item.get("score")
        type_order = _PATTERN_TYPE_ORDER.get(pat.get("pattern_type_label", "없음"), 3)
        total      = sc.total_score if sc else 0
        return (
            not item["in_inter"],
            type_order,
            -total,
            not item["supply_ok"],
            -item["trading_value"],
            -item["change_pct"],
        )

    # ── KH supply_ok 추가 (crawl_codes 후보 — obs 편입 전) ────────────
    for c in key_candidates:
        pat = c.get("patterns", {})
        if pat.get("kim_hyungjun_flag"):
            kh_sup = _calc_kh_supply_ok(c.get("supply"))
            pat["kim_hyungjun_supply_ok"] = kh_sup
            c["kim_hyungjun_supply_ok"]   = kh_sup
        else:
            c["kim_hyungjun_supply_ok"] = None

    # ── KH 전용 후보 수집 (kh_extra_codes 중 KH 조건 충족) ─────────
    _key_codes = {c["code"] for c in key_candidates}
    kh_only_candidates: list[dict] = []
    for _kh_code in kh_extra_codes:
        _kh_enr = kh_extra_enriched.get(_kh_code, {})
        _kh_pat = _kh_enr.get("patterns", {})
        if not _kh_pat.get("kim_hyungjun_flag", False):
            continue
        _kh_row = filtered_df[filtered_df["종목코드"] == _kh_code]
        if _kh_row.empty:
            continue
        _kh_row_data = _kh_row.iloc[0]
        _kh_tv       = float(_kh_row_data.get("거래대금", 0))
        _kh_supply   = _kh_enr.get("supply", SupplyData(code=_kh_code))
        _kh_sup_ok   = _calc_kh_supply_ok(_kh_supply)
        _kh_pat["kim_hyungjun_supply_ok"] = _kh_sup_ok
        kh_only_candidates.append({
            "name":                   _kh_row_data.get("종목명", ""),
            "code":                   _kh_code,
            "market":                 _kh_row_data.get("시장", ""),
            "change_pct":             float(_kh_row_data.get("등락률", 0)),
            "trading_value":          _kh_tv,
            "signal_price":           float(_kh_row_data.get("현재가", 0)),
            "patterns":               _kh_pat,
            "supply":                 _kh_supply,
            "news":                   _kh_enr.get("news", NewsData(code=_kh_code)),
            "in_inter":               _kh_code in inter_codes,
            "sector":                 code_to_sector.get(_kh_code, ""),
            "kim_hyungjun_supply_ok": _kh_sup_ok,
        })
    if kh_only_candidates:
        logger.info(f"KH 전용 후보: {len(kh_only_candidates)}개")

    # ── recent_base_pool 관찰 풀 (2차/수동 실행 시) ─────────────────
    obs_candidates: list[dict] = []
    if run_type != "1차":
        _all_crawled = set(crawl_codes) | set(kh_extra_codes)
        _OBS_MIN_TV_WON = KH_CRAWL_MIN_TV_EOK * 100_000_000
        _obs_code_map = _build_recent_base_pool(
            signals_dir=SIGNALS_DIR,
            run_date=report_date,
            filtered_df=filtered_df,
            exclude_codes=_all_crawled,
            obs_min_tv_won=_OBS_MIN_TV_WON,
        )
        if _obs_code_map:
            logger.info(f"recent_base_pool 관찰 후보 크롤링: {len(_obs_code_map)}개...")
            _obs_enriched = _enrich_candidates(list(_obs_code_map.keys()), filtered_df, run_type)
            for _obs_code, _base_date in _obs_code_map.items():
                _obs_row = filtered_df[filtered_df["종목코드"] == _obs_code]
                if _obs_row.empty:
                    continue
                _obs_row_data = _obs_row.iloc[0]
                _obs_enr = _obs_enriched.get(_obs_code, {})
                _obs_pat = _obs_enr.get("patterns", {})

                if _obs_pat.get("structure_broken_flag"):
                    continue
                _base_idx = (_obs_pat.get("details") or {}).get("base_idx")
                if (_base_idx is None or _base_idx < 1) and not _obs_pat.get("kim_hyungjun_flag"):
                    continue

                obs_candidates.append({
                    "name":                        _obs_row_data.get("종목명", ""),
                    "code":                        _obs_code,
                    "market":                      _obs_row_data.get("시장", ""),
                    "change_pct":                  float(_obs_row_data.get("등락률", 0)),
                    "trading_value":               float(_obs_row_data.get("거래대금", 0)),
                    "signal_price":                float(_obs_row_data.get("현재가", 0)),
                    "sector":                      code_to_sector.get(_obs_code, ""),
                    "source_pool":                 "recent_base_pool",
                    "observation_only":            True,
                    "pattern_type_label":          _obs_pat.get("pattern_type_label", "없음"),
                    "is_htc_candidate":            bool(_obs_pat.get("high_tight_consolidation_flag")),
                    "is_high_range_candidate":     bool(_obs_pat.get("pattern3")),
                    "kim_hyungjun_flag":           bool(_obs_pat.get("kim_hyungjun_flag")),
                    "base_candle_date":            _base_date,
                    "base_candle_offset":          _obs_pat.get("base_candle_day_offset"),
                    "today_tv_ratio":              _obs_pat.get("tv_ratio"),
                    "close_from_base_high_pct":    _obs_pat.get("base_high_gap_pct"),
                    "above_ma5":                   _obs_pat.get("kim_hyungjun_above_ma5"),
                    "supply_label":                getattr(_obs_enr.get("supply"), "supply_label", "") or "",
                    "patterns":                    _obs_pat,
                    "supply":                      _obs_enr.get("supply"),
                })
            logger.info(f"recent_base_pool 관찰 후보 최종: {len(obs_candidates)}개"
                        f" (HTC={sum(c['is_htc_candidate'] for c in obs_candidates)}"
                        f" 횡보={sum(c['is_high_range_candidate'] for c in obs_candidates)}"
                        f" KH={sum(c['kim_hyungjun_flag'] for c in obs_candidates)})")
            if obs_candidates:
                try:
                    _save_obs_pool(obs_candidates, report_date, REPORTS_DIR)
                except Exception as e:
                    logger.warning(f"관찰 풀 CSV 저장 실패 (무시): {e}")
        else:
            logger.info("recent_base_pool: 과거 신호 데이터 없음 또는 조건 미충족 (정상)")

    # ── obs_candidates → key_candidates 편입 (패턴 통과 종목) ──────────
    _obs_remaining: list[dict] = []
    for _obs_c in obs_candidates:
        _obs_code      = _obs_c["code"]
        _obs_tv        = _obs_c["trading_value"]
        _obs_pat       = _obs_c.get("patterns", {})
        _obs_enr_d     = _obs_enriched.get(_obs_code, {}) if run_type != "1차" else {}
        _obs_proc      = _obs_enr_d.get("processed", ProcessedData(code=_obs_code))
        _obs_sup       = _obs_c.get("supply") or SupplyData(code=_obs_code)
        _obs_news      = _obs_enr_d.get("news") or NewsData(code=_obs_code)
        _obs_in_inter  = _obs_c.get("in_inter", False)
        _obs_has_pat   = _obs_pat.get("pattern_summary", "없음") != "없음"
        _obs_pat_label = _obs_pat.get("pattern_type_label", "없음")
        _obs_tv_ratio  = _obs_pat.get("tv_ratio")

        _obs_tv_min = TV_RATIO_WATCH_MIN if _obs_pat_label == "당일돌파형" else TV_RATIO_P2P3_MIN
        if _obs_tv_ratio is not None and _obs_tv_ratio < _obs_tv_min:
            _obs_remaining.append(_obs_c)
            continue
        if not _obs_in_inter and not _obs_has_pat:
            _obs_remaining.append(_obs_c)
            continue

        _obs_checklist     = build_checklist(_obs_code, _obs_tv, _obs_proc, _obs_sup)
        _obs_score         = calc_score(
            code=_obs_code, trading_value=_obs_tv, processed=_obs_proc,
            supply=_obs_sup, news=_obs_news, in_intersection=_obs_in_inter,
            patterns=_obs_pat,
        )
        _obs_sector        = _obs_c.get("sector", "")
        _obs_regular_close = _obs_enr_d.get("regular_close_price")
        _obs_signal_px     = _obs_c.get("signal_price", 0)
        _obs_entry_ref     = _obs_regular_close if _obs_regular_close else _obs_signal_px
        _obs_kh_sup_ok     = _calc_kh_supply_ok(_obs_sup) if _obs_pat.get("kim_hyungjun_flag") else None
        _obs_pat["kim_hyungjun_supply_ok"] = _obs_kh_sup_ok

        key_candidates.append({
            "name":                          _obs_c["name"],
            "code":                          _obs_code,
            "market":                        _obs_c.get("market", ""),
            "change_pct":                    _obs_c["change_pct"],
            "trading_value":                 _obs_tv,
            "signal_price":                  _obs_signal_px,
            "indicators":                    _obs_enr_d.get("indicators", {}),
            "patterns":                      _obs_pat,
            "supply":                        _obs_sup,
            "news":                          _obs_news,
            "score":                         _obs_score,
            "checklist":                     _obs_checklist,
            "in_inter":                      _obs_in_inter,
            "has_pattern":                   _obs_has_pat,
            "supply_ok":                     _obs_checklist.supply_ok,
            "near_high_52w":                 _obs_proc.near_high_52w,
            "sector":                        _obs_sector,
            "is_leading_sector":             bool(_obs_sector) and _obs_sector in leading_sector_names,
            "prog_net_eok":                  prog_data.get(_obs_code),
            "regular_close_price":           _obs_regular_close,
            "regular_close_price_available": bool(_obs_regular_close),
            "entry_reference_price":         _obs_entry_ref,
            "price_source":                  "regular_close_price" if _obs_regular_close else "signal_price",
            "source_pool":                   "recent_base_pool",
            "kim_hyungjun_supply_ok":        _obs_kh_sup_ok,
        })

    obs_candidates = _obs_remaining
    logger.info(f"obs→key 편입 후: key={len(key_candidates)}개 / obs 잔여={len(obs_candidates)}개")

    # ── 정렬: 교집합 > 패턴타입 > score > supply_ok > 거래대금 > 상승률 ──
    key_candidates.sort(key=_priority)

    # ── 장세별 핵심/관심 분리 ────────────────────────────────
    if market_regime == "강세":
        _max_n = CANDIDATES_MAX_BULL
    elif market_regime == "중립":
        _max_n = CANDIDATES_MAX_NEUTRAL
    elif market_regime == "약세" and market_subtype == "자금집중형":
        _max_n = CANDIDATES_MAX_CONCENTRATED_BEAR
    else:
        _max_n = CANDIDATES_MAX_BEAR
    core_candidates  = key_candidates[:_max_n]
    watch_candidates = key_candidates[_max_n:]
    logger.info(f"장세={market_regime} → 핵심 {len(core_candidates)}개 / 관심 {len(watch_candidates)}개")

    # ── LLM 뉴스 분석 (핵심 후보에만, 파이프라인 중단 금지) ────
    if USE_LLM_NEWS and core_candidates:
        try:
            from scripts import llm_analyzer
            for c in core_candidates:
                news = c.get("news")
                if isinstance(news, NewsData) and news.titles:
                    result = llm_analyzer.analyze_news(
                        code=c["code"],
                        name=c["name"],
                        change_pct=c["change_pct"],
                        pattern_type=c["patterns"].get("pattern_type_label", "없음"),
                        news_titles=news.titles,
                        sector=c.get("sector", ""),
                    )
                    if result:
                        news.llm_summary = result
        except Exception as e:
            logger.warning(f"LLM 분석 전체 실패 (무시): {e}")

    # ── daily_summary.json 저장 (복기 대시보드 크로스레퍼런스용) ────────────
    import json as _json_daily
    _summary_path = Path("data") / "signals" / f"daily_summary_{report_date}.json"
    _summary_path.parent.mkdir(parents=True, exist_ok=True)
    _summary_data = {
        "date":                 report_date,
        "run_time":             run_time,
        "run_type":             run_type,
        "kospi_level":          index_levels.get("kospi_level"),
        "kosdaq_level":         index_levels.get("kosdaq_level"),
        "kospi_tv_eok":         market_totals.get("kospi_total_tv_eok", 0),
        "kosdaq_tv_eok":        market_totals.get("kosdaq_total_tv_eok", 0),
        "market_regime":        market_regime,
        "market_type":          market_type,
        "leading_sector_names": [s["sector_name"] for s in leading_sectors[:4]],
        "limit_up_count":       limit_up_count,
        "code_to_sector":       code_to_sector,
    }
    try:
        _summary_path.write_text(
            _json_daily.dumps(_summary_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"daily_summary.json 저장: {_summary_path}")
    except Exception as e:
        logger.warning(f"daily_summary.json 저장 실패: {e}")

    # 시그널 저장
    def _kh_sig(c: dict, is_kh_only: bool = False) -> dict:
        pat = c.get("patterns", {})
        return {
            "kim_hyungjun_flag":                   pat.get("kim_hyungjun_flag", False),
            "kim_hyungjun_stage":                  pat.get("kim_hyungjun_stage"),
            "kim_hyungjun_base_offset":            pat.get("kim_hyungjun_base_offset"),
            "kim_hyungjun_base_tv_ratio":          pat.get("kim_hyungjun_base_tv_ratio"),
            "kim_hyungjun_today_tv_ratio":         pat.get("kim_hyungjun_today_tv_ratio"),
            "kim_hyungjun_close_vs_base_high_pct": pat.get("kim_hyungjun_close_vs_base_high_pct"),
            "kim_hyungjun_above_ma5":              pat.get("kim_hyungjun_above_ma5"),
            "kim_hyungjun_supply_ok":              c.get("kim_hyungjun_supply_ok"),
            "is_kh_only":                          is_kh_only,
        }

    _sig_rows = [{
        "종목명":             c["name"],
        "종목코드":           c["code"],
        "시장":               c["market"],
        "등락률":             c["change_pct"],
        "거래대금":           c["trading_value"],
        "signal_price":       c.get("signal_price", 0),
        "sector":             c.get("sector", ""),
        "패턴":               c["patterns"].get("pattern_summary", ""),
        "pattern_type_label": c["patterns"].get("pattern_type_label", "없음"),
        "base_candle_offset": c["patterns"].get("base_candle_day_offset"),
        "base_high_gap_pct":  c["patterns"].get("base_high_gap_pct"),
        "tv_ratio":           c["patterns"].get("tv_ratio"),
        "status_summary":     c["patterns"].get("status_summary", ""),
        "total_score":        c["score"].total_score if c.get("score") else 0,
        "checklist_pass":     c["checklist"].required_pass_count if c.get("checklist") else 0,
        "in_inter":           c["in_inter"],
        "supply_label":       getattr(c.get("supply"), "supply_label", ""),
        "run_time":                      run_time,
        "run_type":                      run_type,
        "signal_time":                   run_time,
        "regular_close_price":           c.get("regular_close_price"),
        "regular_close_price_available": c.get("regular_close_price_available", False),
        "entry_reference_price":         c.get("entry_reference_price", 0),
        "price_source":                  c.get("price_source", ""),
        **_kh_sig(c, is_kh_only=False),
    } for c in key_candidates]

    _kh_only_rows = [{
        "종목명":             c["name"],
        "종목코드":           c["code"],
        "시장":               c["market"],
        "등락률":             c["change_pct"],
        "거래대금":           c["trading_value"],
        "signal_price":       c.get("signal_price", 0),
        "sector":             c.get("sector", ""),
        "패턴":               "",
        "pattern_type_label": "없음",
        "base_candle_offset": None,
        "base_high_gap_pct":  None,
        "tv_ratio":           None,
        "status_summary":     "",
        "total_score":        0,
        "checklist_pass":     0,
        "in_inter":           c.get("in_inter", False),
        "supply_label":       getattr(c.get("supply"), "supply_label", "") or "",
        "run_time":                      run_time,
        "run_type":                      run_type,
        "signal_time":                   run_time,
        "regular_close_price":           None,
        "regular_close_price_available": False,
        "entry_reference_price":         c.get("signal_price", 0),
        "price_source":                  "signal_price",
        **_kh_sig(c, is_kh_only=True),
    } for c in kh_only_candidates]

    if _sig_rows or _kh_only_rows:
        save_signals(pd.DataFrame(_sig_rows + _kh_only_rows), timestamp_str)

    # 대시보드 생성
    report_data["market_summary"]["core_count"] = len(core_candidates)
    report_data["core_candidates"]     = core_candidates
    report_data["watch_candidates"]    = watch_candidates
    report_data["rejected_candidates"] = rejected_list
    report_data["kh_only_candidates"]  = kh_only_candidates
    report_data["kh_candidates_scope"] = "top40_only"
    report_data["obs_candidates"]      = obs_candidates

    dashboard_links = {}
    if ENABLE_DASHBOARD:
        try:
            latest_name = f"latest_{snapshot_time}.html" if run_type in ("1차", "2차") else "latest.html"
            dated_path  = REPORTS_DIR / f"{report_date}_{snapshot_time}.html"
            latest_path = REPORTS_DIR / latest_name
            generate_dashboard_html(report_data, dated_path, latest_path)
            if ENABLE_GITHUB_PAGES_LINK:
                dashboard_links = build_dashboard_links(report_date, snapshot_time, GITHUB_PAGES_BASE_URL, latest_name)
        except Exception as e:
            logger.warning(f"대시보드 생성 중 오류 (무시): {e}")

    # 알림 전송
    _ms_extra = {
        "tv_1500_count":         tv_1500_count,
        "gainers_tv_1500_count": gainers_tv_1500_count,
        "intersection_count":    len(intersection) if not intersection.empty else 0,
        "core_count":            len(core_candidates),
        "market_regime":         market_regime,
        "market_subtype":        market_subtype,
        "market_type":           market_type,
        "market_adl":            _market_adl,
        "kospi_level":           index_levels.get("kospi_level"),
        "kosdaq_level":          index_levels.get("kosdaq_level"),
        "kospi_chg":             index_levels.get("kospi_chg"),
        "kosdaq_chg":            index_levels.get("kosdaq_chg"),
        "limit_up_count":        limit_up_count,
        "limit_up_names":        limit_up_names,
        "limit_up_list":         limit_up_list,
        "code_to_sector":        code_to_sector,
        "inter_codes":           inter_codes,
    }
    if run_type == "1차":
        msg = ntf.build_first_alert(
            market_totals, gainers, top_tv, intersection,
            core_candidates, run_time, enriched,
            dashboard_links=dashboard_links,
            market_summary_extra=_ms_extra,
            leading_sectors=leading_sectors,
            watch_candidates=watch_candidates,
        )
        ntf.send_message(msg)
        logger.info(f"1차 알림 전송 완료 (핵심 {len(core_candidates)}개 / 관심 {len(watch_candidates)}개)")
    else:
        msg = ntf.build_second_alert(
            market_totals, gainers, top_tv, intersection,
            core_candidates, run_time, enriched,
            dashboard_links=dashboard_links,
            market_summary_extra=_ms_extra,
            leading_sectors=leading_sectors,
            watch_candidates=watch_candidates,
            run_type=run_type,
        )
        ntf.send_message(msg)
        logger.info(f"2차 알림 전송 완료 (핵심 {len(core_candidates)}개 / 관심 {len(watch_candidates)}개)")

    if ENABLE_DASHBOARD:
        try:
            generate_index_html(REPORTS_DIR)
        except Exception as e:
            logger.warning(f"인덱스 생성 중 오류 (무시): {e}")

    # ── 공개 리포트 생성 ─────────────────────────────────────
    try:
        from scripts import public_report as _pub
        _pub.run(
            report_date=report_date,
            market_summary={
                "kospi_level":   index_levels.get("kospi_level"),
                "kosdaq_level":  index_levels.get("kosdaq_level"),
                "kospi_chg":     index_levels.get("kospi_chg"),
                "kosdaq_chg":    index_levels.get("kosdaq_chg"),
                "market_regime": market_regime,
                "market_type":   market_type,
                "limit_up_count": limit_up_count,
                "kospi_tv_eok":  market_totals.get("kospi_total_tv_eok", 0),
                "kosdaq_tv_eok": market_totals.get("kosdaq_total_tv_eok", 0),
            },
            leading_sectors=leading_sectors,
            top_tv_records=top_tv.head(20).to_dict("records") if not top_tv.empty else [],
        )
    except Exception as e:
        logger.warning(f"공개 리포트 생성 실패 (무시): {e}")

    if preview and GITHUB_PAGES_BASE_URL:
        glossary_url = GITHUB_PAGES_BASE_URL.rstrip("/") + "/reports/glossary.html"
        ntf.send_message(f"📖 용어 해설집\n{glossary_url}")

    logger.info("=== 파이프라인 완료 ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", action="store_true",
                        help="TELEGRAM_CHAT_ID_DEV로만 발송 (단체방 제외)")
    args = parser.parse_args()
    run(preview=args.preview)
