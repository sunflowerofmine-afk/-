@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo ============================================================
echo   Jongbe Weekly Trade Review
echo ============================================================
echo.

:: [1] git pull
echo [1/4] git pull...
git pull
echo.

:: [2] signals sync (GitHub Actions artifacts)
::   data/signals/ is in .gitignore, so we fetch from gh artifacts
echo [2/4] Syncing signals + backfilling reviews...
python -m scripts.sync_signals
if %ERRORLEVEL% NEQ 0 (
    echo   [warn] sync_signals error - continuing anyway
)
python -m scripts.backfill_reviews
if %ERRORLEVEL% NEQ 0 (
    echo   [warn] backfill_reviews error - continuing anyway
)
echo.

:: [3] HTS CSV check
echo [3/4] Checking HTS trade CSV...
dir /b /od "data\weekly_trading_review\*.csv" 2>nul | findstr /r "." > nul
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo   [ERROR] No CSV found in data\weekly_trading_review\
    echo.
    echo   Please export from HTS (Youngwoong S#):
    echo     Account > Orders > Period Order Details (CSV)
    echo   Then place the file in:
    echo     %~dp0data\weekly_trading_review\
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%f in ('dir /b /od "data\weekly_trading_review\*.csv"') do set LATEST_CSV=%%f
echo   Target: %LATEST_CSV%
echo.

:: [4] Trade analysis + compliance dashboard
echo [4/5] Running trade analysis and compliance dashboard...
python -m scripts.trade_analyzer --latest
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo   [ERROR] trade_analyzer failed - check errors above
    pause
    exit /b 1
)
echo.

python -m scripts.weekly_review_dashboard --latest --open
if %ERRORLEVEL% NEQ 0 (
    echo   [warn] dashboard build error - check existing file
)
echo.

:: [5] System win-rate backtest
::   Runs locally where synced signals are available.
::   GitHub Actions cannot do this (review.json not persisted between runs).
echo [5/5] Running system win-rate backtest...
python -m scripts.weekly_backtest --no-notify
if %ERRORLEVEL% NEQ 0 (
    echo   [warn] weekly_backtest failed - check errors above
)

echo.
echo ============================================================
echo   Done!
echo   - Compliance dashboard : %~dp0reports\trade_reviews\weekly_dashboard.html
echo   - Win-rate backtest    : %~dp0reports\weekly_backtest\
echo ============================================================
echo.
pause
