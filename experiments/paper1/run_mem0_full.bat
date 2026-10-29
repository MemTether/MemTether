@echo off
cd /d E:\RUANJIAN\memory_hub
set HF_HOME=E:\Temp\hf_cache
set HF_ENDPOINT=https://hf-mirror.com
echo Starting mem0 full 500Q experiment (resumable)...
echo Press Ctrl+C to stop - progress is saved after each batch.
echo.
.venv-memory\Scripts\python.exe experiments\paper1\mem0_full_concurrent.py
pause
