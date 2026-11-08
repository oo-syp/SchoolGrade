@echo off
rem ============================================================
rem  Сборка SchoolGrade.exe (один файл) для Windows 7 - 11
rem  Требуется Python 3.8.10 (лучше 32-битный - работает и на x86, и на x64)
rem  https://www.python.org/downloads/release/python-3810/
rem
rem  Без интернета: положите файлы .whl в папку wheels\ (см. README).
rem  Если мешает прокси: set SG_NO_PROXY=1 перед запуском.
rem ============================================================
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3.8) else (set PY=python)

%PY% -c "import sys; assert sys.version_info[:2]==(3,8), sys.version" 2>nul
if errorlevel 1 (
  echo [!] Нужен Python 3.8 - последняя версия, поддерживающая Windows 7.
  echo     Если собрать на более новом Python, exe не запустится на Windows 7.
  echo     Продолжить всё равно? Ctrl+C - отмена.
  pause
)

if not exist .venv (
  echo Создание виртуального окружения...
  %PY% -m venv .venv || goto :err
)
call .venv\Scripts\activate.bat

python -c "import struct,sys; sys.exit(0 if struct.calcsize('P')==4 else 1)"
if errorlevel 1 (
  echo [i] Python 64-битный: exe будет работать только на 64-битных Windows.
  echo     Для 32-битных Windows 7 соберите на 32-битном Python 3.8.10.
)

rem --- прокси: старый pip не понимает адрес прокси вида https://...
if "%SG_NO_PROXY%"=="1" (
  set HTTP_PROXY=
  set HTTPS_PROXY=
  set NO_PROXY=*
) else (
  python tools\fix_proxy.py > "%TEMP%\sg_proxy.cmd"
  call "%TEMP%\sg_proxy.cmd"
)

if exist wheels\*.whl (
  echo Установка из локальной папки wheels\ ...
  python -m pip install --no-index --find-links wheels -r requirements-dev.txt || goto :pipfail
) else (
  echo Обновление pip...
  python -m pip install --upgrade "pip<25" || echo [!] pip не обновился, пробуем дальше...
  python -m pip install -r requirements-dev.txt || goto :pipfail
)

rem прокси нужен был только для pip
set HTTP_PROXY=
set HTTPS_PROXY=
set NO_PROXY=127.0.0.1,localhost

echo Проверка (юнит-тесты)...
python -m unittest discover tests || goto :err

echo Сборка...
if exist build rmdir /s /q build
if exist dist\SchoolGrade.exe del /q dist\SchoolGrade.exe
pyinstaller --noconfirm --clean SchoolGrade.spec || goto :err

echo.
echo ============================================================
echo  Готово: dist\SchoolGrade.exe
echo  Скопируйте exe в отдельную папку на компьютере-сервере.
echo  Рядом будет создана папка SchoolGrade_data с базой.
echo ============================================================
pause
exit /b 0

:pipfail
echo.
echo [ОШИБКА] Не удалось скачать PyInstaller.
echo  1) Отключите VPN / прокси (Параметры - Сеть - Прокси) и запустите снова, или
echo  2) выполните: set SG_NO_PROXY=1  и затем build.bat, или
echo  3) на другом компьютере скачайте файлы и положите в папку wheels\ :
echo     py -3.8 -m pip download pyinstaller==5.13.2 -d wheels
pause
exit /b 1

:err
echo.
echo [ОШИБКА] Сборка не выполнена.
pause
exit /b 1
