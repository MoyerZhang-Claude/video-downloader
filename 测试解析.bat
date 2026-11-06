@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 解析自检：把 test.txt 里的链接逐个解析，并试着下载每个视频开头的一小段（不保存）
rem 也可以把别的链接文件拖到这个 bat 上
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" tools\check_links.py %*
echo.
pause
