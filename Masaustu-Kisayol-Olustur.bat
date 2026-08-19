@echo off
REM ===========================================================================
REM  Masaustune "BIST Takip" kisayolu olusturur.
REM  Bir kez calistirmak yeterlidir.
REM ===========================================================================
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\create-shortcut.ps1"
