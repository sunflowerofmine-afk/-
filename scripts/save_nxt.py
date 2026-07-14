# scripts/save_nxt.py
"""NXT 일별 거래대금 저장 — NXT 종료(20:00 KST) 이후 수동 실행 전용.

GitHub Actions의 nxt_save.yml에서 workflow_dispatch로 트리거.
fetch_nxt_quant()로 NXT 거래상위 종목의 당일 최종 거래대금을 수집해
data/nxt/{날짜}_nxt.csv 로 적재한다. 향후 NXT 합산 거래대금 백테스트용.
"""

import sys
import logging
from pathlib import Path
from datetime import datetime

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.fetch_nxt_data import fetch_nxt_quant

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_OUT_DIR = Path("data/nxt")


def run() -> None:
    nxt = fetch_nxt_quant()
    if not nxt:
        logger.warning("NXT 데이터 없음 — 저장 생략 (장 시간 외이거나 페이지 변경 가능성)")
        return

    df = pd.DataFrame.from_dict(nxt, orient="index")
    df.index.name = "종목코드"
    df = df.sort_values("nxt_tv", ascending=False)

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")  # 워크플로우에서 TZ=Asia/Seoul
    path = _OUT_DIR / f"{date_str}_nxt.csv"
    df.to_csv(path, encoding="utf-8-sig")

    top = df.head(2)
    top_str = ", ".join(f"{code} {row.nxt_tv/1e12:.1f}조" for code, row in top.iterrows())
    logger.info(f"NXT 거래대금 저장: {path} ({len(df)}종목) | 상위2: {top_str}")


if __name__ == "__main__":
    run()
