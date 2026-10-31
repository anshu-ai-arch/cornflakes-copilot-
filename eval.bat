@echo off
REM Cornflakes Media — Windows Evaluation Benchmark Runner
echo ======================================================================
echo 🌽 CORNFLAKES MEDIA — 60-SCENARIO EVALUATION BENCHMARK
echo ======================================================================

IF EXIST venv\Scripts\python.exe (
    venv\Scripts\python.exe -m eval.evaluator
) ELSE (
    python -m eval.evaluator
)
pause
