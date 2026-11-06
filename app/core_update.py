"""解析内核（yt-dlp）更新。电脑版打包版和安卓版共用。

源码版：直接 pip install -U（pip 会把 yt-dlp-ejs 一起升级）。
打包版 / 安卓版：从 PyPI 下载最新的 yt-dlp 和它要求的 yt-dlp-ejs（YouTube 验证脚本，两者版本必须配套），
都是纯 Python 的 wheel 包，核对 sha256 后解压到数据目录的 core_update 文件夹；
启动时 install_override() 让 `import yt_dlp` 优先使用这里的新版本，不需要重新打包。
只有比安装包自带的版本新时才用（装了新版安装包后，旧的更新自动作废）。
"""
import hashlib
import importlib.machinery
import json
import os
import re
import shutil
import sys
import urllib.request
import zipfile

from .config import DATA, ROOT

OVERRIDE_DIR = os.path.join(DATA, 'core_update')
_PACKAGES = ('yt_dlp', 'yt_dlp_ejs')


class _OverrideFinder:
    """放在 sys.meta_path 最前面：yt_dlp、yt_dlp_ejs 及其子模块优先从 core_update 文件夹加载（内置版本被覆盖）。"""

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


def _read_version(path):
    try:
        with open(path, encoding='utf-8') as f:
            m = re.search(r"__version__\s*=\s*'([^']+)'", f.read())
        return m.group(1) if m else None
    except OSError:
        return None


def bundled_version():
    """安装包自带的 yt-dlp 版本：电脑版打包时写进 ytdlp_version.txt，安卓版构建时写进 app/_bundled.py。"""
    try:
        from ._bundled import YTDLP
        return YTDLP
    except ImportError:
        pass
    try:
        with open(os.path.join(getattr(sys, '_MEIPASS', ROOT), 'ytdlp_version.txt'), encoding='utf-8') as f:
            return f.read().strip() or None
    except OSError:
        return None


def override_version():
    """在线更新下载的 yt-dlp 版本；没有更新过返回 None。"""
    if not os.path.isfile(os.path.join(OVERRIDE_DIR, 'yt_dlp', '__init__.py')):
        return None
    return _read_version(os.path.join(OVERRIDE_DIR, 'yt_dlp', 'version.py'))


def install_override():
    """程序最开始调用（在导入 yt_dlp 之前）。返回是否使用了在线更新的版本。"""
    if not os.path.isfile(os.path.join(OVERRIDE_DIR, 'yt_dlp', '__init__.py')):
        return False
    ov, bv = override_version(), bundled_version()
    if ov and bv and _norm(ov) <= _norm(bv):
        # 新装的安装包自带的版本已经不旧了：以前下载的更新作废，免得反而用回旧版
        shutil.rmtree(OVERRIDE_DIR, ignore_errors=True)
        return False
    if not any(isinstance(f, _OverrideFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, _OverrideFinder())
    return True


def _version_from_file():
    """不导入 yt_dlp，直接读 version.py（后台线程正在导入 yt_dlp 时，界面线程再导入会触发导入锁死锁）。"""
    import importlib.util
    try:
        spec = importlib.util.find_spec('yt_dlp')
        for d in (spec.submodule_search_locations or []) if spec else []:
            path = os.path.join(d, 'version.py')
            if os.path.isfile(path):
                v = _read_version(path)
                if v:
                    return v
    except Exception:  # noqa: BLE001
        pass
    return None


def current_version(block=True):
    mod = sys.modules.get('yt_dlp.version')
    if getattr(mod, '__version__', None):
        return mod.__version__
    v = _version_from_file()
    if v:
        return v
    if not block and 'yt_dlp' in sys.modules:      # 别的线程正在导入，不要在这里等
        return '（加载中）'
    try:
        from yt_dlp.version import __version__
        return __version__
    except Exception:  # noqa: BLE001
        return '未知'


# ---------------------------------------------------------------- 下载
_UA = {'User-Agent': 'LinkDownloader-core-update'}


def _open(url, timeout):
    return urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=timeout)


def _pypi_release(name, version=None, timeout=15):
    """PyPI 上某个包（默认最新版）的 (版本, 依赖列表, wheel 地址, wheel 的 sha256)。"""
    url = f'https://pypi.org/pypi/{name}/{version}/json' if version else f'https://pypi.org/pypi/{name}/json'
    with _open(url, timeout) as r:
        data = json.load(r)
    wheel = next((u for u in data['urls'] if u.get('packagetype') == 'bdist_wheel'
                  and u['filename'].endswith('py3-none-any.whl')), None)
    return (data['info']['version'], data['info'].get('requires_dist') or [],
            wheel and wheel['url'], wheel and (wheel.get('digests') or {}).get('sha256'))


def _ejs_pin(requires):
    """yt-dlp 依赖列表里写死的 yt-dlp-ejs 版本，如 'yt-dlp-ejs==0.8.0; extra == "default"' → '0.8.0'。"""
    for r in requires:
        m = re.match(r'\s*yt[-_]dlp[-_]ejs\s*==\s*([0-9][\w.]*)', r, re.I)
        if m:
            return m.group(1)
    return None


def latest_version(timeout=15):
    version, _req, wheel, _sha = _pypi_release('yt-dlp', timeout=timeout)
    return version, wheel


def _download(url, sha256, path, progress=None, base=0.0, span=1.0):
    """下载到 path 并核对 sha256（PyPI 给出的校验值）；不一致说明文件损坏或被篡改，直接报错。"""
    h = hashlib.sha256()
    with _open(url, 60) as r, open(path, 'wb') as f:
        total = int(r.headers.get('Content-Length') or 0)
        done = 0
        while chunk := r.read(256 * 1024):
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress and total:
                progress(base + span * done / total)
    if not sha256:
        raise RuntimeError('PyPI 没有提供校验值，为安全起见不安装')
    if h.hexdigest().lower() != sha256.lower():
        raise RuntimeError('下载的文件校验不通过（可能下载不完整），请重试')


def _extract(whl, package, dest):
    with zipfile.ZipFile(whl) as z:
        members = [m for m in z.namelist() if m.startswith(package + '/')]
        if f'{package}/__init__.py' not in members:
            raise RuntimeError('下载的安装包不完整')
        z.extractall(dest, members)


def update_frozen(progress=None):
    """下载并安装最新 yt-dlp（和配套的 yt-dlp-ejs）到 core_update。返回 (旧版本, 新版本, 是否有更新)。"""
    old = current_version()
    new, requires, wheel, sha = _pypi_release('yt-dlp')
    if old not in ('未知', '（加载中）') and _norm(new) <= _norm(old):
        return old, new, False
    if not wheel:
        raise RuntimeError('PyPI 上没有找到可用的安装包')
    jobs = [('yt_dlp', wheel, sha)]
    ejs = _ejs_pin(requires)
    if ejs:
        _v, _r, ejs_wheel, ejs_sha = _pypi_release('yt-dlp-ejs', ejs)
        if not ejs_wheel:
            raise RuntimeError(f'PyPI 上没有找到 yt-dlp-ejs {ejs}')
        jobs.append(('yt_dlp_ejs', ejs_wheel, ejs_sha))
    tmp = OVERRIDE_DIR + '.download'
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    try:
        for i, (pkg, url, digest) in enumerate(jobs):
            whl = os.path.join(tmp, pkg + '.whl')
            # yt-dlp 约 3 MB，ejs 不到 100 KB：进度条按 95% / 5% 分
            base, span = (0.0, 0.95) if i == 0 else (0.95, 0.05)
            _download(url, digest, whl, progress, base, span if len(jobs) > 1 else 1.0)
            _extract(whl, pkg, tmp)
            os.remove(whl)
        with open(os.path.join(tmp, 'versions.json'), 'w', encoding='utf-8') as f:
            json.dump({'yt-dlp': new, 'yt-dlp-ejs': ejs}, f)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    # 原子替换：先把旧的挪开，再把新的放进去
    old_dir = OVERRIDE_DIR + '.old'
    shutil.rmtree(old_dir, ignore_errors=True)
    if os.path.isdir(OVERRIDE_DIR):
        os.replace(OVERRIDE_DIR, old_dir)
    os.replace(tmp, OVERRIDE_DIR)
    shutil.rmtree(old_dir, ignore_errors=True)
    if progress:
        progress(1.0)
    return old, new, True


def reset_frozen():
    """删掉 core_update，回到打包时内置的版本。"""
    shutil.rmtree(OVERRIDE_DIR, ignore_errors=True)


def _norm(v):
    return tuple(int(x) for x in v.replace('-', '.').split('.') if x.isdigit())
