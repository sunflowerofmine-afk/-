"""
금요일(2026-04-17) 실제 raw 데이터 기반 테스트 대시보드 생성 스크립트.
핵심 후보는 실제 OHLCV 히스토리를 가져와 패턴을 탐지한다.
"""
import sys, time, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd

from config.settings import MIN_TRADING_VALUE_EOK, ENABLE_SUPPLY_FETCH, ENABLE_NEWS_FETCH, REQUEST_DELAY
from scripts.ranking import filter_excluded_stocks, apply_price_filter, get_top_gainers, get_top_trading_value, get_intersection, calc_market_total
from scripts.fetch_stock_data import fetch_chart_data
from scripts.fetch_supply_data import fetch_supply
from scripts.fetch_news import fetch_news
from scripts.indicators import is_big_candle, is_first_big_candle, is_ma_cluster, is_volume_peak, is_trading_value_peak, calc_all_ma
from scripts.pattern_detector import detect_patterns
from scripts.models import ProcessedData, SupplyData, NewsData
from scripts.scoring import calc_score, build_checklist
from scripts.dashboard import generate_dashboard_html, generate_index_html

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

MIN_TV_WON = MIN_TRADING_VALUE_EOK * 1e8

# ── 금요일 raw 데이터 로드 ───────────────────────────────────
kospi  = pd.read_csv("data/raw/2026-04-17_2039_KOSPI.csv",  encoding="utf-8-sig")
kosdaq = pd.read_csv("data/raw/2026-04-17_2039_KOSDAQ.csv", encoding="utf-8-sig")
all_df = pd.concat([kospi, kosdaq], ignore_index=True)

filtered = apply_price_filter(filter_excluded_stocks(all_df))
market_totals = calc_market_total(kospi, kosdaq)

gainers = get_top_gainers(filtered)
top_tv  = get_top_trading_value(filtered)
inter   = get_intersection(gainers, top_tv)
inter_codes = set(inter["종목코드"].dropna() if not inter.empty else [])

tv_1500_count         = int((filtered["거래대금"] >= MIN_TV_WON).sum())
gainers_tv_1500_count = int((gainers["거래대금"] >= MIN_TV_WON).sum())

# ── 후보 종목 수집 (상승률20 ∪ 거래대금20) ──────────────────
top20_g  = filtered[filtered["등락률"] > 0].nlargest(20, "등락률")
top20_tv = filtered.nlargest(20, "거래대금")
candidate_codes = list(set(
    list(top20_g["종목코드"].dropna()) + list(top20_tv["종목코드"].dropna())
))

logger.info(f"후보 {len(candidate_codes)}개 히스토리 수집 시작...")

key_candidates = []
rejected_list  = []
_PATTERN_TYPE_ORDER = {"당일돌파형": 0, "고가횡보형": 1, "눌림관찰형": 2, "없음": 3}

tv_map = filtered.set_index("종목코드")["거래대금"].to_dict()
for code in candidate_codes:
    row = filtered[filtered["종목코드"] == code]
    if row.empty:
        continue
    row  = row.iloc[0]
    tv   = float(row.get("거래대금", 0))
    name = row.get("종목명", "")

    if tv < MIN_TV_WON:
        rejected_list.append({"code": code, "name": name,
                               "reason": f"거래대금 부족 ({tv/1e8:.0f}억)",
                               "trading_value": tv, "change_pct": float(row.get("등락률", 0))})
        continue

    # 히스토리 수집
    daily_df = fetch_chart_data(code)
    time.sleep(0.3)

    indicators, processed, pat = {}, ProcessedData(code=code), {}

    if not daily_df.empty:
        daily_df_ma = calc_all_ma(daily_df)
        row0 = daily_df_ma.iloc[0]
        change_pct = float(row.get("등락률", 0))
        price      = float(row.get("현재가", 0))

        bc  = is_big_candle(price, price, price, price, change_pct, tv)
        fbc = is_first_big_candle(daily_df, today_idx=0)
        mac = is_ma_cluster(ma5=row0.get("ma5", 0), ma10=row0.get("ma10", 0),
                            ma20=row0.get("ma20", 0), ma60=row0.get("ma60"))
        vpk  = is_volume_peak(daily_df, today_idx=0)
        tvpk = is_trading_value_peak(daily_df, today_idx=0, today_tv=tv)

        first_bc_flag = bc.get("big_candle", False) and fbc.get("first_big_candle", False)
        processed = ProcessedData(
            code=code,
            ma5=row0.get("ma5"), ma10=row0.get("ma10"),
            ma20=row0.get("ma20"), ma60=row0.get("ma60"),
            ma_cluster_flag=mac["cluster"],
            volume_peak_60d=vpk, trading_value_peak_60d=tvpk,
            candle_body_ratio=bc.get("body_ratio", 0.0),
            upper_shadow_ratio=bc.get("upper_tail_ratio", 0.0),
            big_candle_flag=bc.get("big_candle", False),
            loose_big_candle_flag=bc.get("loose_big_candle", False),
            first_big_candle_flag=first_bc_flag,
            data_ok=fbc.get("data_ok", False),
        )
        indicators = {**bc, **fbc, "ma_cluster": mac["cluster"],
                      "vol_peak": vpk, "tv_peak": tvpk}

        pat = detect_patterns(
            code=code, today_open=price, today_high=price,
            today_low=price, today_close=price,
            today_change_pct=change_pct, today_tv=tv, daily_df=daily_df,
        )

    supply = SupplyData(code=code)
    if ENABLE_SUPPLY_FETCH:
        try:
            supply = fetch_supply(code)
            time.sleep(REQUEST_DELAY)
        except Exception:
            pass

    news_obj = NewsData(code=code)
    if ENABLE_NEWS_FETCH:
        try:
            news_obj = fetch_news(code)
            time.sleep(REQUEST_DELAY)
        except Exception:
            pass

    in_inter  = code in inter_codes
    overheated   = pat.get("overheated_3d_flag", False)
    struct_broken = pat.get("structure_broken_flag", False)
    has_pattern  = pat.get("pattern_summary", "없음") != "없음"

    if struct_broken:
        rejected_list.append({"code": code, "name": name, "reason": "구조 붕괴",
                               "trading_value": tv, "change_pct": float(row.get("등락률", 0))})
        continue
    if overheated:
        rejected_list.append({"code": code, "name": name, "reason": "과열",
                               "trading_value": tv, "change_pct": float(row.get("등락률", 0))})
        continue
    if not in_inter and not has_pattern:
        rejected_list.append({"code": code, "name": name, "reason": "패턴 없음 + 교집합 아님",
                               "trading_value": tv, "change_pct": float(row.get("등락률", 0))})
        continue

    checklist = build_checklist(code, tv, processed, supply)
    score     = calc_score(code=code, trading_value=tv, processed=processed,
                           supply=supply, news=news_obj, in_intersection=in_inter)

    key_candidates.append({
        "name": name, "code": code, "market": row.get("시장", ""),
        "change_pct": float(row.get("등락률", 0)), "trading_value": tv,
        "indicators": indicators, "patterns": pat,
        "supply": supply, "news": news_obj,
        "score": score, "checklist": checklist,
        "in_inter": in_inter, "has_pattern": has_pattern,
        "supply_ok": checklist.supply_ok,
    })

# 정렬
key_candidates.sort(key=lambda item: (
    not item["in_inter"],
    _PATTERN_TYPE_ORDER.get(item["patterns"].get("pattern_type_label", "없음"), 3),
    -(item["score"].total_score if item.get("score") else 0),
    not item["supply_ok"],
    -item["trading_value"],
    -item["change_pct"],
))

logger.info(f"핵심 후보: {len(key_candidates)}개")

# ── 대시보드 생성 ────────────────────────────────────────────
report_data = {
    "metadata": {
        "date": "2026-04-17", "snapshot_time": "1750",
        "run_time": "2026-04-17 17:50", "run_type": "2차 (테스트)",
    },
    "market_summary": {
        "kospi_tv_eok":          market_totals.get("kospi_total_tv_eok", 0),
        "kosdaq_tv_eok":         market_totals.get("kosdaq_total_tv_eok", 0),
        "tv_1500_count":         tv_1500_count,
        "gainers_tv_1500_count": gainers_tv_1500_count,
        "gainers_count":         len(gainers),
        "tv_count":              len(top_tv),
        "intersection_count":    len(inter) if not inter.empty else 0,
        "core_count":            len(key_candidates),
    },
    "gainers_top20":           gainers.to_dict("records"),
    "trading_value_top20":     top_tv.to_dict("records"),
    "intersection_candidates": inter.to_dict("records") if not inter.empty else [],
    "core_candidates":         key_candidates,
    "rejected_candidates":     rejected_list,
}

out = Path("reports/test_friday_1750.html")
generate_dashboard_html(report_data, out)
generate_index_html(Path("reports"))
print(f"\n✓ 생성 완료: {out.resolve()}")
print(f"  핵심 후보: {len(key_candidates)}개")
print(f"  탈락: {len(rejected_list)}개")
