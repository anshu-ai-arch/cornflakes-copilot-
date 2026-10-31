@echo off
REM Cornflakes Media — Windows Chaos Injection Suite Runner
echo ======================================================================
echo 🌽 CORNFLAKES MEDIA — RUNNING CHAOS SUITE (TRAPS T1, T2, T3, T5, T12)
echo ======================================================================

IF EXIST venv\Scripts\python.exe (
    venv\Scripts\python.exe scripts\chaos.py
) ELSE (
    python scripts\chaos.py
)
pause
