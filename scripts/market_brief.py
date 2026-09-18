# scripts/market_brief.py
"""시황 알림 — 봇이 사용자에게 보내는 유일한 알림 (2026-09-11 결정).

봇은 종목을 고르지 않는다. 후보·게이트·패턴은 계속 계산해 저장하되(백테스트용) 알림엔 싣지
않는다. 이유: 봇 선별은 D+1 승률 48.1%·중앙값 0.00%로 예측력이 없고, 고수 셋이 산 대장주를
봇 트랙이 구조적으로 못 고르며, 후보를 보여 주면 사용자 판단이 거기 앵커링된다.
사용자는 이 시황을 보고 MTS에서 직접 판단하고, 매매 근거는 텔레그램 답장으로 남긴다
(`scripts/harvest_trades.py`).

내용 = 판단지 0번("오늘 자리가 있는가"). 숫자만 쓴다. "밀리지 않았다" 같은 해석 문구는 넣지
않는다 — 사용자 요청.
  지수 / 거래대금(전일·20일 평균 대비 %) / 오른 종목 비율·상한가·집중도 /
  미선물·유가·환율 / NXT 거래대금 상위 10 / 다음 거래일까지 밤 수 + 테마 맵 이미지
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import REPORTS_DIR
from scripts import market_history as mh
from scripts import notifier as ntf
from scripts.market_calendar import get_now_kst, get_run_type, is_trading_day
from scripts.naver_api import fetch_stock_default, to_float

logger = logging.getLogger(__name__)

_WD = ["월", "화", "수", "목", "금", "토", "일"]
# 슬롯 라벨 — 사용자가 "NXT 막판"이 무슨 뜻인지 물었다(2026-09-15). 시각의 뜻을 그대로 쓴다.
_SLOT_LABEL = {"1420": "KRX 마감 70분 전 · 잠정치", "1535": "KRX 마감 · 종가 확정",
               "1750": "저녁장(KRX·NXT) 중간", "1930": "저녁장 마감 30분 전"}
KRX_CLOSE_DIR = Path("data") / "krx_close"   # 정규장 종가 저장(15:35 실행이 씀, 저녁 실행이 읽음)
_SESSION_KO = {"PRE_MARKET": "프리장", "REGULAR_MARKET": "메인", "AFTER_MARKET": "저녁장"}
NXT_TOP_N = 10


def slot_for(now: datetime, run_type: str) -> str:
    hhmm = now.strftime("%H%M")
    if run_type == "1차":
        return "1420"
    if run_type == "2차":
        return "1535" if hhmm < "1700" else "1750"
    if "1900" <= hhmm <= "2000":
        return "1930"
    return hhmm


def _next_trading_day(d: date) -> date:
    n = d + timedelta(days=1)
    for _ in range(15):
        if is_trading_day(n):
            return n
        n += timedelta(days=1)
    return n


def _pct_txt(v: float | None, none: str = "기록 없음") -> str:
    return none if v is None else f"{v:+.0f}%"


def _tv_txt(eok: float | None) -> str:
    if not eok:
        return "-"
    return f"{eok/10000:.1f}조" if eok >= 10000 else f"{eok:,.0f}억"


def _adl_from(df: pd.DataFrame) -> float | None:
    """오른 종목 비율(%) — 등락률 0 제외. pipeline._calc_market_regime과 같은 정의."""
    try:
        chg = df["등락률"].dropna()
        total = int((chg != 0).sum())
        return round(float((chg > 0).sum()) / total * 100, 1) if total else None
    except Exception:
        return None


def collect(now: datetime, run_type: str, raw_data: dict[str, pd.DataFrame] | None = None,
            merged_df: pd.DataFrame | None = None, index_levels: dict | None = None) -> dict:
    """알림 재료를 모은다. pipeline이 이미 받은 데이터는 인자로 넘겨 다시 받지 않는다.
    단독 실행(19:30)이면 전부 직접 받는다. 어느 항목이 실패해도 나머지로 알림을 만든다."""
    from scripts import fetch_market_data as fmd
    from scripts.ranking import calc_market_total, filter_excluded_stocks

    d: dict = {"now": now, "run_type": run_type, "slot": slot_for(now, run_type)}
    today = now.date()

    # 지수
    if index_levels is None:
        try:
            index_levels = fmd.fetch_index_levels()
        except Exception as e:
            logger.warning(f"지수 수집 실패: {e}")
            index_levels = {}
    d["index"] = index_levels

    # 전 종목 (KRX). 표시용 KRX 가격은 NXT 합산 전 원본에서 읽는다.
    if raw_data is None:
        try:
            raw_data = fmd.run()
        except Exception as e:
            logger.warning(f"전 종목 수집 실패: {e}")
            raw_data = {}
    kospi_df = raw_data.get("KOSPI", pd.DataFrame())
    kosdaq_df = raw_data.get("KOSDAQ", pd.DataFrame())
    all_raw = pd.concat([x for x in (kospi_df, kosdaq_df) if not x.empty], ignore_index=True) \
        if (not kospi_df.empty or not kosdaq_df.empty) else pd.DataFrame()
    totals = calc_market_total(kospi_df, kosdaq_df)
    d["kospi_tv_eok"]  = totals.get("kospi_total_tv_eok") or None     # ETF·ETN 포함 (집중도 분모용)
    d["kosdaq_tv_eok"] = totals.get("kosdaq_total_tv_eok") or None

    # 알림에 보이는 거래대금 = 지수 거래대금(주식만, HTS·네이버 지수 화면과 같은 값).
    # 오늘 값은 지수 integration API(장중엔 부분 누적), 없으면 marketValue 주식만 합.
    # 과거 20일은 기록 파일의 마감 후 행. 16:00 이후엔 15:35 행의 값이 "정규장 거래대금"이고,
    # 지금 주식만 합에서 그것을 뺀 만큼이 장후(종가매매 + KRX 애프터마켓) 누적이다.
    from scripts.naver_api import fetch_index_turnover_today
    today_s = today.isoformat()
    def _stock_only(df: pd.DataFrame) -> float | None:
        if df.empty or "유형" not in df.columns:
            return None
        v = float(df.loc[df["유형"] == "stock", "거래대금"].sum()) / 1e8
        return v or None
    own_k, own_d = _stock_only(kospi_df), _stock_only(kosdaq_df)
    api_k = api_d = None
    try:
        api_k = fetch_index_turnover_today("KOSPI")
        api_d = fetch_index_turnover_today("KOSDAQ")
    except Exception as e:
        logger.warning(f"지수 거래대금 API 실패: {e}")
    d["kospi_idx_tv_eok"]  = api_k or own_k
    d["kosdaq_idx_tv_eok"] = api_d or own_d
    hist_rows = mh.load()
    series_k: dict[str, float] = {}
    series_d: dict[str, float] = {}
    for r in hist_rows:
        if mh._is_closed(str(r.get("slot") or "")) and r.get("date", "") < today_s:
            kv, dv = mh._f(r.get("kospi_idx_tv_eok")), mh._f(r.get("kosdaq_idx_tv_eok"))
            if kv:
                series_k[r["date"]] = kv     # 같은 날 여러 행이면 마지막(가장 늦은) 행이 남는다
            if dv:
                series_d[r["date"]] = dv
    d["after_k"] = d["after_d"] = None
    if now.strftime("%H%M") >= "1600":
        row1535 = next((r for r in hist_rows if r.get("date") == today_s and r.get("slot") == "1535"), None)
        reg_k, reg_d = mh._f(row1535 and row1535.get("kospi_idx_tv_eok")), mh._f(row1535 and row1535.get("kosdaq_idx_tv_eok"))
        if reg_k and own_k and own_k > reg_k:
            d["after_k"] = own_k - reg_k
        if reg_d and own_d and own_d > reg_d:
            d["after_d"] = own_d - reg_d
        if reg_k:
            d["kospi_idx_tv_eok"] = reg_k       # 비교는 정규장끼리
        if reg_d:
            d["kosdaq_idx_tv_eok"] = reg_d
    d["cmp_idx"] = mh.compare_index(today_s, d["slot"], d["kospi_idx_tv_eok"], d["kosdaq_idx_tv_eok"], series_k, series_d)

    # NXT 행의 기준 = 정규장 종가. 네이버 현재가는 16:00부터 20:00 KRX 애프터마켓 가격을 따라 움직이고
    # 일봉 종가도 그 마지막 가격이다(2026-09-14 실측: 하닉 정규장 1,697,000 → 17:50 1,682,000 → 일봉 1,683,000).
    # 그래서 15:30부터 16:00 사이 실행이 정규장 종가를 파일로 남기고, 저녁 실행은 그 파일을 읽는다.
    d["krx_price"] = {}
    d["krx_basis"] = ""
    d["krx_am"] = {}          # 종목별 KRX 애프터마켓 누적 거래대금(억) = 지금 누적 − 15:35 누적
    if not all_raw.empty:
        live = dict(zip(all_raw["종목코드"].astype(str), all_raw["현재가"].astype(float)))
        live_tv = dict(zip(all_raw["종목코드"].astype(str), all_raw["거래대금"].astype(float)))
        hhmm = now.strftime("%H%M")
        close_path = KRX_CLOSE_DIR / f"{today_s}.csv"
        if "1530" <= hhmm < "1600":
            # 정규장 종가·거래대금 + 시가·고가·저가·거래량. 저녁 실행과 D+1 복기가 이 파일로
            # 오늘 일봉을 정규장 값으로 되돌린다(네이버 일봉은 애프터마켓 반영). fetch_stock_data 참조.
            try:
                KRX_CLOSE_DIR.mkdir(parents=True, exist_ok=True)
                out = all_raw[["종목코드", "현재가", "거래대금"]].copy()
                try:
                    krx_rows = fetch_stock_default(trade_type="KRX", market_type="ALL", order_type="marketSum")
                    ohlv = pd.DataFrame([{
                        "종목코드": str(r.get("itemcode") or ""),
                        "시가": to_float(r.get("openPrice")), "고가": to_float(r.get("highPrice")),
                        "저가": to_float(r.get("lowPrice")), "거래량": to_float(r.get("tradeVolume")),
                    } for r in krx_rows])
                    out = out.merge(ohlv, on="종목코드", how="left")
                except Exception as e:
                    logger.warning(f"정규장 시가·고가·저가 저장 실패(종가·거래대금만 저장): {e}")
                out.to_csv(close_path, index=False, encoding="utf-8-sig")
            except Exception as e:
                logger.warning(f"정규장 종가 저장 실패: {e}")
            d["krx_price"], d["krx_basis"] = live, "정규장 종가"
        elif hhmm < "1530":
            d["krx_price"], d["krx_basis"] = live, "KRX 현재가"
        elif close_path.exists():
            saved = pd.read_csv(close_path, dtype={"종목코드": str}, encoding="utf-8-sig")
            d["krx_price"] = dict(zip(saved["종목코드"].astype(str), saved["현재가"].astype(float)))
            d["krx_basis"] = "정규장 종가"
            if "거래대금" in saved.columns:
                base_tv = dict(zip(saved["종목코드"].astype(str), saved["거래대금"].astype(float)))
                for code, tv_now in live_tv.items():
                    diff = tv_now - base_tv.get(code, tv_now)
                    if diff > 0:
                        d["krx_am"][code] = diff / 1e8
        else:
            d["krx_price"], d["krx_basis"] = live, "KRX 애프터마켓 반영가"   # 15:35 파일이 없을 때만
        d["krx_live_price"] = live

    # NXT 전체 (거래대금 상위 10 + 총액). 실패하면 빈 값.
    nxt_rows: list[dict] = []
    try:
        nxt_rows = fetch_stock_default(trade_type="NXT", market_type="ALL", order_type="quantTop")
    except Exception as e:
        logger.warning(f"NXT 수집 실패: {e}")
    nxt = []
    for r in nxt_rows:
        code = str(r.get("itemcode") or "")
        amt = to_float(r.get("tradeAmount"), 0.0) or 0.0
        nxt.append({"code": code, "name": r.get("itemname"), "price": to_float(r.get("nowPrice")),
                    "tv_eok": amt / 1e8, "session": r.get("tradingSessionType"),
                    "status": r.get("marketStatus")})
    nxt.sort(key=lambda x: x["tv_eok"], reverse=True)
    d["nxt_total_eok"] = round(sum(x["tv_eok"] for x in nxt)) if nxt else None
    d["nxt_session"] = _SESSION_KO.get((nxt[0]["session"] if nxt else "") or "", "")
    d["nxt_open"] = bool(nxt and nxt[0]["status"] == "OPEN")
    # 저녁(16:00 이후)엔 KRX 애프터마켓이 NXT와 같은 시간에 돈다 → 종목별 합산으로 순위를 매긴다.
    # 15:35 저장 파일에 거래대금이 없으면(09-18 이전 파일) NXT만으로 순위.
    d["krx_am_total_eok"] = round(sum(d["krx_am"].values())) if d.get("krx_am") else None
    if d.get("krx_am"):
        names = {r.get("itemcode"): r.get("itemname") for r in nxt_rows}
        by_code = {x["code"]: dict(x) for x in nxt}
        for code, am in d["krx_am"].items():
            row = by_code.setdefault(code, {"code": code, "name": names.get(code), "price": None, "tv_eok": 0.0})
            row["krx_am_eok"] = am
        for row in by_code.values():
            row.setdefault("krx_am_eok", 0.0)
            row["total_eok"] = row["tv_eok"] + row["krx_am_eok"]
            if not row.get("name"):
                row["name"] = (all_raw.loc[all_raw["종목코드"].astype(str) == row["code"], "종목명"].iloc[0]
                               if not all_raw.empty and (all_raw["종목코드"].astype(str) == row["code"]).any() else row["code"])
            if not row.get("price"):
                row["price"] = (d.get("krx_live_price") or {}).get(row["code"])   # NXT 체결 없으면 KRX 애프터마켓가
        merged_rows = sorted(by_code.values(), key=lambda x: x["total_eok"], reverse=True)
        d["nxt_top"] = merged_rows[:NXT_TOP_N]
        d["evening_merged"] = True
    else:
        d["nxt_top"] = nxt[:NXT_TOP_N]
        d["evening_merged"] = False

    # 오른 종목 비율 · 상한가 · Top5 집중도 — pipeline과 같은 정의.
    # 집중도 분자는 NXT 합산 후 거래대금이라(pipeline의 top_tv) 단독 실행도 같은 합산을 거친다.
    if (merged_df is None or merged_df.empty) and not all_raw.empty:
        try:
            from scripts.fetch_nxt_data import fetch_nxt_quant, merge_nxt_into_df
            merged_df = merge_nxt_into_df(all_raw.copy(), fetch_nxt_quant())
        except Exception as e:
            logger.warning(f"NXT 합산 실패 — KRX만으로 계산: {e}")
    base_df = merged_df if merged_df is not None and not merged_df.empty else all_raw
    d["adl_pct"] = _adl_from(base_df) if not base_df.empty else None
    d["limit_up"] = None
    d["top5_pct"] = None
    d["limit_up_top"] = []
    d["day_top"] = []
    if not base_df.empty:
        try:
            ex = filter_excluded_stocks(base_df)
            lu_df = ex[ex["등락률"] >= 29.5].nlargest(5, "거래대금")
            d["limit_up"] = int((ex["등락률"] >= 29.5).sum())
            d["limit_up_top"] = [{"name": r["종목명"], "tv_eok": float(r["거래대금"]) / 1e8} for _, r in lu_df.iterrows()]
            total_eok = (d["kospi_tv_eok"] or 0) + (d["kosdaq_tv_eok"] or 0)
            if total_eok > 0:
                top5_eok = float(ex.nlargest(5, "거래대금")["거래대금"].sum()) / 1e8
                d["top5_pct"] = round(top5_eok / total_eok * 100, 1)
            # 낮 상위 10 = KRX + NXT(전체) 합산 거래대금. 등락률은 KRX, NXT 비중 표기. 14:20·15:35 전용.
            nxt_by_code = {x["code"]: x["tv_eok"] for x in nxt}
            ex_raw = filter_excluded_stocks(all_raw) if not all_raw.empty else ex
            rows = []
            for _, r in ex_raw.iterrows():
                code = str(r["종목코드"])
                krx_eok = float(r["거래대금"]) / 1e8
                n_eok = nxt_by_code.get(code, 0.0)
                tot = krx_eok + n_eok
                rows.append({"code": code, "name": r["종목명"], "chg": float(r["등락률"]),
                             "total_eok": tot, "nxt_share": (n_eok / tot * 100) if tot > 0 else 0.0})
            rows.sort(key=lambda x: x["total_eok"], reverse=True)
            d["day_top"] = rows[:NXT_TOP_N]
        except Exception as e:
            logger.warning(f"상한가·집중도·상위 계산 실패: {e}")

    # 시장 수급 — 지수 단위 외인·기관·개인 순매수(억). 당일 행이면 잠정치.
    d["flow"] = {}
    for code in ("KOSPI", "KOSDAQ"):
        try:
            from scripts.naver_api import get_json
            t = get_json(f"https://m.stock.naver.com/api/index/{code}/trend", {"pageSize": 1})
            t = t[0] if isinstance(t, list) else t
            d["flow"][code] = {"date": str(t.get("bizdate") or ""), "foreign": to_float(t.get("foreignValue")),
                               "inst": to_float(t.get("institutionalValue")), "personal": to_float(t.get("personalValue"))}
        except Exception as e:
            logger.warning(f"[{code}] 시장 수급 실패: {e}")

    # 17:50 스냅샷 — 19:30이 "17:50 대비 변화"를 보이기 위해 저장(워크플로가 커밋)
    snap_dir = Path("data") / "evening_snapshot"
    snap_path = snap_dir / f"{today_s}.json"
    d["snap_prev"] = None
    if d["slot"] == "1750":
        try:
            snap_dir.mkdir(parents=True, exist_ok=True)
            snap_path.write_text(json.dumps({
                "time": now.strftime("%H:%M"), "nxt_total_eok": d.get("nxt_total_eok"),
                "krx_am_total_eok": d.get("krx_am_total_eok"),
                "rows": {x["code"]: {"price": x.get("price"), "total_eok": x.get("total_eok", x.get("tv_eok"))}
                         for x in d.get("nxt_top") or []},
            }, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.warning(f"저녁 스냅샷 저장 실패: {e}")
    elif d["slot"] == "1930" and snap_path.exists():
        try:
            d["snap_prev"] = json.loads(snap_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"저녁 스냅샷 읽기 실패: {e}")

    # 미선물 · 거시
    try:
        from scripts.fetch_futures import fetch_futures
        d["futures"] = fetch_futures()
    except Exception as e:
        logger.warning(f"미선물 실패: {e}")
        d["futures"] = {}
    try:
        from scripts.fetch_macro import fetch_macro
        d["macro"] = fetch_macro()
    except Exception as e:
        logger.warning(f"거시 실패: {e}")
        d["macro"] = {}

    # 전일·20일 비교 (기록 파일) → 그 뒤에 오늘 행 추가
    d["cmp"] = mh.compare(today.isoformat(), d["slot"], d["kospi_tv_eok"], d["kosdaq_tv_eok"], d["nxt_total_eok"])
    try:
        mh.append({
            "date": today.isoformat(), "slot": d["slot"], "time": now.strftime("%H%M"),
            "kospi_tv_eok": d["kospi_tv_eok"], "kosdaq_tv_eok": d["kosdaq_tv_eok"],
            "nxt_tv_eok": d["nxt_total_eok"], "adl_pct": d["adl_pct"], "limit_up": d["limit_up"],
            "top5_pct": d["top5_pct"], "kospi_chg": index_levels.get("kospi_chg"),
            "kosdaq_chg": index_levels.get("kosdaq_chg"),
            "kospi_idx_tv_eok": d.get("kospi_idx_tv_eok"), "kosdaq_idx_tv_eok": d.get("kosdaq_idx_tv_eok"),
        })
    except Exception as e:
        logger.warning(f"시황 기록 저장 실패: {e}")

    nxt_day = _next_trading_day(today)
    d["next_day"] = nxt_day
    d["nights"] = (nxt_day - today).days
    return d


def _macro_line(d: dict) -> str:
    fu = d.get("futures") or {}; mc = d.get("macro") or {}
    parts = []
    for name in ("나스닥선물", "S&P선물", "VIX"):
        v = fu.get(name)
        if v and v.get("chg_pct") is not None:
            parts.append(f"{name} {v['value']:,.1f} ({v['chg_pct']:+.2f}%)" if name == "VIX" else f"{name} {v['chg_pct']:+.2f}%")
    if mc.get("wti") is not None:
        parts.append(f"WTI {mc['wti']:.1f} ({mc.get('wti_chg', 0):+.2f})")
    if mc.get("usdkrw") is not None:
        parts.append(f"환율 {mc['usdkrw']:,.1f} ({mc.get('usdkrw_chg', 0):+.1f})")
    return " · ".join(parts)


def _flow_line(d: dict) -> str:
    """시장 수급 — 외인·기관·개인 순매수(억). 당일 행이면 잠정."""
    fl = d.get("flow") or {}
    k = fl.get("KOSPI") or {}
    if not k or k.get("foreign") is None:
        return ""
    today8 = d["now"].strftime("%Y%m%d")
    kd = str(k.get("date") or "")
    tag = "당일 잠정" if kd == today8 else f"{kd[4:6]}/{kd[6:8]} 확정"
    def _v(x):  return f"{x:+,.0f}억" if x is not None else "-"
    dq = fl.get("KOSDAQ") or {}
    return (f"수급({tag}) 코스피 외인 {_v(k.get('foreign'))} · 기관 {_v(k.get('inst'))} · 개인 {_v(k.get('personal'))}"
            f" / 코스닥 외인 {_v(dq.get('foreign'))} · 기관 {_v(dq.get('inst'))}")


def build_text(d: dict) -> str:
    # 낮(14:20·15:35)과 저녁(17:50·19:30)의 틀이 다르다 — 그 시각에 정할 것에 필요한 것만.
    # 낮: 국면·비중 판단 재료 전부 + 테마 맵. 저녁: 저녁장 흐름만(정규장 숫자 반복 없음, 맵 없음).
    now: datetime = d["now"]
    slot = d["slot"]
    head_when = f"{now.month:02d}/{now.day:02d} ({_WD[now.weekday()]}) {now.strftime('%H:%M')}"
    label = _SLOT_LABEL.get(slot, "수동")
    evening = now.strftime("%H%M") >= "1600"
    L = [f"<b>[종베 시황] {head_when} · {label}</b>"]
    c = d.get("cmp") or {}
    basis = d.get("krx_basis") or "KRX 가격"

    if not evening:
        ix = d.get("index") or {}
        def _lv(v):  return f"{v:,.2f}" if v is not None else "-"
        def _ch(v):  return f"<b>{v:+.2f}%</b>" if v is not None else "-"
        L.append(f"코스피 {_lv(ix.get('kospi_level'))} {_ch(ix.get('kospi_chg'))} · 코스닥 {_lv(ix.get('kosdaq_level'))} {_ch(ix.get('kosdaq_chg'))}")

        ci = d.get("cmp_idx") or {}
        kt, dt = d.get("kospi_idx_tv_eok"), d.get("kosdaq_idx_tv_eok")
        if slot == "1420":
            def _prog(v):  return f"{v}%" if v is not None else "-"
            L.append(f"거래대금(주식) 코스피 {_tv_txt(kt)} · 코스닥 {_tv_txt(dt)} — 지금까지 누적")
            L.append(f" ├ 전일 같은 시각 대비: 코스피 {_pct_txt(ci.get('kospi_vs_prev'))} · 코스닥 {_pct_txt(ci.get('kosdaq_vs_prev'))}")
            L.append(f" └ 전일 하루치 대비 진행률: 코스피 {_prog(ci.get('progress_kospi'))} · 코스닥 {_prog(ci.get('progress_kosdaq'))}")
        else:
            L.append(f"거래대금(주식·정규장) 코스피 {_tv_txt(kt)} · 코스닥 {_tv_txt(dt)}")
            L.append(f" ├ 전일 대비: 코스피 {_pct_txt(ci.get('kospi_vs_prev'))} · 코스닥 {_pct_txt(ci.get('kosdaq_vs_prev'))}")
            L.append(f" └ 20거래일 평균 대비: 코스피 {_pct_txt(ci.get('kospi_vs_avg20'))} · 코스닥 {_pct_txt(ci.get('kosdaq_vs_avg20'))}")

        adl = d.get("adl_pct"); lu = d.get("limit_up"); t5 = d.get("top5_pct")
        L.append((f"오른 종목 {adl:.0f}%" if adl is not None else "오른 종목 -")
                 + f" · 상한가 {lu if lu is not None else '-'} · Top5 집중 {t5 if t5 is not None else '-'}%")
        if d.get("limit_up_top"):
            L.append("상한가(거래대금순) " + " · ".join(f"{x['name']} {_tv_txt(x['tv_eok'])}" for x in d["limit_up_top"]))
        fl = _flow_line(d)
        if fl:
            L.append(fl)
        ml = _macro_line(d)
        if ml:
            L.append(ml)

        if d.get("day_top"):
            L.append("")
            L.append("<b>거래대금 상위 10</b> — KRX + NXT 합산 · 등락률 · NXT 비중")
            for i, x in enumerate(d["day_top"], 1):
                L.append(f" {i:>2} {x['name']} {x['chg']:+.2f}% · {_tv_txt(x['total_eok'])} (NXT {x['nxt_share']:.0f}%)")

    else:
        # 저녁: 정규장 숫자는 반복하지 않는다. 저녁장 흐름 + 저녁에 움직이는 지표만.
        sess = d.get("nxt_session") or "NXT"
        state = "진행 중" if d.get("nxt_open") else "마감"
        prev = d.get("snap_prev") or {}
        def _delta(cur, key):
            base = prev.get(key)
            return f" (17:50 대비 {cur - base:+,.0f}억)" if (prev and cur is not None and base is not None) else ""
        L.append(f"NXT {sess} {state} · 누적 {_tv_txt(d.get('nxt_total_eok'))}{_delta(d.get('nxt_total_eok'), 'nxt_total_eok')} · 전일 같은 시각 대비 {_pct_txt(c.get('nxt_vs_prev'))}")
        if d.get("evening_merged"):
            L.append(f"KRX 애프터마켓 누적 {_tv_txt(d.get('krx_am_total_eok'))}{_delta(d.get('krx_am_total_eok'), 'krx_am_total_eok')} (16:00부터)")
        elif d.get("after_k") or d.get("after_d"):
            L.append(f"KRX 장후 누적(종가매매 + 애프터마켓) 코스피 {_tv_txt(d.get('after_k'))} · 코스닥 {_tv_txt(d.get('after_d'))}")
        ml = _macro_line(d)
        if ml:
            L.append(ml)

        if d.get("nxt_top"):
            L.append("")
            if d.get("evening_merged"):
                L.append(f"<b>저녁장 거래대금 상위 10</b> — 현재가 · {basis} 대비 · NXT + KRX 애프터마켓 합산")
            else:
                L.append(f"<b>NXT 거래대금 상위 10</b> — NXT 현재가 · {basis} 대비")
            prev_rows = prev.get("rows") or {}
            for i, x in enumerate(d["nxt_top"], 1):
                krx = (d.get("krx_price") or {}).get(x["code"])
                px = x.get("price")
                if d.get("evening_merged"):
                    amt = f"{_tv_txt(x.get('total_eok'))} = N {_tv_txt(x.get('tv_eok'))} + K {_tv_txt(x.get('krx_am_eok'))}"
                else:
                    amt = _tv_txt(x["tv_eok"])
                pv = (prev_rows.get(x["code"]) or {}).get("price")
                since = f" · 17:50 대비 {(px / pv - 1) * 100:+.2f}%" if (px and pv) else ""
                if px and krx:
                    L.append(f" {i:>2} {x['name']} {px:,.0f} {(px / krx - 1) * 100:+.2f}% (종가 {krx:,.0f}){since} · {amt}")
                elif px:
                    L.append(f" {i:>2} {x['name']} {px:,.0f} (기준가 없음){since} · {amt}")
                else:
                    L.append(f" {i:>2} {x['name']} - · {amt}")

    nd = d.get("next_day")
    if nd:
        L.append("")
        L.append(f"다음 거래일 {nd.month:02d}/{nd.day:02d} ({_WD[nd.weekday()]}) · 밤 {d.get('nights')}")
    return chr(10).join(L)


def build_map(d: dict) -> Path | None:
    from scripts.theme_map import build
    now: datetime = d["now"]
    title = f"주도 테마 · {now.month:02d}/{now.day:02d} ({_WD[now.weekday()]}) {now.strftime('%H:%M')}"
    out = REPORTS_DIR / "theme_map" / f"{now.strftime('%Y-%m-%d_%H%M')}.png"
    return build(out, title)


def send(d: dict) -> bool:
    text = build_text(d)
    ok = ntf.send_message(text)
    logger.info(f"시황 알림 발송 {'성공' if ok else '실패'} ({d.get('slot')})")
    if d["now"].strftime("%H%M") >= "1600":
        logger.info("저녁 슬롯 — 테마 맵 생략(15:35과 같은 그림)")
        return ok
    img = build_map(d)
    if img:
        ok_img = ntf.send_photo(img)
        logger.info(f"테마 맵 발송 {'성공' if ok_img else '실패'}: {img}")
    else:
        logger.warning("테마 맵 없음 — 텍스트만 발송")
    return ok


def run(run_type: str | None = None) -> None:
    """단독 실행 (19:30 NXT 막판 · 수동). 전 종목·NXT·지표를 직접 받는다."""
    now = get_now_kst()
    rt = run_type or get_run_type(now)
    if not is_trading_day(now.date()):
        logger.info("비거래일 — 시황 알림 생략")
        return
    d = collect(now, rt)
    send(d)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true", help="TELEGRAM_CHAT_ID_DEV로만 발송")
    ap.add_argument("--dry", action="store_true", help="발송 없이 텍스트만 출력")
    a = ap.parse_args()
    if a.preview:
        ntf.set_preview_mode(True)
    if a.dry:
        _now = get_now_kst()
        _d = collect(_now, get_run_type(_now))
        print(build_text(_d))
        print(build_map(_d))
    else:
        run()
