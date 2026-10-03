@echo off
cd /d E:\RUANJIAN\memtether
set MEM_DB=E:\RUANJIAN\memory_hub\memory.db
set PYTHONIOENCODING=utf-8
start "" "http://localhost:8820/dashboard"
timeout /t 3 /nobreak >nul
start /min "" "E:\RUANJIAN\memory_hub\.venv-memory\Scripts\pythonw.exe" E:\RUANJIAN\memtether\api_server.py --port 8820
exit