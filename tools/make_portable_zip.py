"""打包.bat 的最后一步：把 dist\\链接解析下载器 打成免安装版 zip（dist\\installer\\LinkDownloader-版本-windows.zip）。

zip 里只能有程序文件。设置、登录信息、下载历史、日志、头像这类个人数据从 2.1 起都放在 %APPDATA%\\VideoDownloader，
程序文件夹里本来不该有；万一有（比如手动拷进去过），这里会列出来并且不生成 zip，免得发布出去。

用法：.venv\\Scripts\\python.exe tools\\make_portable_zip.py
"""
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOLDER_NAME = '链接解析下载器'

# 个人数据：文件名 / 文件夹名（不区分大小写）
_PERSONAL_FILES = re.compile(r'^(config\.json|history\.db(-journal|-wal|-shm)?|tasks\.json|retry_urls\.json|comment_exports\.json'
                             r'|token\.bin|client\.json|accounts\.dat|bilibili[^/\\]*\.(bin|txt|json)|[^/\\]+\.log)$', re.I)
_PERSONAL_DIRS = {'cookies', 'logs', 'avatar', 'core_update', 'gdrive', 'pan', 'browser_profile'}


def find_personal(folder):
    """folder 里看起来像个人数据的文件 / 文件夹（相对路径列表）"""
    hits = []
    for dirpath, dirnames, filenames in os.walk(folder):
        rel_dir = os.path.relpath(dirpath, folder)
        in_internal = rel_dir.split(os.sep)[0] == '_internal'
        for d in list(dirnames):
            if not in_internal and d.lower() in _PERSONAL_DIRS:
                hits.append(os.path.normpath(os.path.join(rel_dir, d)) + os.sep)
                dirnames.remove(d)
        for f in filenames:
            if _PERSONAL_FILES.match(f) and not in_internal:
                hits.append(os.path.normpath(os.path.join(rel_dir, f)))
    return hits


def version():
    with open(os.path.join(ROOT, 'app', '__init__.py'), encoding='utf-8') as f:
        m = re.search(r"APP_VERSION\s*=\s*'([^']+)'", f.read())
    return m.group(1) if m else '0'


def make_zip(folder, out_path):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp = out_path + '.tmp'
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for dirpath, _dirnames, filenames in os.walk(folder):
            for f in sorted(filenames):
                p = os.path.join(dirpath, f)
                z.write(p, os.path.join(FOLDER_NAME, os.path.relpath(p, folder)))
    os.replace(tmp, out_path)


def main():
    folder = os.path.join(ROOT, 'dist', FOLDER_NAME)
    if not os.path.isfile(os.path.join(folder, FOLDER_NAME + '.exe')):
        print(f'没有找到 {folder}\\{FOLDER_NAME}.exe：先打包')
        return 1
    hits = find_personal(folder)
    if hits:
        print('程序文件夹里有个人数据，没有生成 zip（请挪走后重新运行打包）：')
        for h in hits:
            print('  ' + h)
        return 1
    out = os.path.join(ROOT, 'dist', 'installer', f'LinkDownloader-{version()}-windows.zip')
    make_zip(folder, out)
    print(f'免安装版：{os.path.relpath(out, ROOT)}（{os.path.getsize(out) / 1048576:.0f} MB）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
