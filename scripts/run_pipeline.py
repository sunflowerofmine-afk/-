"""
run_pipeline.py
전체 파이프라인 실행: 수집 → 필터링 → 텔레그램 전송
"""

import sys
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import PROCESSED_DIR, LOG_DIR
import scripts.fetch_data    as fetch
import scripts.filter_stocks as filt
import scripts.send_telegram  as tg

# ── 로깅 (파일 + 콘솔) ────────────────────────────────────
log_file = LOG_DIR / f"pipeline_{datetime.now().strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


def run():
    run_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    logger.info(f"=== 파이프라인 시작: {run_time} ===")

    # 1. 데이터 수집
    raw_paths = fetch.run()
    if not raw_paths:
        logger.error("수집된 데이터 없음. 파이프라인 중단")
        return

    # 2. 필터링 + 저장
    filtered = {}
    for market_name, raw_path in raw_paths.items():
        df = pd.read_csv(raw_path)
        result = filt.filter_stocks(df)

        processed_path = PROCESSED_DIR / raw_path.name.replace("_", "_filtered_", 1)
        result.to_csv(processed_path, index=False, encoding="utf-8-sig")
        filtered[market_name] = result

    # 3. 텔레그램 전송
    for market_name, df in filtered.items():
        msg = tg.format_message(df, market_name, run_time)
        tg.send_message(msg)

    logger.info("=== 파이프라인 완료 ===")


if __name__ == "__main__":
    run()
