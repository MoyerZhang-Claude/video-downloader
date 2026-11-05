@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "OUT=dist\链接解析下载器"
set "OLD=dist\视频解析下载器"
set "KEEP=build_keep"
rem dist 里的软件还开着时打包会删到一半失败（正在用的文件删不掉），先检查
rem （只看 dist 里的这一份；装好的安装版开着不影响打包）
powershell -NoProfile -Command "if (Get-Process | Where-Object { $_.Path -like '%CD%\dist\*' }) { exit 1 }" || (
    echo 链接解析下载器还在运行：请先右键托盘图标选「退出」，再重新打包。
    pause
    exit /b 1
)
rem 网盘解析下载引擎（Go）：每次打包都重新编译；没装 Go 时用已有的 pan\bin\pan-engine.exe
where go >nul 2>nul
if errorlevel 1 (
    if not exist "pan\bin\pan-engine.exe" (
        echo 没有找到 Go，也没有编译好的网盘引擎 pan\bin\pan-engine.exe：请先安装 Go（https://go.dev/dl/）
        pause
        exit /b 1
    )
    echo 没有找到 Go，使用已有的 pan\bin\pan-engine.exe
) else (
    pushd pan
    go build -trimpath -ldflags "-s -w" -o bin\pan-engine.exe .\cmd\pan-engine
    if errorlevel 1 (
        popd
        echo 网盘引擎编译失败
        pause
        exit /b 1
    )
    popd
    echo 网盘引擎已编译
)
echo 正在打包，约需 1-2 分钟 ...
rem 2.1 起设置、历史等数据放在 %%APPDATA%%\VideoDownloader；旧版留在程序文件夹里的数据先备份，新版第一次启动时会自动搬过去。
rem 第一次打包新名字时，从旧的「视频解析下载器」文件夹搬过来（旧文件夹保留不动，确认没问题后可以自己删掉）
set "SRC=%OUT%"
if not exist "%OUT%\config.json" if exist "%OLD%\config.json" set "SRC=%OLD%"
if exist "%KEEP%" rmdir /s /q "%KEEP%"
mkdir "%KEEP%"
if exist "%SRC%\cookies" xcopy /e /i /y /q "%SRC%\cookies" "%KEEP%\cookies" >nul
if exist "%SRC%\config.json" copy /y "%SRC%\config.json" "%KEEP%\config.json" >nul
if exist "%SRC%\avatar" xcopy /e /i /y /q "%SRC%\avatar" "%KEEP%\avatar" >nul
if exist "%SRC%\history.db" copy /y "%SRC%\history.db" "%KEEP%\history.db" >nul
if exist "%SRC%\logs" xcopy /e /i /y /q "%SRC%\logs" "%KEEP%\logs" >nul
".venv\Scripts\pyinstaller.exe" build.spec --noconfirm --clean
if errorlevel 1 (
    echo 打包失败，原来的登录信息备份在 %KEEP% 文件夹
    pause
    exit /b 1
)
xcopy /e /i /y /q "ffmpeg" "%OUT%\ffmpeg" >nul
if exist "%KEEP%\cookies" xcopy /e /i /y /q "%KEEP%\cookies" "%OUT%\cookies" >nul
if exist "%KEEP%\config.json" copy /y "%KEEP%\config.json" "%OUT%\config.json" >nul
if exist "%KEEP%\avatar" xcopy /e /i /y /q "%KEEP%\avatar" "%OUT%\avatar" >nul
if exist "%KEEP%\history.db" copy /y "%KEEP%\history.db" "%OUT%\history.db" >nul
if exist "%KEEP%\logs" xcopy /e /i /y /q "%KEEP%\logs" "%OUT%\logs" >nul
rmdir /s /q "%KEEP%"
if exist build rmdir /s /q build
rem 安装包（需要 Inno Setup 6：https://jrsoftware.org/isinfo.php）
set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
for /f "tokens=2 delims='" %%v in ('findstr /b "APP_VERSION" app\__init__.py') do set "VER=%%v"
if exist "%ISCC%" (
    "%ISCC%" /Q /DAppVersion=%VER% installer.iss
    if errorlevel 1 (echo 安装包生成失败) else (echo 安装包：dist\installer\)
) else (
    echo 没有找到 Inno Setup 6，跳过安装包（只生成了绿色版文件夹）
)
echo.
echo 完成：%OUT%\链接解析下载器.exe
if "%SRC%"=="%OLD%" echo 已把旧版「视频解析下载器」的设置、登录信息、头像和下载历史搬到新文件夹；旧文件夹 %OLD% 可以删掉了。
pause
