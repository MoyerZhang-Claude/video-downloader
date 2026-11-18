@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "OUT=dist\链接解析下载器"
set "OLD=dist\视频解析下载器"
set "KEEP=打包前的旧数据"
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
rem 2.1 起设置、登录、历史等个人数据都放在 %%APPDATA%%\VideoDownloader，程序文件夹里只有程序。
rem 很旧的版本留在程序文件夹里的数据（如果还有）挪到 dist 外面的「%KEEP%」文件夹，不再拷回新的程序文件夹，
rem 免得被打进免安装版 zip 发布出去。旧版用户第一次打开新版时，软件自己会把旧数据搬到 %%APPDATA%%。
set "SRC=%OUT%"
if not exist "%OUT%\config.json" if exist "%OLD%\config.json" set "SRC=%OLD%"
set "MOVED="
for %%n in (cookies avatar logs) do if exist "%SRC%\%%n" (
    if not exist "%KEEP%" mkdir "%KEEP%"
    xcopy /e /i /y /q "%SRC%\%%n" "%KEEP%\%%n" >nul
    set "MOVED=1"
)
for %%n in (config.json history.db) do if exist "%SRC%\%%n" (
    if not exist "%KEEP%" mkdir "%KEEP%"
    copy /y "%SRC%\%%n" "%KEEP%\%%n" >nul
    set "MOVED=1"
)
if defined MOVED echo 程序文件夹里的旧数据已备份到「%KEEP%」文件夹（不会打进安装包和 zip）
".venv\Scripts\pyinstaller.exe" build.spec --noconfirm --clean
if errorlevel 1 (
    echo 打包失败
    pause
    exit /b 1
)
xcopy /e /i /y /q "ffmpeg" "%OUT%\ffmpeg" >nul
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
rem 免安装版 zip：只打包程序文件，发现个人数据就停下（见 tools\make_portable_zip.py）
".venv\Scripts\python.exe" tools\make_portable_zip.py
if errorlevel 1 (
    echo 没有生成免安装版 zip
    pause
    exit /b 1
)
echo.
echo 完成：%OUT%\链接解析下载器.exe
pause
