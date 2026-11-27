# scripts/sync_signals.py
"""
GitHub Actions artifacts → data/signals/ 동기화.
gh CLI 없으면 건너뜁니다.

사용법:
  python -m scripts.sync_signals
"""
import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).parent.parent
SIGNALS_DIR = BASE / "data" / "signals"
KST = timezone(timedelta(hours=9))


def _run(cmd: list[str]) -> tuple[int, str]:
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


def main() -> None:
    # ── gh CLI 존재 확인 ────────────────────────────────────
    if not shutil.which("gh"):
        print("  gh CLI 없음 → 신호 파일 동기화 건너뜀")
        print("  (일부 종목에 SIGNAL_FILE_MISSING 태그 발생 가능)")
        return

    # ── 최근 20개 run 목록 ───────────────────────────────────
    rc, out = _run([
        "gh", "run", "list",
        "--workflow", "run_bot.yml",
        "--status", "success",
        "--limit", "20",
        "--json", "databaseId,createdAt",
        "--jq", "[.[] | {id:.databaseId,date:.createdAt}]",
    ])
    if rc != 0 or not out:
        print("  run 목록 조회 실패 → 건너뜀")
        return

    runs = json.loads(out)
    SIGNALS_DIR.mkdir(parents=True, exist_ok=True)

    # 이미 로컬에 있는 날짜 집합 (YYYY-MM-DD)
    existing = {f.stem[:10] for f in SIGNALS_DIR.glob("*_signals.csv")}
    synced = 0

    for run in runs:
        run_id = run["id"]
        dt = datetime.fromisoformat(run["date"].replace("Z", "+00:00")).astimezone(KST)
        date_str = dt.strftime("%Y-%m-%d")

        # 1차(14:50 KST) 실행은 signals 미생성 → skip
        if dt.hour < 17:
            continue
        # 해당 날짜 파일이 이미 있으면 skip
        if date_str in existing:
            continue

        print(f"  {date_str} 다운로드 중...", end=" ", flush=True)
        with tempfile.TemporaryDirectory() as tmpdir:
            rc, _ = _run([
                "gh", "run", "download", str(run_id),
                "-n", f"bot-output-{run_id}",
                "-D", tmpdir,
            ])
            if rc != 0:
                print("아티팩트 다운로드 실패")
                continue

            src = Path(tmpdir) / "data" / "signals"
            if not src.exists():
                print("signals 없음")
                continue

            # signals CSV + review.json + daily_summary.json 전체 동기화
            new_files = [f for f in src.glob("*") if f.is_file() and not (SIGNALS_DIR / f.name).exists()]
            for f in new_files:
                shutil.copy2(f, SIGNALS_DIR / f.name)
            synced += len(new_files)
            existing.add(date_str)
            print(f"완료 ({len(new_files)}개)")

    # ── 결과 요약 ────────────────────────────────────────────
    if synced:
        print(f"  → 총 {synced}개 신호 파일 추가")
    else:
        print("  → 신규 신호 파일 없음 (이미 최신)")

    all_files = sorted(SIGNALS_DIR.glob("*_signals.csv"))
    if all_files:
        print(f"  신호 범위: {all_files[0].name[:10]} ~ {all_files[-1].name[:10]}")


if __name__ == "__main__":
    main()
