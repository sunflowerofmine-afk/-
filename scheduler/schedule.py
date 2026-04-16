"""
schedule.py
매일 15:05, 18:05 파이프라인 자동 실행
"""

import sys
import logging
from pathlib import Path

import schedule
import time

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import SCHEDULE_TIMES
from scripts.run_pipeline import run as run_pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def job():
    logger.info("스케줄 작업 실행")
    try:
        run_pipeline()
    except Exception as e:
        logger.exception(f"파이프라인 오류: {e}")


for t in SCHEDULE_TIMES:
    schedule.every().day.at(t).do(job)
    logger.info(f"스케줄 등록: 매일 {t}")

logger.info("스케줄러 시작. 종료하려면 Ctrl+C")
while True:
    schedule.run_pending()
    time.sleep(30)
