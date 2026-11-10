"""YouTube 需要的 JS 运行时（Deno）：按需下载到数据目录，不打进安装包。

yt-dlp 从 2025.11 起解 YouTube 的验证要靠外部 JS 运行时（推荐 Deno）。普通用户的电脑上既没有 Deno 也没有 Node.js，
YouTube 就会少清晰度或者直接失败。Deno 约 40 MB（解压后 110 MB），所以第一次解析 YouTube 时才下载一次：
从 Deno 官方 GitHub Releases 下载 Windows 版 zip，用 GitHub API 给出的 sha256（asset.digest）核对后，
解压到 %APPDATA%\\VideoDownloader\\runtime\\deno.exe；之后每周检查一次新版本。
启动后第一次使用前还会核对 deno.exe 的哈希（数据目录用户可写，文件被换掉就删除重下，不运行来路不明的程序）。
"""
import hashlib
import json
import os
import platform
import re
import threading
import time
import urllib.request
import zipfile

from .config import DATA

RUNTIME_DIR = os.path.join(DATA, 'runtime')
DENO = os.path.join(RUNTIME_DIR, 'deno.exe')
META = os.path.join(RUNTIME_DIR, 'runtime.json')
CHECK_EVERY = 7 * 86400

_REPO = 'denoland/deno'
_API = f'https://api.github.com/repos/{_REPO}/releases/latest'
_UA = {'User-Agent': 'LinkDownloader-runtime', 'Accept': 'application/vnd.github+json'}
_lock = threading.Lock()
_verified = None            # (修改时间, 大小)：这次启动后已经核对过哈希的那个 deno.exe
log = None                  # 可选：log(level, msg)，由界面接到日志页


def _log(level, msg):
    if log:
        try:
            log(level, msg)
        except Exception:  # noqa: BLE001
            pass


def _asset_name():
    arch = 'aarch64' if platform.machine().upper() in ('ARM64', 'AARCH64') else 'x86_64'
    return f'deno-{arch}-pc-windows-msvc.zip'


def supported():
    """只有 Windows 电脑版需要（安卓版用系统 WebView 的 V8）。"""
    return os.name == 'nt' and platform.machine().upper() in ('AMD64', 'X86_64', 'ARM64', 'AARCH64')


def _opener(proxy=''):
    if proxy:
        return urllib.request.build_opener(urllib.request.ProxyHandler({'http': proxy, 'https': proxy}))
    return urllib.request.build_opener()


def _load_meta():
    try:
        with open(META, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_meta(meta):
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    tmp = META + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(meta, f)
    os.replace(tmp, META)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def installed_version():
    return (_load_meta().get('deno') or {}).get('version') if os.path.isfile(DENO) else None


def deno_path():
    """数据目录里可用的 deno.exe；没有、或者和安装时记下的哈希对不上（被改过）时返回 None。"""
    global _verified
    try:
        st = os.stat(DENO)
    except OSError:
        return None
    key = (st.st_mtime, st.st_size)
    if _verified == key:
        return DENO
    with _lock:
        if _verified == key:
            return DENO
        want = (_load_meta().get('deno') or {}).get('sha256')
        try:
            ok = bool(want) and _sha256(DENO) == want
        except OSError:
            ok = False
        if not ok:
            _log('warning', 'YouTube 组件（deno.exe）和安装时的校验值对不上，已删除，下次解析 YouTube 时重新下载')
            try:
                os.remove(DENO)
            except OSError:
                pass
            return None
        _verified = key
    return DENO


def latest(proxy='', timeout=20):
    """GitHub 上最新的 Deno：(版本号, 下载地址, zip 的 sha256, 大小)。"""
    name = _asset_name()
    op = _opener(proxy)
    try:
        with op.open(urllib.request.Request(_API, headers=_UA), timeout=timeout) as r:
            data = json.load(r)
        asset = next((a for a in data.get('assets') or [] if a.get('name') == name), None)
        digest = (asset or {}).get('digest') or ''
        if asset and digest.startswith('sha256:'):
            return ((data.get('tag_name') or '').lstrip('v'), asset['browser_download_url'], digest[7:].lower(),
                    int(asset.get('size') or 0))
    except Exception as e:  # noqa: BLE001 - API 有每小时次数限制，失败时走下面的备用办法
        _log('info', f'GitHub API 没有取到 Deno 的版本信息（{e}），改用发布页的校验文件')
    # 备用：发布页上和 zip 放在一起的 .sha256sum 文件
    base = f'https://github.com/{_REPO}/releases/latest/download/{name}'
    with op.open(urllib.request.Request(base + '.sha256sum', headers=_UA), timeout=timeout) as r:
        text = r.read(4096).decode('utf-8', 'replace')
        final = r.geturl()
    m = re.search(r'\b([0-9a-fA-F]{64})\b', text)
    if not m:
        raise RuntimeError('没有取到 Deno 的校验值')
    ver = re.search(r'/download/v?([\d.]+)/', final)
    return (ver.group(1) if ver else ''), base, m.group(1).lower(), 0


def install(progress=None, proxy=''):
    """下载并安装最新的 Deno。progress(0~1)。校验不通过绝不安装。返回版本号。"""
    version, url, sha, size = latest(proxy)
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    part = os.path.join(RUNTIME_DIR, 'deno.zip.part')
    h = hashlib.sha256()
    try:
        with _opener(proxy).open(urllib.request.Request(url, headers={'User-Agent': _UA['User-Agent']}),
                                 timeout=60) as r, open(part, 'wb') as f:
            total = int(r.headers.get('Content-Length') or 0) or size
            done = 0
            while chunk := r.read(256 * 1024):
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress and total:
                    progress(min(0.98, done / total))
        if h.hexdigest().lower() != sha:
            raise RuntimeError('下载的 YouTube 组件校验不通过（可能下载不完整），请重试')
        tmp_exe = DENO + '.new'
        with zipfile.ZipFile(part) as z, z.open('deno.exe') as src, open(tmp_exe, 'wb') as dst:
            while chunk := src.read(1024 * 1024):
                dst.write(chunk)
        exe_sha = _sha256(tmp_exe)
        os.replace(tmp_exe, DENO)           # 正在被使用时会失败：下次再装
    finally:
        for p in (part, DENO + '.new'):
            try:
                os.remove(p)
            except OSError:
                pass
    meta = _load_meta()
    meta['deno'] = {'version': version, 'sha256': exe_sha, 'checked': time.time()}
    _save_meta(meta)
    global _verified
    _verified = None
    if progress:
        progress(1.0)
    _log('info', f'YouTube 组件（Deno {version}）已安装到 {DENO}')
    return version


_install_lock = threading.Lock()


def ensure(progress=None, proxy=''):
    """没有就下载（同时来的多个解析只下载一次，其余的等它）。返回 deno.exe 的路径。"""
    path = deno_path()
    if path:
        return path
    with _install_lock:
        path = deno_path()
        if not path:
            install(progress, proxy)
            path = deno_path()
    return path


def check_update(proxy='', force=False):
    """装过 Deno 的话每周看一次有没有新版本，有就换上。返回新版本号（没更新返回 None）。"""
    if not supported() or not os.path.isfile(DENO):
        return None
    meta = _load_meta()
    info = meta.get('deno') or {}
    if not force and time.time() - (info.get('checked') or 0) < CHECK_EVERY:
        return None
    with _install_lock:
        version = latest(proxy)[0]
        if version and version != info.get('version'):
            return install(None, proxy)
        info['checked'] = time.time()
        meta['deno'] = info
        _save_meta(meta)
    return None
