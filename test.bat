@echo off
REM Cornflakes Media — Windows Automated Test Runner
echo ======================================================================
echo 🌽 CORNFLAKES MEDIA — RUNNING AUTOMATED TEST SUITE (33 TESTS)
echo ======================================================================

IF EXIST venv\Scripts\python.exe (
    venv\Scripts\python.exe -m pytest tests\ -v
) ELSE (
    python -m pytest tests\ -v
)
pause
