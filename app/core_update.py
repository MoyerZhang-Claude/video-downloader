"""解析内核（yt-dlp）更新。电脑版打包版和安卓版共用。

源码版：直接 pip install -U（pip 会把 yt-dlp-ejs 一起升级）。
打包版 / 安卓版：从 PyPI 下载最新的 yt-dlp 和它要求的 yt-dlp-ejs（YouTube 验证脚本，两者版本必须配套），
都是纯 Python 的 wheel 包，核对 sha256 后解压到数据目录的 core_update 文件夹；
启动时 install_override() 让 `import yt_dlp` 优先使用这里的新版本，不需要重新打包。
只有比安装包自带的版本新时才用（装了新版安装包后，旧的更新自动作废）。
新下载的先放在 core_update.next，下次启动时才换上：正在运行的软件已经加载了一部分旧内核，
中途把文件换成新版本会新旧混用（YouTube 验证脚本和内核版本对不上就会解析失败）。

- 通道：稳定版（PyPI 上的正式版）/ 预览版（yt-dlp 的 nightly，以预发布版本 x.y.z.devN 发布在 PyPI 上）。
- 依赖：在线更新只换 yt_dlp 和 yt_dlp_ejs，curl_cffi、websockets、requests 等还是安装包里的版本；
  新内核要求的版本范围和它们对不上时不安装，提示装新版软件（NeedsNewApp）。
- 自检：安装时把每个文件的 sha256 记进 versions.json，第一次导入 yt_dlp 之前核对；
  对不上（文件损坏、被杀毒软件删了一部分、被改过）就删掉更新包，回到内置版本。
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
PENDING_DIR = OVERRIDE_DIR + '.next'          # 已下载、下次启动时换上的新内核
RESET_MARK = OVERRIDE_DIR + '.reset'          # 「恢复为内置版本」：下次启动时删掉更新包
_PACKAGES = ('yt_dlp', 'yt_dlp_ejs')
CHANNELS = {'stable': '稳定版', 'nightly': '预览版（每天更新，网站改版后修得最快，偶尔会有新问题）'}


class NeedsNewApp(RuntimeError):
    """新内核需要的依赖库比软件自带的新：只换内核不够，要装新版软件。"""


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


def _dir_version(folder):
    if not os.path.isfile(os.path.join(folder, 'yt_dlp', '__init__.py')):
        return None
    return _read_version(os.path.join(folder, 'yt_dlp', 'version.py'))


def pending_version():
    """已经下载好、重启后生效的 yt-dlp 版本；没有返回 None。"""
    return _dir_version(PENDING_DIR)


def override_version():
    """在线更新下载的 yt-dlp 版本（包括已下载还没生效的）；没有更新过返回 None。"""
    if os.path.exists(RESET_MARK):
        return pending_version()
    return pending_version() or _dir_version(OVERRIDE_DIR)


def override_active():
    """这次启动用的是不是在线更新的内核"""
    return any(isinstance(f, _OverrideFinder) for f in sys.meta_path)


def _remove_finder():
    sys.meta_path[:] = [f for f in sys.meta_path if not isinstance(f, _OverrideFinder)]


def install_override():
    """程序最开始调用（在导入 yt_dlp 之前）。返回是否使用了在线更新的版本。"""
    if os.path.exists(RESET_MARK):               # 上次点了「恢复为内置版本」
        shutil.rmtree(OVERRIDE_DIR, ignore_errors=True)
        try:
            os.remove(RESET_MARK)
        except OSError:
            pass
    if os.path.isfile(os.path.join(PENDING_DIR, 'versions.json')):      # 上次下载好的新内核：现在换上
        shutil.rmtree(OVERRIDE_DIR, ignore_errors=True)
        try:
            os.replace(PENDING_DIR, OVERRIDE_DIR)
        except OSError:
            pass
    shutil.rmtree(PENDING_DIR, ignore_errors=True)        # 没下完的残留
    if not os.path.isfile(os.path.join(OVERRIDE_DIR, 'yt_dlp', '__init__.py')):
        return False
    ov, bv = _dir_version(OVERRIDE_DIR), bundled_version()
    if ov and bv and _norm(ov) <= _norm(bv):
        # 新装的安装包自带的版本已经不旧了：以前下载的更新作废，免得反而用回旧版
        shutil.rmtree(OVERRIDE_DIR, ignore_errors=True)
        return False
    if not any(isinstance(f, _OverrideFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, _OverrideFinder())
    return True


# ---------------------------------------------------------------- 自检
def _file_hashes(root):
    """root 下 yt_dlp、yt_dlp_ejs 里每个文件的 sha256（不算 Python 自己生成的 __pycache__）。"""
    out = {}
    for pkg in _PACKAGES:
        base = os.path.join(root, pkg)
        for folder, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d != '__pycache__']
            for name in files:
                path = os.path.join(folder, name)
                h = hashlib.sha256()
                with open(path, 'rb') as f:
                    while chunk := f.read(256 * 1024):
                        h.update(chunk)
                out[os.path.relpath(path, root).replace('\\', '/')] = h.hexdigest()
    return out


_verify_result = None


def verify_override(log=None):
    """第一次导入 yt_dlp 之前调用（core.yt()）：核对在线更新的内核和安装时记下的文件哈希。
    对不上就删掉更新包、回到内置版本。返回 True = 可以放心用（或者根本没有更新包）。只查一次。"""
    global _verify_result
    if _verify_result is not None:
        return _verify_result
    if not override_active():
        _verify_result = True
        return True
    problem = ''
    try:
        with open(os.path.join(OVERRIDE_DIR, 'versions.json'), encoding='utf-8') as f:
            want = (json.load(f) or {}).get('files')
        if want is None:
            # 2.5 之前下载的更新包没有记哈希：补记一次（之后才能发现改动）
            _write_manifest(OVERRIDE_DIR)
        else:
            have = _file_hashes(OVERRIDE_DIR)
            bad = [k for k in want if have.get(k) != want[k]] + [k for k in have if k not in want]
            if bad:
                problem = f'{len(bad)} 个文件和安装时不一致（如 {bad[0]}）'
    except (OSError, ValueError) as e:
        problem = f'读不出校验信息（{e}）'
    if problem:
        _remove_finder()
        shutil.rmtree(OVERRIDE_DIR, ignore_errors=True)
        if log:
            log('warning', f'在线更新的解析内核没有通过自检：{problem}。已删除，这次使用软件内置的版本')
    _verify_result = not problem
    return _verify_result


def _write_manifest(root, extra=None):
    path = os.path.join(root, 'versions.json')
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f) or {}
    except (OSError, ValueError):
        data = {}
    data.update(extra or {})
    data['files'] = _file_hashes(root)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f)


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


# ---------------------------------------------------------------- 版本号
_VER_RE = re.compile(r'^\s*v?(\d+(?:\.\d+)*)(?:[._-]?(a|alpha|b|beta|c|rc|pre|preview|dev|post)[._-]?(\d*))?', re.I)
_PHASE = {'dev': -4, 'a': -3, 'alpha': -3, 'b': -2, 'beta': -2, 'c': -1, 'rc': -1, 'pre': -1, 'preview': -1, 'post': 1}


def _norm(v):
    """版本号 → 可以比较大小的元组。认预发布号：2026.11.5.232815.dev0 < 2026.11.5.232815，
    但比上一个正式版 2026.11.5 新；前导零不影响（2026.11.05 == 2026.11.5）。"""
    m = _VER_RE.match(v or '')
    if not m:
        return ((0,), 0, 0)
    release = [int(x) for x in m.group(1).split('.')]
    while len(release) > 1 and release[-1] == 0:
        release.pop()
    return (tuple(release), _PHASE.get((m.group(2) or '').lower(), 0), int(m.group(3) or 0))


def is_prerelease(v):
    return _norm(v)[1] < 0


# ---------------------------------------------------------------- 下载
_UA = {'User-Agent': 'LinkDownloader-core-update'}


def _open(url, timeout):
    return urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=timeout)


def _pypi_json(name, version=None, timeout=15):
    url = f'https://pypi.org/pypi/{name}/{version}/json' if version else f'https://pypi.org/pypi/{name}/json'
    with _open(url, timeout) as r:
        return json.load(r)


def _release_of(data):
    """PyPI 返回的某个版本 → (版本, 依赖列表, wheel 地址, wheel 的 sha256)。"""
    wheel = next((u for u in data['urls'] if u.get('packagetype') == 'bdist_wheel'
                  and u['filename'].endswith('py3-none-any.whl')), None)
    return (data['info']['version'], data['info'].get('requires_dist') or [],
            wheel and wheel['url'], wheel and (wheel.get('digests') or {}).get('sha256'))


def _pypi_release(name, version=None, timeout=15, channel='stable'):
    """PyPI 上某个包的 (版本, 依赖列表, wheel 地址, wheel 的 sha256)。
    不指定版本时：稳定版通道取最新正式版；预览版通道取最新的预发布版本（如果它比正式版新）。"""
    data = _pypi_json(name, version, timeout)
    if version or channel != 'nightly':
        return _release_of(data)
    stable = data['info']['version']
    pre = [v for v, files in (data.get('releases') or {}).items()
           if is_prerelease(v) and files and not all(f.get('yanked') for f in files)]
    newest = max(pre, key=_norm, default=None)
    if not newest or _norm(newest) <= _norm(stable):
        return _release_of(data)
    return _release_of(_pypi_json(name, newest, timeout))


def _ejs_pin(requires):
    """yt-dlp 依赖列表里写死的 yt-dlp-ejs 版本，如 'yt-dlp-ejs==0.8.0; extra == "default"' → '0.8.0'。"""
    for r in requires:
        m = re.match(r'\s*yt[-_]dlp[-_]ejs\s*==\s*([0-9][\w.]*)', r, re.I)
        if m:
            return m.group(1)
    return None


def latest_version(timeout=15, channel='stable'):
    version, _req, wheel, _sha = _pypi_release('yt-dlp', timeout=timeout, channel=channel)
    return version, wheel


# ---------------------------------------------------------------- 依赖兼容检查
# 包名 → 导入名（只查软件里带着的这些；没带的不管）
_DEP_MODULES = {'curl-cffi': 'curl_cffi', 'websockets': 'websockets', 'requests': 'requests', 'urllib3': 'urllib3',
                'certifi': 'certifi', 'brotli': 'brotli', 'mutagen': 'mutagen', 'pycryptodomex': 'Cryptodome'}
_SPEC_RE = re.compile(r'(~=|==|!=|<=|>=|<|>)\s*([\w.*+!-]+)')


def installed_version(dist):
    """软件自带的某个依赖库的版本；查不到返回 None。"""
    try:
        from importlib import metadata
        return metadata.version(dist)
    except Exception:  # noqa: BLE001 - 打包版里没有元数据
        pass
    try:        # 打包时记下的版本（build.spec 写的 bundled_deps.json）
        with open(os.path.join(getattr(sys, '_MEIPASS', ROOT), 'bundled_deps.json'), encoding='utf-8') as f:
            v = (json.load(f) or {}).get(dist)
        if v:
            return v
    except (OSError, ValueError):
        pass
    return None


def _satisfies(version, op, want):
    if want.endswith('.*'):                         # ==1.2.* / !=0.6.*
        prefix = tuple(int(x) for x in re.findall(r'\d+', want))
        rel = _norm(version)[0]
        same = (rel + (0,) * len(prefix))[:len(prefix)] == prefix
        return same if op == '==' else not same if op == '!=' else True
    a, b = _norm(version), _norm(want)
    if op == '~=':                                  # ~=1.4.2 相当于 >=1.4.2, ==1.4.*
        head = tuple(int(x) for x in re.match(r'[\d.]+', want).group(0).strip('.').split('.'))[:-1]
        return a >= b and (a[0] + (0,) * len(head))[:len(head)] == head
    return {'==': a == b, '!=': a != b, '<=': a <= b, '>=': a >= b, '<': a < b, '>': a > b}[op]


def dep_conflicts(requires, get_version=installed_version):
    """新内核的依赖要求（PyPI 的 requires_dist）里，软件自带的版本满足不了的：['websockets 要求 >=15，自带 13.1', …]"""
    out = []
    for req in requires or []:
        spec, _, marker = req.partition(';')
        extra = re.search(r'extra\s*==\s*["\']([\w.-]+)["\']', marker)
        if extra and extra.group(1) not in ('default', 'curl-cffi'):
            continue                                 # 开发 / 打包 / 固定版本等用不到的可选依赖
        m = re.match(r'\s*([A-Za-z0-9][\w.-]*)\s*(?:\[[^\]]*\])?\s*\(?([^)]*)\)?', spec)
        if not m:
            continue
        name = m.group(1).lower().replace('_', '-')
        if name not in _DEP_MODULES:
            continue
        have = get_version(name)
        if not have:
            continue
        clauses = _SPEC_RE.findall(m.group(2))
        if any(not _satisfies(have, op, want) for op, want in clauses):
            out.append(f'{name} 要求 {m.group(2).strip()}，软件自带 {have}')
    return out


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


def update_frozen(progress=None, channel='stable'):
    """下载最新 yt-dlp（和配套的 yt-dlp-ejs），重启后生效。返回 (当前版本, 新版本, 是否有更新)。
    已经下载过同一版本（还没重启）时不重复下载，也算「有更新」。依赖对不上时抛 NeedsNewApp。"""
    old = current_version()
    new, requires, wheel, sha = _pypi_release('yt-dlp', channel=channel)
    known = old not in ('未知', '（加载中）')
    pending = pending_version()
    if pending and _norm(new) <= _norm(pending):
        return old, pending, True
    if known and _norm(new) <= _norm(old):
        return old, new, False
    if not wheel:
        raise RuntimeError('PyPI 上没有找到可用的安装包')
    conflicts = dep_conflicts(requires)
    if conflicts:
        raise NeedsNewApp(f'新内核 {new} 需要更新的组件（{"；".join(conflicts)}），只更新内核不够：请安装新版软件')
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
        _write_manifest(tmp, {'yt-dlp': new, 'yt-dlp-ejs': ejs, 'channel': channel})
        shutil.rmtree(PENDING_DIR, ignore_errors=True)
        os.replace(tmp, PENDING_DIR)            # versions.json 最后写、整个文件夹一次改名：不会留下半截的更新包
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    try:
        os.remove(RESET_MARK)
    except OSError:
        pass
    if progress:
        progress(1.0)
    return old, new, True


def reset_frozen():
    """回到打包时内置的版本（重启后生效）。正在使用的更新包留到下次启动再删，免得这次运行新旧混用。"""
    shutil.rmtree(PENDING_DIR, ignore_errors=True)
    if override_active():
        try:
            with open(RESET_MARK, 'w', encoding='utf-8') as f:
                f.write('1')
            return
        except OSError:
            pass
    shutil.rmtree(OVERRIDE_DIR, ignore_errors=True)
