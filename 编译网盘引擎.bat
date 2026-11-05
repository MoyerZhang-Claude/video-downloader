@echo off
chcp 65001 >nul
rem 编译网盘解析下载引擎（Go 编写，源码在 pan 文件夹）：生成 pan\bin\pan-engine.exe
rem 需要 Go 1.26 以上：https://go.dev/dl/
where go >nul 2>nul || (
    echo 没有找到 Go，请先安装 Go：https://go.dev/dl/
    exit /b 1
)
pushd "%~dp0pan"
go build -trimpath -ldflags "-s -w" -o bin\pan-engine.exe .\cmd\pan-engine
set "ERR=%errorlevel%"
popd
if not "%ERR%"=="0" (
    echo 网盘引擎编译失败
    exit /b 1
)
echo 网盘引擎已编译：pan\bin\pan-engine.exe
exit /b 0
