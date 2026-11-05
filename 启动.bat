@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 第一次运行源码版时先编译网盘引擎（需要 Go）
if not exist "pan\bin\pan-engine.exe" (
    pushd pan
    go build -trimpath -ldflags "-s -w" -o bin\pan-engine.exe .\cmd\pan-engine
    popd
)
start "" ".venv\Scripts\pythonw.exe" "main.py"
