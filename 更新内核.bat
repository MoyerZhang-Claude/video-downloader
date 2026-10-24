@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在更新解析内核 yt-dlp ...
".venv\Scripts\python.exe" -m pip install -U --disable-pip-version-check "yt-dlp[default,curl-cffi]"
echo.
echo 完成。重新启动程序后生效（打包版需要再运行一次 打包.bat）。
pause
