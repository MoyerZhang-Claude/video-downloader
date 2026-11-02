@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "OUT=dist\视频解析下载器"
set "KEEP=build_keep"
rem 软件还开着时打包会删到一半失败（正在用的文件删不掉），先检查：日志文件改不了名就说明还在运行
if exist "%OUT%\logs\app.log" (
    ren "%OUT%\logs\app.log" app.log.chk 2>nul || (
        echo 视频解析下载器还在运行：请先右键托盘图标选「退出」，再重新打包。
        pause
        exit /b 1
    )
    ren "%OUT%\logs\app.log.chk" app.log
)
echo 正在打包，约需 1-2 分钟 ...
rem 打包会清空输出目录，先备份登录信息、设置、下载历史和日志
if exist "%KEEP%" rmdir /s /q "%KEEP%"
mkdir "%KEEP%"
if exist "%OUT%\cookies" xcopy /e /i /y /q "%OUT%\cookies" "%KEEP%\cookies" >nul
if exist "%OUT%\config.json" copy /y "%OUT%\config.json" "%KEEP%\config.json" >nul
if exist "%OUT%\history.db" copy /y "%OUT%\history.db" "%KEEP%\history.db" >nul
if exist "%OUT%\logs" xcopy /e /i /y /q "%OUT%\logs" "%KEEP%\logs" >nul
".venv\Scripts\pyinstaller.exe" build.spec --noconfirm --clean
if errorlevel 1 (
    echo 打包失败，原来的登录信息备份在 %KEEP% 文件夹
    pause
    exit /b 1
)
xcopy /e /i /y /q "ffmpeg" "%OUT%\ffmpeg" >nul
if exist "%KEEP%\cookies" xcopy /e /i /y /q "%KEEP%\cookies" "%OUT%\cookies" >nul
if exist "%KEEP%\config.json" copy /y "%KEEP%\config.json" "%OUT%\config.json" >nul
if exist "%KEEP%\history.db" copy /y "%KEEP%\history.db" "%OUT%\history.db" >nul
if exist "%KEEP%\logs" xcopy /e /i /y /q "%KEEP%\logs" "%OUT%\logs" >nul
rmdir /s /q "%KEEP%"
if exist build rmdir /s /q build
echo.
echo 完成：%OUT%\视频解析下载器.exe
pause
