# 网盘下载核心（pan-engine）

原来的 [PanDL 网盘下载器](https://github.com/MoyerZhang-Claude/cloud-drive-link-parsing) 的核心部分（Go 编写），合并进「链接解析下载器」后，
编译成后台进程 `pan-engine.exe`，由电脑版界面（`app/pan.py`）启动和调用。原项目的 Wails 外壳和 Vue 界面没有搬过来。

- `internal/`：和 PanDL 相同——core（串起链接识别、分享解析、下载队列）、share（链接识别）、engine（多连接下载、续传、校验）、
  queue（下载队列）、provider（百度网盘 / 夸克网盘 / 123云盘 / Mega / 直链）、account（DPAPI 加密保存账号）
- `cmd/pan-engine/`：给界面用的后台进程。标准输入一行一个 JSON 请求，标准输出一行一个回复或事件，详见 main.go 开头。
- `cmd/pandl-cli/`、`cmd/pandl-check/`、`cmd/speedtest/`：开发用的命令行工具（用真实分享测试解析和下载）。
- `docs/`：PanDL 的设计方案、各轮交接记录和阶段验收（里面提到的 Wails 外壳、安卓版是原项目的内容）。

编译（需要 Go 1.26+）：在项目根目录运行 `编译网盘引擎.bat`，或

```bash
cd pan
go build -trimpath -ldflags "-s -w" -o bin/pan-engine.exe ./cmd/pan-engine
go test ./internal/...
```

安卓版：Gradle 的 `buildPanEngine` 任务用 Android NDK 把同一份代码编译成 `libpanengine.so`（其实是可执行程序，
放进 jniLibs 才会被安装到可以执行的目录），App 用 `ProcessBuilder` 启动它，协议和电脑版完全一样。
安卓没有 DPAPI：App 用 Android Keystore 保管一个随机密钥，启动引擎时通过环境变量 `PAN_ENGINE_KEY` 传入，引擎用它（AES-GCM）加密账号。

电脑版数据目录：`%APPDATA%\VideoDownloader\pan`（tasks.json 任务列表、accounts.dat 加密的网盘账号、settings.json 网盘下载设置）。
