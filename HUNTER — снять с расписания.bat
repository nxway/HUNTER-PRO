@echo off
chcp 65001 >nul
schtasks /delete /tn "HUNTER-PRO ночной сбор" /f
pause
