@echo off
chcp 65001 > nul
cd /d "%~dp0"

IF "%~1"=="" (
    python -m scripts.trade_analyzer --latest
    IF %ERRORLEVEL% EQU 0 (
        python -m scripts.weekly_review_dashboard --latest --open
    )
) ELSE (
    python -m scripts.trade_analyzer %*
    python -m scripts.weekly_review_dashboard --latest
)
pause
