@echo off
REM Ejecuta el sistema en modo desarrollo (requiere Python instalado)
cd /d "%~dp0"
python -m pip install -r requirements.txt
python app.py
pause
