"""解析内核（yt-dlp）更新。

源码版：直接 pip install -U。
打包版：从 PyPI 下载最新的 yt-dlp（纯 Python 的 wheel 包），解压到 exe 旁边的 core_update 文件夹；
启动时 install_override() 让 `import yt_dlp` 优先使用这里的新版本，不需要重新打包。
"""
import importlib.machinery
import json
import os
import shutil
import sys
import urllib.request
import zipfile

from .config import ROOT

OVERRIDE_DIR = os.path.join(ROOT, 'core_update')
_PACKAGES = ('yt_dlp',)


class _OverrideFinder:
    """放在 sys.meta_path 最前面：yt_dlp 及其子模块优先从 core_update 文件夹加载（打包版的内置版本被覆盖）。"""

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] not in _PACKAGES:
            return None
        search = [OVERRIDE_DIR] if '.' not in fullname else path
        if not search:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, search)
        if spec and spec.origin and os.path.abspath(spec.origin).startswith(os.path.abspath(OVERRIDE_DIR)):
            return spec
        return None


def install_override():
    """程序最开始调用（在导入 yt_dlp 之前）。"""
    if os.path.isfile(os.path.join(OVERRIDE_DIR, 'yt_dlp', '__init__.py')):
        if not any(isinstance(f, _OverrideFinder) for f in sys.meta_path):
            sys.meta_path.insert(0, _OverrideFinder())
        return True
    return False


def current_version():
    try:
        from yt_dlp.version import __version__
        return __version__
    except Exception:  # noqa: BLE001
        return '未知'


def latest_version(timeout=15):
    with urllib.request.urlopen('https://pypi.org/pypi/yt-dlp/json', timeout=timeout) as r:
        data = json.load(r)
    version = data['info']['version']
    wheel = next((u['url'] for u in data['urls'] if u.get('packagetype') == 'bdist_wheel'
                  and u['filename'].endswith('py3-none-any.whl')), None)
    return version, wheel


def _norm(v):
    return tuple(int(x) for x in v.replace('-', '.').split('.') if x.isdigit())


def update_frozen(progress=None):
    """下载并安装最新 yt-dlp 到 core_update。返回 (旧版本, 新版本, 是否有更新)。"""
    old = current_version()
    new, wheel = latest_version()
    if old != '未知' and _norm(new) <= _norm(old):
        return old, new, False
    if not wheel:
        raise RuntimeError('PyPI 上没有找到可用的安装包')
    tmp = OVERRIDE_DIR + '.download'
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    whl = os.path.join(tmp, 'yt_dlp.whl')
    with urllib.request.urlopen(wheel, timeout=60) as r, open(whl, 'wb') as f:
        total = int(r.headers.get('Content-Length') or 0)
        done = 0
        while chunk := r.read(256 * 1024):
            f.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(done / total)
    with zipfile.ZipFile(whl) as z:
        members = [m for m in z.namelist() if m.startswith('yt_dlp/')]
        if not any(m == 'yt_dlp/__init__.py' for m in members):
            raise RuntimeError('下载的安装包不完整')
        z.extractall(tmp, members)
    os.remove(whl)
    # 原子替换：先把旧的挪开，再把新的放进去
    old_dir = OVERRIDE_DIR + '.old'
    shutil.rmtree(old_dir, ignore_errors=True)
    if os.path.isdir(OVERRIDE_DIR):
        os.replace(OVERRIDE_DIR, old_dir)
    os.replace(tmp, OVERRIDE_DIR)
    shutil.rmtree(old_dir, ignore_errors=True)
    return old, new, True


def reset_frozen():
    """删掉 core_update，回到打包时内置的版本。"""
    shutil.rmtree(OVERRIDE_DIR, ignore_errors=True)
