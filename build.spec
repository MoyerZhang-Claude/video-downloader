# PyInstaller 打包配置：  .venv\Scripts\pyinstaller build.spec --noconfirm
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = collect_submodules('yt_dlp_ejs') + ['curl_cffi']
datas = [('app/icon.ico', 'app')] + collect_data_files('yt_dlp_ejs')

excludes = ['tkinter', 'unittest', 'pydoc', 'PySide6.QtNetwork', 'PySide6.QtQml', 'PySide6.QtQuick',
            'PySide6.QtSql', 'PySide6.QtTest', 'PySide6.QtXml', 'PySide6.QtOpenGL', 'PySide6.QtPdf',
            'PySide6.QtSvg', 'PySide6.QtDBus', 'PySide6.QtConcurrent', 'PySide6.QtPrintSupport']

a = Analysis(['main.py'], pathex=['.'], hiddenimports=hiddenimports, datas=datas, excludes=excludes)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='视频解析下载器', icon='app/icon.ico',
          console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name='视频解析下载器', upx=False)
