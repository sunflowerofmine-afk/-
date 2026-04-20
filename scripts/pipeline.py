# scripts/pipeline.py
"""전체 파이프라인 메인 모듈"""

import sys
import logging
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import (
    LOG_DIR, MIN_TRADING_VALUE_EOK,
    ENABLE_NEWS_FETCH, ENABLE_SUPPLY_FETCH, USE_LLM_NEWS,
    REQUEST_DELAY,
    REPORTS_DIR, ENABLE_DASHBOARD, ENABLE_GITHUB_PAGES_LINK, GITHUB_PAGES_BASE_URL,
    TV_RATIO_WATCH_MIN,
    ENABLE_SECTOR_FETCH, SECTOR_TOP_N,
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
)
from scripts.pattern_detector import detect_patterns
from scripts import ranking as rnk
from scripts.ranking import filter_excluded_stocks
from scripts.models import ProcessedData, SupplyData, NewsData
from scripts.scoring import calc_score, build_checklist
from scripts import notifier as ntf
from scripts.dashboard import generate_dashboard_html, build_dashboard_links, generate_index_html

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


def _enrich_candidates(codes: list[str], all_df: pd.DataFrame) -> dict:
    """
    상위 후보 종목에 대해 히스토리, 지표, 패턴, 수급, 뉴스 수집.
    반환: {code: {indicators, patterns, supply, news}}
    """
    enriched = {}

    for code in codes:
        enr = {"indicators": {}, "patterns": {}, "supply": SupplyData(code=code), "news": NewsData(code=code)}
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

            bc = is_big_candle(
                open_=_open,
                high=_close,   # 장중 고가 불명 → 현재가로 보수 추정
                low=_close,
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
            )
            enr["indicators"] = {
                **bc, **fbc,
                "ma_cluster": mac["cluster"],
                "ma_details": mac,
                "vol_peak":   vpk,
                "tv_peak":    tvpk,
            }
            enr["processed"] = processed

            pat = detect_patterns(
                code=code,
                today_open=_open,
                today_high=_close,
                today_low=_close,
                today_close=_close,
                today_change_pct=_chg,
                today_tv=tv,
                daily_df=daily_df,
            )
            enr["patterns"] = pat

        # 수급 (주수 × 현재가 → 원화 변환)
        if ENABLE_SUPPLY_FETCH:
            try:
                sup = fetch_supply(code)
                _price = float(row.get("현재가", 0))
                if sup.status == "ok" and _price > 0:
                    if sup.institution_net is not None:
                        sup.institution_net = sup.institution_net * _price
                    if sup.foreign_net is not None:
                        sup.foreign_net = sup.foreign_net * _price
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


def run():
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

    if not raw_data:
        ntf.send_message(f"<b>[오류]</b> {run_time} KST\n데이터 수집 실패")
        logger.error("수집 실패 → 종료")
        return

    # raw 저장
    for market_name, df in raw_data.items():
        save_raw(df, market_name, timestamp_str)

    # 전체 합치기
    all_df = pd.concat(raw_data.values(), ignore_index=True)

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
        leading_sectors.append({
            "sector_name": sec["sector_name"],
            "change_pct":  avg_chg,
            "tv_eok":      round(float(sec_df["거래대금"].sum()) / 1e8, 0),
            "top_stocks":  top_stocks,
        })

    # gainers_top20, trading_value_top20에 sector 태그 추가
    def _add_sector(records: list) -> list:
        for r in records:
            r["sector"] = code_to_sector.get(str(r.get("종목코드", "")), "")
        return records

    # ── 5. report_data 기본 구조 (1차/2차 공통) ──────────────
    report_date   = now.strftime("%Y-%m-%d")
    snapshot_time = {"1차": "1450", "2차": "1750"}.get(run_type, timestamp_str.split("_")[1])

    _min_tv_won = MIN_TRADING_VALUE_EOK * 100_000_000
    tv_1500_count = int((filtered_df["거래대금"] >= _min_tv_won).sum()) if not filtered_df.empty else 0
    gainers_tv_1500_count = int((gainers["거래대금"] >= _min_tv_won).sum()) if not gainers.empty else 0

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
        },
        "gainers_top20":          _add_sector(gainers.to_dict("records") if not gainers.empty else []),
        "trading_value_top20":    _add_sector(top_tv.to_dict("records")  if not top_tv.empty  else []),
        "intersection_candidates": intersection.to_dict("records") if not intersection.empty else [],
        "core_candidates":        [],
        "rejected_candidates":    [],
        "leading_sectors":        leading_sectors,
        "sector_calendar":        {},
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

    # ── 6. 전체 후보 분석 (1차/2차/수동 공통) ───────────────────
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
    MIN_TV_WON     = MIN_TRADING_VALUE_EOK * 100_000_000
    inter_codes    = set(intersection["종목코드"].dropna() if not intersection.empty else [])

    # ── TV 1차 필터: 크롤링 전에 적용해 불필요한 수집 방지 ─────
    tv_map = filtered_df.set_index("종목코드")["거래대금"].to_dict()
    crawl_codes = []
    for code in candidate_codes:
        tv = float(tv_map.get(code, 0))
        if tv < MIN_TV_WON:
            row = filtered_df[filtered_df["종목코드"] == code]
            name = row.iloc[0].get("종목명", "") if not row.empty else ""
            chg  = float(row.iloc[0].get("등락률", 0)) if not row.empty else 0.0
            rejected_list.append({"code": code, "name": name,
                                   "reason": f"거래대금 부족 ({tv/1e8:.0f}억)",
                                   "trading_value": tv, "change_pct": chg})
        else:
            crawl_codes.append(code)

    logger.info(f"후보 종목 {len(crawl_codes)}개 지표 수집 시작 (TV필터 후, 원본 {len(candidate_codes)}개) [{run_type}]...")
    enriched = _enrich_candidates(crawl_codes, filtered_df)

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

        # 거래대금 급감 제외 (ratio < 0.2)
        if tv_ratio is not None and tv_ratio < TV_RATIO_WATCH_MIN:
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
                               supply=supply, news=news, in_intersection=in_inter)
        supply_ok = checklist.supply_ok

        key_candidates.append({
            "name":          name,
            "code":          code,
            "market":        row.get("시장", ""),
            "change_pct":    float(row.get("등락률", 0)),
            "trading_value": tv,
            "indicators":    enr.get("indicators", {}),
            "patterns":      pat,
            "supply":        supply,
            "news":          news,
            "score":         score,
            "checklist":     checklist,
            "in_inter":      in_inter,
            "has_pattern":   has_pattern,
            "supply_ok":     supply_ok,
        })

    # 정렬: 교집합 > 패턴타입 > score > supply_ok > 거래대금 > 상승률
    _PATTERN_TYPE_ORDER = {"당일돌파형": 0, "고가횡보형": 1, "눌림관찰형": 2, "없음": 3}

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

    key_candidates.sort(key=_priority)

    # ── LLM 뉴스 분석 (최종 후보에만, 파이프라인 중단 금지) ────
    if USE_LLM_NEWS and key_candidates:
        try:
            from scripts import llm_analyzer
            for c in key_candidates:
                news = c.get("news")
                if isinstance(news, NewsData) and news.titles:
                    result = llm_analyzer.analyze_news(
                        code=c["code"],
                        name=c["name"],
                        change_pct=c["change_pct"],
                        pattern_type=c["patterns"].get("pattern_type_label", "없음"),
                        news_titles=news.titles,
                    )
                    if result:
                        news.llm_summary = result
        except Exception as e:
            logger.warning(f"LLM 분석 전체 실패 (무시): {e}")

    # 시그널 저장
    if key_candidates:
        sig_df = pd.DataFrame([{
            "종목명":             c["name"],
            "종목코드":           c["code"],
            "시장":               c["market"],
            "등락률":             c["change_pct"],
            "거래대금":           c["trading_value"],
            "패턴":               c["patterns"].get("pattern_summary", ""),
            "pattern_type_label": c["patterns"].get("pattern_type_label", "없음"),
            "base_candle_offset": c["patterns"].get("base_candle_day_offset"),
            "base_high_gap_pct":  c["patterns"].get("base_high_gap_pct"),
            "tv_ratio":           c["patterns"].get("tv_ratio"),
            "status_summary":     c["patterns"].get("status_summary", ""),
            "total_score":        c["score"].total_score if c.get("score") else 0,
            "checklist_pass":     c["checklist"].required_pass_count if c.get("checklist") else 0,
            "run_time":           run_time,
            "run_type":           run_type,
        } for c in key_candidates])
        save_signals(sig_df, timestamp_str)

    # 대시보드 생성
    report_data["market_summary"]["core_count"] = len(key_candidates)
    report_data["core_candidates"]    = key_candidates
    report_data["rejected_candidates"] = rejected_list

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
        "core_count":            len(key_candidates),
    }
    if run_type == "1차":
        msg = ntf.build_first_alert(
            market_totals, gainers, top_tv, intersection,
            key_candidates, run_time, enriched,
            dashboard_links=dashboard_links,
            market_summary_extra=_ms_extra,
        )
        ntf.send_message(msg)
        logger.info(f"1차 알림 전송 완료 (핵심 후보 {len(key_candidates)}개)")
    else:
        msg = ntf.build_second_alert(
            market_totals, gainers, top_tv, intersection,
            key_candidates, run_time, enriched,
            dashboard_links=dashboard_links,
            market_summary_extra=_ms_extra,
        )
        ntf.send_message(msg)
        logger.info(f"2차 알림 전송 완료 (핵심 후보 {len(key_candidates)}개)")

    if ENABLE_DASHBOARD:
        try:
            generate_index_html(REPORTS_DIR)
        except Exception as e:
            logger.warning(f"인덱스 생성 중 오류 (무시): {e}")

    logger.info("=== 파이프라인 완료 ===")


if __name__ == "__main__":
    run()
