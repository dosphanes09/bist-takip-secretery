@echo off
REM ===========================================================================
REM  BIST Takip - tek tikla baslatici
REM
REM  Bu dosya yalnizca bir sarmalayicidir; tum mantik scripts\launcher.ps1
REM  icindedir. PowerShell'i -ExecutionPolicy Bypass ile cagirmak, sistemin
REM  betik calistirma kisitina takilmadan calismasini saglar (bu ayar yalnizca
REM  bu surec icin gecerlidir, sistem ayarini degistirmez).
REM ===========================================================================
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launcher.ps1" %*
if errorlevel 1 (
    echo.
    echo Bir hata olustu. Pencereyi kapatmak icin bir tusa bas...
    pause >nul
)
