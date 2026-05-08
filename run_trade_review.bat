@echo off
chcp 65001 > nul
cd /d "%~dp0"

REM 인수 없으면 --latest --open 기본 실행
IF "%~1"=="" (
    python -m scripts.trade_analyzer --latest --open
) ELSE (
    python -m scripts.trade_analyzer %*
)
pause
