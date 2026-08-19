@echo off
REM ===========================================================================
REM  Kimlik bilgisi guvenlik denetimi.
REM  Cift tiklayarak calistir. Sifre ekrana yazilmaz.
REM ===========================================================================
cd /d "%~dp0"
title BIST Takip - Guvenlik Denetimi

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   Sanal ortam bulunamadi.
    echo   Once "BIST Takip" kisayolunu bir kez calistir.
    echo.
    pause
    exit /b 1
)

.venv\Scripts\python.exe app.py --check-secrets

echo.
echo   Kapatmak icin bir tusa bas...
pause >nul
