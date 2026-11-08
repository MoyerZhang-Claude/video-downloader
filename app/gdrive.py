"""谷歌云盘备份：把下载好的视频 / 网盘文件上传到自己的 Google Drive。

只有数据目录里放了 OAuth 客户端文件（gdrive\\client.json，在 Google Cloud 控制台创建「桌面应用」客户端后下载）
时这个功能才会出现；发布版不带这个文件，其他用户看不到。

- 登录：浏览器里授权一次（本机回环地址接收授权码，PKCE）。权限只申请 drive.file：
  软件只能看到、管理自己上传的文件和文件夹，碰不到云盘里的其它东西。
  refresh token 用 Windows DPAPI 加密保存（只有当前 Windows 用户能解开）。
- 文件夹：链接解析下载器 / 视频分区 / 平台 / 年-月；链接解析下载器 / 网盘分区 / 网盘名 / 年-月 / 分享里的目录。
- 上传：断点续传（resumable upload），每块 16 MB；断网、关掉软件后下次从断点接着传。
  云盘同一文件夹里已有同名同大小的文件时直接跳过。
- 上传完不保留本地文件时，本地文件移到回收站（不是彻底删除）。
"""
import base64
import ctypes
import hashlib
import http.server
import json
import mimetypes
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser

from .config import DATA

DIR = os.path.join(DATA, 'gdrive')
CLIENT_PATH = os.path.join(DIR, 'client.json')        # Google Cloud 下载的 OAuth 客户端（桌面应用）
TOKEN_PATH = os.path.join(DIR, 'token.bin')           # 加密的登录凭据
QUEUE_PATH = os.path.join(DIR, 'queue.json')          # 没传完的任务
UPLOADED_PATH = os.path.join(DIR, 'uploaded.json')    # 已上传的本地文件 → 云盘文件
FOLDERS_PATH = os.path.join(DIR, 'folders.json')      # 云盘文件夹路径 → ID 缓存

SCOPE = 'https://www.googleapis.com/auth/drive.file'
API = 'https://www.googleapis.com/drive/v3'
UPLOAD_API = 'https://www.googleapis.com/upload/drive/v3/files'
FOLDER_MIME = 'application/vnd.google-apps.folder'
CHUNK = 16 * 1024 * 1024            # 必须是 256 KB 的整数倍
ROOT_NAME = '链接解析下载器'
VIDEO_DIR, PAN_DIR = '视频分区', '网盘分区'
OTHER_SITES = '其他网站'


def available():
    """这台电脑有没有配置谷歌云盘（有 OAuth 客户端文件）"""
    return os.path.isfile(CLIENT_PATH)


def _load_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


# ---------------------------------------------------------------- DPAPI（只有当前 Windows 用户能解开）
class _Blob(ctypes.Structure):
    _fields_ = [('cbData', ctypes.c_uint32), ('pbData', ctypes.POINTER(ctypes.c_char))]


def _dpapi(data, protect):
    if os.name != 'nt':
        return data
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    src, out = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), _Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(out)):    # CRYPTPROTECT_UI_FORBIDDEN
        raise OSError('登录信息加密 / 解密失败')
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


# ---------------------------------------------------------------- 文件夹规则
def month_of(ts):
    return time.strftime('%Y-%m', time.localtime(ts or time.time()))


def _safe(name):
    name = ''.join('_' if ch in '/\\' else ch for ch in (name or '')).strip()
    return name or OTHER_SITES


def platform_folder(platform):
    """解析结果的平台名 → 视频分区下的文件夹名。
    认得的网站 yt-dlp 都有专门的解析器，平台名可靠；通用解析器（「网页」）解析的野网站统一放「其他网站」，免得建错文件夹。"""
    p = (platform or '').strip()
    if not p or p in ('网页', 'Generic', 'generic', 'HTML5MediaEmbed'):
        return OTHER_SITES
    if p == 'B站番剧':
        return 'B站'
    return _safe(p)


def video_folder(platform, ts):
    return [ROOT_NAME, VIDEO_DIR, platform_folder(platform), month_of(ts)]


def pan_folder(provider_name, ts, rel_dir=''):
    parts = [p for p in (rel_dir or '').replace('\\', '/').split('/') if p and p != '.']
    return [ROOT_NAME, PAN_DIR, _safe(provider_name), month_of(ts), *map(_safe, parts)]


def sidecars(video_path):
    """和视频同名的附属文件（字幕 .srt、弹幕 .ass、封面 .jpg 等）：跟视频一起上传。"""
    d, base = os.path.split(video_path)
    stem = os.path.splitext(base)[0]
    out = []
    try:
        for n in os.listdir(d or '.'):
            p = os.path.join(d, n)
            if n != base and n.startswith(stem + '.') and os.path.isfile(p) and not n.endswith(('.part', '.ytdl')):
                out.append(p)
    except OSError:
        pass
    return sorted(out)


def to_recycle_bin(path):
    """移到回收站（可以恢复）；失败返回 False。"""
    if os.name != 'nt':
        return False

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [('hwnd', ctypes.c_void_p), ('wFunc', ctypes.c_uint), ('pFrom', ctypes.c_wchar_p),
                    ('pTo', ctypes.c_wchar_p), ('fFlags', ctypes.c_uint16), ('fAnyOperationsAborted', ctypes.c_int),
                    ('hNameMappings', ctypes.c_void_p), ('lpszProgressTitle', ctypes.c_wchar_p)]
    # FO_DELETE=3；FOF_ALLOWUNDO|FOF_NOCONFIRMATION|FOF_SILENT|FOF_NOERRORUI
    op = SHFILEOPSTRUCTW(None, 3, os.path.abspath(path) + '\0', None, 0x40 | 0x10 | 0x4 | 0x400, 0, None, None)
    return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not os.path.exists(path)


# ---------------------------------------------------------------- 登录
class AuthError(Exception):
    """需要重新登录（没登录、授权被撤销或过期）"""


class Auth:
    def __init__(self, session_factory):
        self._session = session_factory
        self._lock = threading.Lock()
        self._access, self._expires = None, 0
        self.client = self._load_client()
        self.refresh_token, self.email = self._load_token()

    @staticmethod
    def _load_client():
        c = _load_json(CLIENT_PATH, {})
        c = c.get('installed') or c.get('web') or c
        return {'id': c.get('client_id', ''), 'secret': c.get('client_secret', ''),
                'auth_uri': c.get('auth_uri') or 'https://accounts.google.com/o/oauth2/auth',
                'token_uri': c.get('token_uri') or 'https://oauth2.googleapis.com/token'}

    @staticmethod
    def _load_token():
        try:
            with open(TOKEN_PATH, 'rb') as f:
                d = json.loads(_dpapi(f.read(), False))
            return d.get('refresh_token'), d.get('email', '')
        except (OSError, ValueError):
            return None, ''

    def _save_token(self):
        os.makedirs(DIR, exist_ok=True)
        data = json.dumps({'refresh_token': self.refresh_token, 'email': self.email}).encode()
        with open(TOKEN_PATH, 'wb') as f:
            f.write(_dpapi(data, True))

    @property
    def logged_in(self):
        return bool(self.refresh_token)

    def login(self, timeout=300, open_browser=webbrowser.open):
        """打开浏览器授权，等用户点完（最多 timeout 秒）。成功返回账号邮箱。"""
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        state = secrets.token_urlsafe(16)
        result = {}
        done = threading.Event()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                if 'code' not in q and 'error' not in q:
                    self.send_response(204)
                    self.end_headers()
                    return
                ok = q.get('state', [''])[0] == state and 'code' in q
                result.update(code=q.get('code', [''])[0] if ok else '', error=q.get('error', [''])[0])
                body = ('<h2>已连接谷歌云盘</h2><p>可以关闭这个页面，回到链接解析下载器。</p>' if ok else
                        '<h2>没有完成授权</h2><p>可以关闭这个页面，回到软件里重试。</p>')
                page = (f'<!doctype html><meta charset="utf-8"><title>链接解析下载器</title>'
                        f'<body style="font-family:sans-serif;text-align:center;padding-top:80px;color:#333">{body}')
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(page.encode())
                done.set()

            def log_message(self, *a):
                pass

        server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        redirect = f'http://127.0.0.1:{server.server_port}/'
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = self.client['auth_uri'] + '?' + urllib.parse.urlencode({
                'client_id': self.client['id'], 'redirect_uri': redirect, 'response_type': 'code', 'scope': SCOPE,
                'access_type': 'offline', 'prompt': 'consent', 'state': state,
                'code_challenge': challenge, 'code_challenge_method': 'S256'})
            open_browser(url)
            if not done.wait(timeout):
                raise AuthError('等待授权超时，请重试')
        finally:
            server.shutdown()
            server.server_close()
        if not result.get('code'):
            raise AuthError('没有完成授权' + (f'（{result["error"]}）' if result.get('error') else ''))
        r = self._session().post(self.client['token_uri'], data={
            'code': result['code'], 'client_id': self.client['id'], 'client_secret': self.client['secret'],
            'redirect_uri': redirect, 'grant_type': 'authorization_code', 'code_verifier': verifier}, timeout=30)
        d = r.json()
        if r.status_code != 200 or not d.get('refresh_token'):
            raise AuthError(f'获取登录凭据失败：{d.get("error_description") or d.get("error") or r.status_code}')
        with self._lock:
            self.refresh_token = d['refresh_token']
            self._access, self._expires = d.get('access_token'), time.time() + int(d.get('expires_in', 3600)) - 120
        return self.refresh_token

    def set_email(self, email):
        self.email = email or ''
        self._save_token()

    def logout(self):
        token = self.refresh_token
        with self._lock:
            self.refresh_token, self.email, self._access, self._expires = None, '', None, 0
        try:
            os.remove(TOKEN_PATH)
        except OSError:
            pass
        if token:       # 顺便在谷歌那边撤销授权（失败也没关系）
            try:
                self._session().post('https://oauth2.googleapis.com/revoke', data={'token': token}, timeout=10)
            except Exception:  # noqa: BLE001
                pass

    def access_token(self, force=False):
        with self._lock:
            if not self.refresh_token:
                raise AuthError('还没有连接谷歌账号')
            if self._access and not force and time.time() < self._expires:
                return self._access
            r = self._session().post(self.client['token_uri'], data={
                'client_id': self.client['id'], 'client_secret': self.client['secret'],
                'refresh_token': self.refresh_token, 'grant_type': 'refresh_token'}, timeout=30)
            d = r.json() if r.content else {}
            if r.status_code in (400, 401) and d.get('error') in ('invalid_grant', 'unauthorized_client'):
                raise AuthError('谷歌账号授权已失效，请在设置里重新连接')
            if r.status_code != 200:
                raise OSError(f'刷新登录失败：{d.get("error_description") or d.get("error") or r.status_code}')
            self._access, self._expires = d['access_token'], time.time() + int(d.get('expires_in', 3600)) - 120
            return self._access


# ---------------------------------------------------------------- Drive 接口
class DriveError(Exception):
    def __init__(self, msg, status=0):
        super().__init__(msg)
        self.status = status


class Drive:
    def __init__(self, get_proxy=lambda: ''):
        self.get_proxy = get_proxy
        self.auth = Auth(self.session)
        self._folders = _load_json(FOLDERS_PATH, {})
        self._folder_lock = threading.Lock()

    def session(self):
        import requests
        s = requests.Session()
        proxy = (self.get_proxy() or '').strip()
        if proxy:
            s.proxies = {'http': proxy, 'https': proxy}
        return s

    def _call(self, method, url, session=None, **kw):
        """带登录的请求；访问令牌过期（401）时刷新一次重试。"""
        s = session or self.session()
        for attempt in (1, 2):
            headers = {**kw.pop('headers', {}), 'Authorization': f'Bearer {self.auth.access_token(force=attempt == 2)}'}
            r = s.request(method, url, headers=headers, timeout=kw.pop('timeout', 60), **kw)
            if r.status_code != 401:
                return r
            kw['headers'] = {k: v for k, v in headers.items() if k != 'Authorization'}
        return r

    @staticmethod
    def _error(r):
        try:
            e = r.json().get('error') or {}
            msg = e.get('message') if isinstance(e, dict) else str(e)
        except ValueError:
            msg = r.text[:200]
        return DriveError(f'谷歌云盘返回 {r.status_code}：{msg}', r.status_code)

    def account(self):
        r = self._call('GET', f'{API}/about', params={'fields': 'user(emailAddress,displayName),storageQuota'})
        if r.status_code != 200:
            raise self._error(r)
        return r.json()

    @staticmethod
    def _q(name):
        return name.replace('\\', '\\\\').replace("'", "\\'")

    def find(self, name, parent, folder=False, size=None):
        q = f"name = '{self._q(name)}' and '{parent}' in parents and trashed = false"
        if folder:
            q += f" and mimeType = '{FOLDER_MIME}'"
        r = self._call('GET', f'{API}/files', params={'q': q, 'fields': 'files(id,name,size,mimeType)', 'pageSize': 50,
                                                       'spaces': 'drive'})
        if r.status_code != 200:
            raise self._error(r)
        for f in r.json().get('files', []):
            if size is None or int(f.get('size') or -1) == int(size):
                return f['id']
        return None

    def ensure_folder(self, parts):
        """确保云盘上有这一串文件夹，返回最里层的 ID（有缓存；缓存的文件夹被删了会重建）。"""
        with self._folder_lock:
            parent = 'root'
            for i in range(len(parts)):
                key = '/'.join(parts[:i + 1])
                fid = self._folders.get(key)
                if fid and not self._exists(fid):
                    self._folders = {k: v for k, v in self._folders.items() if not k.startswith(key)}
                    fid = None
                if not fid:
                    fid = self.find(parts[i], parent, folder=True)
                if not fid:
                    r = self._call('POST', f'{API}/files', params={'fields': 'id'},
                                   json={'name': parts[i], 'mimeType': FOLDER_MIME, 'parents': [parent]})
                    if r.status_code not in (200, 201):
                        raise self._error(r)
                    fid = r.json()['id']
                self._folders[key] = fid
                parent = fid
            _save_json(FOLDERS_PATH, self._folders)
            return parent

    def _exists(self, fid):
        r = self._call('GET', f'{API}/files/{fid}', params={'fields': 'id,trashed'})
        return r.status_code == 200 and not r.json().get('trashed')

    def folder_link(self, parts):
        fid = self._folders.get('/'.join(parts))
        return f'https://drive.google.com/drive/folders/{fid}' if fid else 'https://drive.google.com/drive/my-drive'

    # ------------------------------------------------------------ 断点续传
    def start_upload(self, path, parent, name=None):
        size = os.path.getsize(path)
        mime = mimetypes.guess_type(path)[0] or 'application/octet-stream'
        r = self._call('POST', UPLOAD_API, params={'uploadType': 'resumable', 'fields': 'id,name,size'},
                       headers={'X-Upload-Content-Type': mime, 'X-Upload-Content-Length': str(size)},
                       json={'name': name or os.path.basename(path), 'parents': [parent]})
        if r.status_code != 200 or not r.headers.get('Location'):
            raise self._error(r)
        return r.headers['Location']

    def upload(self, path, session_uri, progress=None, cancelled=lambda: False):
        """把文件传到 session_uri（从云盘那边已经收到的位置接着传）。返回云盘文件 ID。
        session 过期（404/410）抛 DriveError(status=404)，调用方重新开一个。"""
        import requests
        size = os.path.getsize(path)
        s = self.session()
        offset = self._uploaded_bytes(s, session_uri, size)
        if isinstance(offset, str):
            return offset
        fails = 0
        with open(path, 'rb') as f:
            while True:
                if cancelled():
                    raise InterruptedError
                f.seek(offset)
                data = f.read(CHUNK)
                end = offset + len(data) - 1
                headers = {'Content-Range': f'bytes {offset}-{end}/{size}' if data else f'bytes */{size}'}
                base = offset
                body = _Body(data, (lambda n: progress(base + n, size)) if progress else None)
                try:
                    r = s.put(session_uri, data=body, headers=headers, timeout=120)
                except requests.RequestException as e:
                    fails += 1
                    if fails > 8:
                        raise DriveError(f'网络连接失败：{e}') from e
                    time.sleep(min(60, 2 ** fails))
                    got = self._uploaded_bytes(s, session_uri, size)
                    if isinstance(got, str):
                        return got
                    offset = got
                    continue
                if r.status_code in (200, 201):
                    if progress:
                        progress(size, size)
                    return r.json()['id']
                if r.status_code == 308:
                    rng = r.headers.get('Range')
                    offset = int(rng.rsplit('-', 1)[1]) + 1 if rng else 0
                    fails = 0
                    if progress:
                        progress(offset, size)
                    continue
                if r.status_code in (404, 410):
                    raise DriveError('上传会话已过期', 404)
                if r.status_code in (429, 500, 502, 503, 504):
                    fails += 1
                    if fails > 8:
                        raise self._error(r)
                    time.sleep(min(60, 2 ** fails))
                    got = self._uploaded_bytes(s, session_uri, size)
                    if isinstance(got, str):
                        return got
                    offset = got
                    continue
                raise self._error(r)

    def _uploaded_bytes(self, s, session_uri, size):
        """问云盘这个会话已经收到多少字节；已经传完返回文件 ID（str）。"""
        r = s.put(session_uri, headers={'Content-Range': f'bytes */{size}'}, timeout=60)
        if r.status_code in (200, 201):
            return r.json()['id']
        if r.status_code == 308:
            rng = r.headers.get('Range')
            return int(rng.rsplit('-', 1)[1]) + 1 if rng else 0
        if r.status_code in (404, 410):
            raise DriveError('上传会话已过期', 404)
        raise self._error(r)


class _Body:
    """一块要上传的数据：边发送边汇报进度（不然要等一整块 16 MB 传完进度才动一下）。
    requests 看到有 read() 和 __len__ 就按文件流发送，并据此填 Content-Length。"""

    def __init__(self, data, on_sent=None):
        self.data, self.pos, self.on_sent = data, 0, on_sent

    def __len__(self):
        return len(self.data) - self.pos

    def read(self, n=-1):
        n = len(self.data) - self.pos if n is None or n < 0 else n
        out = self.data[self.pos:self.pos + n]
        self.pos += len(out)
        if self.on_sent and out:
            self.on_sent(self.pos)
        return out


def import_client(path):
    """导入在 Google Cloud 下载的 OAuth 客户端文件（client_secret_….json）。返回错误说明，成功返回空字符串。"""
    try:
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
    except (OSError, ValueError):
        return '这不是有效的密钥文件（应该是 client_secret_….json）'
    if 'web' in d:
        return '这是「Web 应用」类型的密钥。请重新创建客户端，应用类型选「桌面应用」'
    c = d.get('installed') or {}
    if not c.get('client_id') or not c.get('client_secret'):
        return '这不是有效的密钥文件（应该是 client_secret_….json）'
    os.makedirs(DIR, exist_ok=True)
    _save_json(CLIENT_PATH, d)
    return ''


# ---------------------------------------------------------------- 上传队列
QUEUED, RUNNING, DONE, FAILED, CANCELED = 'queued', 'running', 'done', 'failed', 'canceled'


class Uploader:
    """后台上传队列：同时传 2 个文件，没传完的记在 queue.json 里，下次打开软件接着传。

    任务（dict）：id, path, folder（文件夹列表）, title, group（界面用来归类：如 'task:12' / 'batch:3' / 'hist:45'）,
    keep_local, state, done, size, session, file_id, error。
    on_change(job) 在后台线程里调用。
    """

    def __init__(self, drive, on_change=lambda job: None, workers=2):
        self.drive = drive
        self.on_change = on_change
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self.jobs = {}
        self.uploaded = _load_json(UPLOADED_PATH, {})
        self._next = 1
        for j in _load_json(QUEUE_PATH, []):
            if j.get('state') in (QUEUED, RUNNING, FAILED):
                j['state'] = QUEUED if j['state'] == RUNNING else j['state']
                self.jobs[j['id']] = j
                self._next = max(self._next, int(j['id']) + 1)
        self._threads = [threading.Thread(target=self._worker, daemon=True, name=f'gdrive-{i}') for i in range(workers)]
        for t in self._threads:
            t.start()

    @staticmethod
    def _key(path):
        return os.path.normcase(os.path.abspath(path))

    def uploaded_info(self, path):
        """这个本地文件传过没有：返回 {id, folder, ts} 或 None"""
        return self.uploaded.get(self._key(path)) if path else None

    def add(self, path, folder, title='', group='', keep_local=True, force=False):
        """加入上传队列。同一个文件已经在队列里或已经传过（force=True 时不管传没传过）就不重复加，返回 None。"""
        key = self._key(path)
        with self._lock:
            if (key in self.uploaded and not force) or any(self._key(j['path']) == key and j['state'] in (QUEUED, RUNNING)
                                           for j in self.jobs.values()):
                return None
            try:
                size = os.path.getsize(path)
            except OSError:
                return None
            j = {'id': self._next, 'path': path, 'folder': list(folder), 'title': title or os.path.basename(path),
                 'group': group, 'keep_local': bool(keep_local), 'state': QUEUED, 'done': 0, 'size': size,
                 'session': '', 'file_id': '', 'error': '', 'ts': time.time()}
            self._next += 1
            self.jobs[j['id']] = j
            self._persist()
            self._cv.notify_all()
        self.on_change(j)
        return j

    def retry(self, job_id):
        with self._lock:
            j = self.jobs.get(job_id)
            if j and j['state'] in (FAILED, CANCELED):
                j.update(state=QUEUED, error='')
                self._persist()
                self._cv.notify_all()
        if j:
            self.on_change(j)

    def cancel(self, job_id):
        with self._lock:
            j = self.jobs.get(job_id)
            if j and j['state'] in (QUEUED, RUNNING, FAILED):
                j['state'] = CANCELED
                self._persist()
        if j:
            self.on_change(j)

    def group_jobs(self, group):
        with self._lock:
            return [dict(j) for j in self.jobs.values() if j['group'] == group]

    def group_state(self, group):
        """一组任务的汇总：(状态, 已传字节, 总字节, 已完成个数, 总个数, 错误)；没有任务返回 None"""
        js = self.group_jobs(group)
        js = [j for j in js if j['state'] != CANCELED]
        if not js:
            return None
        states = {j['state'] for j in js}
        state = RUNNING if RUNNING in states else QUEUED if QUEUED in states else FAILED if FAILED in states else DONE
        err = next((j['error'] for j in js if j['state'] == FAILED), '')
        return (state, sum(j['done'] for j in js), sum(j['size'] for j in js),
                sum(1 for j in js if j['state'] == DONE), len(js), err)

    def pending(self):
        with self._lock:
            return sum(1 for j in self.jobs.values() if j['state'] in (QUEUED, RUNNING))

    def _persist(self):
        _save_json(QUEUE_PATH, [j for j in self.jobs.values() if j['state'] in (QUEUED, RUNNING, FAILED)])

    def _worker(self):
        while True:
            with self._lock:
                while True:
                    j = next((x for x in sorted(self.jobs.values(), key=lambda x: x['id']) if x['state'] == QUEUED), None)
                    if j and self.drive.auth.logged_in:
                        break
                    self._cv.wait(30)
                j['state'] = RUNNING
            self.on_change(j)
            try:
                self._run(j)
            except InterruptedError:
                pass
            except AuthError as e:
                with self._lock:
                    j.update(state=FAILED, error=str(e))
            except Exception as e:  # noqa: BLE001 - 任何错误都显示在界面上，可以重试
                with self._lock:
                    j.update(state=FAILED, error=str(e) or type(e).__name__)
            with self._lock:
                self._persist()
            self.on_change(j)

    def _run(self, j):
        path = j['path']
        if not os.path.isfile(path):
            raise FileNotFoundError('本地文件已经不在了')
        parent = self.drive.ensure_folder(j['folder'])
        name = os.path.basename(path)
        file_id = self.drive.find(name, parent, size=j['size']) if not j['session'] else None
        if not file_id:
            for attempt in (1, 2):
                if not j['session']:
                    j['session'] = self.drive.start_upload(path, parent, name)
                    with self._lock:
                        self._persist()
                try:
                    file_id = self.drive.upload(path, j['session'], self._progress(j),
                                                cancelled=lambda: j['state'] == CANCELED)
                    break
                except DriveError as e:
                    if e.status == 404 and attempt == 1:       # 会话过期：重新开一个，从头传
                        j['session'], j['done'] = '', 0
                        continue
                    raise
        with self._lock:
            j.update(state=DONE, file_id=file_id, done=j['size'], session='')
            self.uploaded[self._key(path)] = {'id': file_id, 'folder': j['folder'], 'ts': time.time(), 'size': j['size']}
            _save_json(UPLOADED_PATH, self.uploaded)
        if not j['keep_local']:
            to_recycle_bin(path)

    def _progress(self, j):
        last = [0.0]

        def cb(done, total):
            j['done'] = done
            now = time.monotonic()
            if now - last[0] > 0.4 or done >= total:
                last[0] = now
                self.on_change(j)
        return cb

    def wake(self):
        """刚登录完：叫醒等待中的上传线程"""
        with self._lock:
            self._cv.notify_all()


def file_link(file_id):
    return f'https://drive.google.com/file/d/{file_id}/view'
