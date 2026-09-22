@echo off
REM ---------------------------------------------------------------
REM Genera el ejecutable de Windows (.exe) del sistema.
REM Requiere Python 3.9+ instalado y en el PATH.
REM ---------------------------------------------------------------
cd /d "%~dp0"

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

set ICONOPT=
if exist "static\img\logo.ico" set ICONOPT=--icon "static\img\logo.ico"

python -m PyInstaller --noconfirm --clean --onefile --name "LiceoItalianoTrilingue" %ICONOPT% ^
  --add-data "templates;templates" ^
  --add-data "static;static" ^
  --collect-all reportlab ^
  --collect-all PIL ^
  --hidden-import "werkzeug.security" ^
  --hidden-import "openpyxl" ^
  app.py

echo.
echo ============================================================
echo  Listo. El ejecutable esta en:  dist\LiceoItalianoTrilingue.exe
echo.
echo  Para instalarlo en otra computadora copie ese archivo .exe
echo  (y, si ya existe, la carpeta "datos" junto a el para llevar
echo   la informacion). La primera vez creara la carpeta "datos".
echo ============================================================
pause
