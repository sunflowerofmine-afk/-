# scripts/fetch_sector_data.py
"""네이버 증권 업종 섹터 데이터 수집"""

import re
import sys
import time
import logging
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import REQUEST_DELAY

logger = logging.getLogger(__name__)

# 2026-09-11까지 finance.naver.com/sise/sise_group.naver(업종·테마 현황)과
# sise_group_detail.naver(구성 종목)를 긁었다. 네이버가 두 페이지를 stock.naver.com으로
# 리다이렉트하면서 JSON API로 전환. 반환 구조(sector_name/sector_no/change_pct, 종목코드 목록)는 동일.
from scripts.naver_api import fetch_group_list, fetch_group_stocks, to_float

_GROUP = {"upjong": "upjong", "theme": "theme"}


def fetch_sector_overview(group_type: str = "upjong") -> pd.DataFrame:
    """
    네이버 업종/테마 현황 → 섹터명, sector_no, 등락률 DataFrame 반환.
    group_type: "upjong" (업종) 또는 "theme" (테마)
    """
    try:
        items = fetch_group_list(_GROUP.get(group_type, group_type))
    except Exception as e:
        logger.warning(f"섹터 목록 요청 실패 ({group_type}): {e}")
        return pd.DataFrame()

    rows = []
    for it in items:
        no = to_float(it.get("no"))
        name = str(it.get("name") or "").strip()
        if no is None or not name:
            continue
        rows.append({
            "sector_name": name,
            "sector_no":   int(no),
            "change_pct":  to_float(it.get("changeRate"), 0.0),
        })

    if not rows:
        logger.warning(f"{group_type} overview 파싱 결과 없음 (API 응답 구조 변경 가능성)")
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    logger.info(f"{'업종' if group_type == 'upjong' else '테마'} overview 수집: {len(df)}개 섹터")
    return df


def fetch_sector_stock_codes(sector_no: int, group_type: str = "upjong") -> list:
    """업종/테마 구성 종목 코드 목록 추출"""
    items = fetch_group_stocks(_GROUP.get(group_type, group_type), sector_no)
    codes = []
    for it in items:
        code = str(it.get("itemcode") or "").strip()
        if re.fullmatch(r"\d{6}", code):
            codes.append(code)
    return list(dict.fromkeys(codes))  # deduplicate, preserve order


def run(top_n: int = 5) -> dict:
    """
    거래대금 상위 top_n개 섹터 수집.
    Returns:
        overview        : pd.DataFrame  전체 업종 개요
        top_sectors     : list[dict]    상위 섹터 (sector_name, change_pct, tv_eok, stock_codes)
        code_to_sector  : dict[str,str] 종목코드 → 섹터명
    """
    result: dict = {
        "overview":       pd.DataFrame(),
        "top_sectors":    [],
        "code_to_sector": {},
    }

    overview = fetch_sector_overview()
    if overview.empty:
        return result
    result["overview"] = overview

    top_df  = overview.nlargest(top_n, "change_pct")
    top_nos = set(top_df["sector_no"].tolist())
    code_to_sector: dict[str, str] = {}

    # 전체 섹터 순회 → code_to_sector 완전 매핑
    for _, row in overview.iterrows():
        no   = int(row["sector_no"])
        name = str(row["sector_name"])
        chg  = float(row["change_pct"])

        codes: list = []
        try:
            codes = fetch_sector_stock_codes(no)
            for c in codes:
                code_to_sector.setdefault(c, name)
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            logger.warning(f"[{name}] 구성종목 수집 실패: {e}")

        # 주도섹터(top_n)만 top_sectors 리스트에 추가
        if no in top_nos:
            result["top_sectors"].append({
                "sector_name": name,
                "change_pct":  chg,
                "tv_eok":      0.0,  # pipeline에서 filtered_df 기반 재계산
                "stock_codes": codes,
            })

    # 테마 데이터: code_to_sector override + top_sectors 교체
    # 상승률 상위 테마부터 순회 → 핫한 테마명이 개별 종목 라벨 및 주도섹터에 반영
    try:
        theme_overview = fetch_sector_overview("theme")
        if not theme_overview.empty:
            top_themes    = theme_overview.nlargest(top_n * 2, "change_pct")
            theme_sectors = []
            _theme_assigned: set[str] = set()   # 복수 테마 소속 종목: 최고 상승률 테마 라벨 유지
            for _, trow in top_themes.iterrows():
                tno   = int(trow["sector_no"])
                tname = str(trow["sector_name"])
                tchg  = float(trow["change_pct"])
                try:
                    tcodes = fetch_sector_stock_codes(tno, "theme")
                    for c in tcodes:
                        if c not in _theme_assigned:
                            code_to_sector[c] = tname
                            _theme_assigned.add(c)
                    theme_sectors.append({
                        "sector_name": tname,
                        "change_pct":  tchg,
                        "tv_eok":      0.0,
                        "stock_codes": tcodes,
                    })
                    time.sleep(REQUEST_DELAY)
                except Exception as e:
                    logger.warning(f"[테마 {tname}] 구성종목 수집 실패: {e}")
            if theme_sectors:
                # 등락률 상위 후보를 전부 넘긴다. 최종 top_n 선별은 pipeline이
                # 거래대금 비중으로 재정렬한 뒤 수행 — 여기서 자르면 종목 수가 많아
                # 평균 등락률이 희석되는 대형 테마(로봇·반도체 등)가 후보에서
                # 아예 빠져 거래대금 정렬로도 복구되지 않는다.
                result["top_sectors"] = theme_sectors
            logger.info(f"테마 override 완료: 상위 {len(theme_sectors)}개 테마 → 주도섹터 후보 교체")
    except Exception as e:
        logger.warning(f"테마 수집 실패 (무시, 업종명 유지): {e}")

    result["code_to_sector"] = code_to_sector
    logger.info(f"섹터 수집 완료: {len(result['top_sectors'])}섹터, {len(code_to_sector)}종목 매핑")
    return result
