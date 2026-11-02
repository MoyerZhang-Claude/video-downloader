# PyInstaller 打包配置：  .venv\Scripts\pyinstaller build.spec --noconfirm
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = collect_submodules('yt_dlp_ejs') + ['curl_cffi']
# app/fonts：标题用的衬线字体（思源宋体子集，OFL 授权）；界面图标用 QtSvg 渲染
datas = [('app/icon.ico', 'app'), ('app/fonts', 'app/fonts')] + collect_data_files('yt_dlp_ejs')

excludes = ['tkinter', 'unittest', 'pydoc', 'PySide6.QtNetwork', 'PySide6.QtQml', 'PySide6.QtQuick',
            'PySide6.QtSql', 'PySide6.QtTest', 'PySide6.QtXml', 'PySide6.QtOpenGL', 'PySide6.QtPdf',
            'PySide6.QtDBus', 'PySide6.QtConcurrent', 'PySide6.QtPrintSupport']

a = Analysis(['main.py'], pathex=['.'], hiddenimports=hiddenimports, datas=datas, excludes=excludes)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='视频解析下载器', icon='app/icon.ico',
          console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name='视频解析下载器', upx=False)
