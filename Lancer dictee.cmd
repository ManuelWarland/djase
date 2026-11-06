@echo off
title Dictee locale
cd /d "%~dp0"
".venv\Scripts\python.exe" -X utf8 dictee.py
pause
