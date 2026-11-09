"""安卓端的谷歌云盘：直接用电脑版的 app/gdrive.py（登录、建文件夹、断点续传、上传队列都一样）。

和电脑版不同的地方：
- 登录：打开系统浏览器授权，谷歌跳回 127.0.0.1 上 App 自己开的临时网页（和电脑版同一个「桌面应用」密钥）；
- 文件：下载的视频存在「下载/链接解析下载器」，Kotlin 把 content:// 换成真实路径再传进来（App 自己的文件可以按路径读）；
- 删除本地文件：交给 Kotlin 移到系统回收站（安卓 11 起），做不到就保留，不会直接删掉；
- 进度：Kotlin 每秒问一次 summary()。
"""
import json
import os
import time

_drive = None
_uploader = None
_trash = None


def _gd():
    from app import gdrive
    return gdrive


def setup(trash_hook=None):
    """App 启动时调用（init 之后）。trash_hook.trash(path) -> bool：把文件移到系统回收站。"""
    global _trash
    if trash_hook is not None:
        _trash = trash_hook
        _gd().to_recycle_bin = _to_trash
    _activate()


def _to_trash(path):
    try:
        return bool(_trash.trash(path)) if _trash else False
    except Exception:  # noqa: BLE001
        return False


def _activate():
    global _drive, _uploader
    g = _gd()
    if _uploader is not None or not g.available():
        return
    _drive = g.Drive()
    _uploader = g.Uploader(_drive)


def state():
    g = _gd()
    auth = _drive.auth if _drive else None
    return json.dumps({'has_key': g.available(), 'logged_in': bool(auth and auth.logged_in),
                       'email': auth.email if auth else '', 'pending': _uploader.pending() if _uploader else 0})


def import_key(path):
    err = _gd().import_client(path)
    if not err and _drive is not None:
        _drive.auth.logout()
        _drive.auth = _gd().Auth(_drive.session)
    if not err:
        _activate()
    return json.dumps({'error': err})


def login(opener):
    """阻塞：打开浏览器授权，等用户点完。opener.open(url) 由 Kotlin 打开浏览器。"""
    try:
        _drive.auth.login(open_browser=lambda url: opener.open(url))
        user = _drive.account().get('user') or {}
        email = user.get('emailAddress') or user.get('displayName') or ''
        _drive.auth.set_email(email)
        _uploader.wake()
        return json.dumps({'email': email})
    except Exception as e:  # noqa: BLE001
        return json.dumps({'error': str(e) or type(e).__name__})


def logout():
    if _drive:
        _drive.auth.logout()


def _sidecars(path):
    """同名的字幕、封面：手机版字幕在旁边的「字幕」文件夹里"""
    g = _gd()
    out = g.sidecars(path)
    d, base = os.path.split(path)
    stem = os.path.splitext(base)[0]
    subs = os.path.join(d, '字幕')
    try:
        out += [os.path.join(subs, n) for n in sorted(os.listdir(subs)) if n.startswith(stem + '.')]
    except OSError:
        pass
    return out


def add_video(path, platform, ts, title, group, keep_local):
    """上传一个视频（连同字幕等附属文件）。返回加入队列的文件数。"""
    if not _uploader or not path or not os.path.isfile(path):
        return 0
    folder = _gd().video_folder(platform, ts or time.time())
    n = 0
    for p in [path] + _sidecars(path):
        if _uploader.add(p, folder, title, group, bool(keep_local)):
            n += 1
    return n


def add_files(paths_json, provider_name, ts, save_dir, title, group, keep_local):
    """上传一批网盘文件（保留分享里的目录结构）。返回加入队列的文件数。"""
    if not _uploader:
        return 0
    g = _gd()
    n = 0
    for p in json.loads(paths_json):
        if not os.path.isfile(p):
            continue
        rel = os.path.relpath(os.path.dirname(p), save_dir) if save_dir else ''
        folder = g.pan_folder(provider_name, ts or time.time(), '' if rel.startswith('..') else rel)
        if _uploader.add(p, folder, title, group, bool(keep_local)):
            n += 1
    return n


def summary():
    """每个上传组的状态：{group: {state, done, total, ndone, n, error}}，以及还没传完的个数"""
    if not _uploader:
        return json.dumps({'groups': {}, 'pending': 0})
    titles = {}
    for j in list(_uploader.jobs.values()):
        titles.setdefault(j['group'], j.get('title') or '')
    out = {}
    for gname, title in titles.items():
        st = _uploader.group_state(gname)
        if st:
            out[gname] = {**dict(zip(('state', 'done', 'total', 'ndone', 'n', 'error'), st)), 'title': title}
    return json.dumps({'groups': out, 'pending': _uploader.pending()})


def uploaded(path):
    info = _uploader.uploaded_info(path) if _uploader and path else None
    return info['id'] if info else ''


def retry(group):
    if _uploader:
        for j in _uploader.group_jobs(group):
            if j['state'] == 'failed':
                _uploader.retry(j['id'])


def guide():
    from app import cloud_guide as c
    return json.dumps({'intro': c.INTRO, 'steps': c.STEPS, 'faq': c.FAQ}, ensure_ascii=False)
