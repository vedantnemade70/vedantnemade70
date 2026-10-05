@echo off
rem Makes a separate copy of MetaTrader 5 for the copier bot, so the MT5 you
rem already use keeps running on its own account and is never switched.
rem
rem Usage:  setup_mt5_copy.bat ["C:\Program Files\Your Broker MT5"]
rem Default source is "C:\Program Files\MetaTrader 5". The copy goes to C:\MT5-Copier.

setlocal
set "SRC=%~1"
if "%SRC%"=="" set "SRC=C:\Program Files\MetaTrader 5"
set "DST=C:\MT5-Copier"

if not exist "%SRC%\terminal64.exe" (
    echo Could not find "%SRC%\terminal64.exe".
    echo Pass your MT5 install folder, e.g.  setup_mt5_copy.bat "C:\Program Files\XM Global MT5"
    exit /b 1
)

echo Copying "%SRC%" to "%DST%" ...
rem Copy only the program files; the existing install keeps its accounts and settings in AppData.
robocopy "%SRC%" "%DST%" /E /XD logs Bases /NFL /NDL /NJH /NJS /NP >nul
if %ERRORLEVEL% GEQ 8 (
    echo Copy failed.
    exit /b 1
)

echo Starting the bot's own MT5 in portable mode ...
start "" "%DST%\terminal64.exe" /portable
echo.
echo In the NEW MT5 window: File ^> Open an Account ^> pick a demo server
echo (e.g. MetaQuotes-Demo or your broker's demo) ^> "Open a demo account".
echo Write down the Login, Password and Server, and put them in .env.
endlocal
