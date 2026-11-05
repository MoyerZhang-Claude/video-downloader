# PyInstaller 打包配置：  .venv\Scripts\pyinstaller build.spec --noconfirm
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = collect_submodules('yt_dlp_ejs') + ['curl_cffi']
# app/fonts：界面英文字体 VDSans / VDSerif（Hanken Grotesk、Source Serif 4 子集，OFL 授权）；界面图标用 QtSvg 渲染
datas = [('app/icon.ico', 'app'), ('app/fonts', 'app/fonts')] + collect_data_files('yt_dlp_ejs')
# 记下自带的 yt-dlp 版本：在线更新过的内核只有比它新时才用（app/core_update.py）
import os  # noqa: E402
import yt_dlp.version as _ytv  # noqa: E402
os.makedirs('build', exist_ok=True)
with open('build/ytdlp_version.txt', 'w', encoding='utf-8') as _f:
    _f.write(_ytv.__version__)
datas.append(('build/ytdlp_version.txt', '.'))

excludes = ['tkinter', 'unittest', 'pydoc', 'PySide6.QtNetwork', 'PySide6.QtQml', 'PySide6.QtQuick',
            'PySide6.QtSql', 'PySide6.QtTest', 'PySide6.QtXml', 'PySide6.QtOpenGL', 'PySide6.QtPdf',
            'PySide6.QtDBus', 'PySide6.QtConcurrent', 'PySide6.QtPrintSupport']

# 网盘解析下载引擎（Go 编写，源码在 pan/；打包.bat 会先编译它）放进 _internal，由 app/pan.py 启动
binaries = [('pan/bin/pan-engine.exe', '.')]

a = Analysis(['main.py'], pathex=['.'], hiddenimports=hiddenimports, datas=datas, binaries=binaries, excludes=excludes)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='链接解析下载器', icon='app/icon.ico',
          console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name='链接解析下载器', upx=False)
