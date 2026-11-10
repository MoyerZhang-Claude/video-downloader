"""B站扫码登录 + 登录自动续期。

登录信息保存在用户数据目录（%APPDATA%\\VideoDownloader），源码版和打包版共用一份，也不会随程序文件夹被拷走。
Cookie 和续期令牌都加密保存（bilibili.bin / bilibili_meta.bin，见 secure_store.py）；2.5 之前的明文文件
第一次读取时自动加密并删除。
B站网页登录的 SESSDATA 有效期较短，网页版靠 refresh_token 定期换新；这里按同样的流程自动续期。
"""
import http.cookiejar
import json
import os
import re
import threading
import time

from . import secure_store
from .config import ROOT

DATA_DIR = os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'), 'VideoDownloader')
COOKIE_PATH = os.path.join(DATA_DIR, 'bilibili.bin')
META_PATH = os.path.join(DATA_DIR, 'bilibili_meta.bin')
# 2.5 之前的明文文件（更早的版本还放在程序目录的 cookies 文件夹里）
_OLD_PATHS = [os.path.join(DATA_DIR, 'bilibili.txt'), os.path.join(ROOT, 'cookies', 'bilibili.txt')]
_OLD_META = os.path.join(DATA_DIR, 'bilibili_meta.json')

_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
       '(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36')
_HEADERS = {'User-Agent': _UA, 'Referer': 'https://www.bilibili.com/'}

# B站网页端刷新 Cookie 用的公钥（生成 correspondPath）
_REFRESH_PUBKEY = '''-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDLgd2OAkcGVtoE3ThUREbio0Eg
Uc/prcajMKXvkCKFCWhJYJcLkcM2DKKcSeFpD/j6Boy538YXnR6VhcuUJOhH2x71
nzPjfdTcqMz7djHum0qSZA0AyCBDABUqCrfNgCiJ00Ra7GmRj+YCK1NJEuewlb40
JNrRuoEUXpabUzGB8QIDAQAB
-----END PUBLIC KEY-----'''

# 扫码轮询状态
WAITING, SCANNED, EXPIRED, SUCCESS, ERROR = 'waiting', 'scanned', 'expired', 'success', 'error'
_POLL_CODES = {86101: WAITING, 86090: SCANNED, 86038: EXPIRED, 0: SUCCESS}

_lock = threading.RLock()
_migrated = False


def _migrate_old():
    """旧版本的明文登录信息：加密保存后删掉原文件（只在还没有加密文件时导入，之后见到明文就直接删）。"""
    global _migrated
    if _migrated:
        return
    _migrated = True
    for old in _OLD_PATHS:
        if not os.path.isfile(old):
            continue
        try:
            if _newer_than_encrypted(old):
                secure_store.import_plain_cookies(old, COOKIE_PATH)
            else:
                os.remove(old)
        except OSError:
            pass
    if os.path.isfile(_OLD_META):
        try:
            if not os.path.isfile(META_PATH):
                with open(_OLD_META, encoding='utf-8') as f:
                    secure_store.write_json(META_PATH, json.load(f))
            os.remove(_OLD_META)
        except (OSError, ValueError):
            pass


def _newer_than_encrypted(path):
    """明文文件比已有的加密登录信息新（或者还没有加密的）：应该导入它。
    加密文件保留了导入时明文文件的修改时间，所以直接比时间就行。"""
    try:
        return not os.path.isfile(COOKIE_PATH) or os.path.getmtime(path) > os.path.getmtime(COOKIE_PATH) + 1
    except OSError:
        return False


def adopt_plain(path):
    """别处找到的明文 B站登录文件（很早的版本放在 cookies 文件夹里）：比已有的登录信息新就导入，然后删掉明文。"""
    with _lock:
        try:
            if _newer_than_encrypted(path):
                secure_store.import_plain_cookies(path, COOKIE_PATH)
            else:
                os.remove(path)
        except OSError:
            pass


def load_cookies():
    """读取已保存的 B站 cookies，没有登录时返回空列表。"""
    with _lock:
        _migrate_old()
        return secure_store.load_cookies(COOKIE_PATH)


def save_cookies(cookies):
    with _lock:
        secure_store.save_cookies(COOKIE_PATH, list(cookies))


def _cookie(name):
    return next((c.value for c in load_cookies() if c.name == name and c.value), '')


def is_logged_in():
    return bool(_cookie('SESSDATA'))


def sessdata_expires():
    return next((c.expires for c in load_cookies() if c.name == 'SESSDATA' and c.expires), None)


def _load_meta():
    with _lock:
        _migrate_old()
    return secure_store.read_json(META_PATH, {}) or {}


def _save_meta(meta):
    secure_store.write_json(META_PATH, meta)


def _save_session_cookies(session):
    cookies = getattr(session.cookies, 'jar', session.cookies)     # curl_cffi 的 Cookies 包着一个标准 CookieJar
    save_cookies(c for c in cookies if 'bilibili' in c.domain or 'biligame' in c.domain)


def _session_with_cookies():
    import requests
    s = requests.Session()
    s.headers.update(_HEADERS)
    for c in load_cookies():
        s.cookies.set_cookie(c)
    return s


def account_info(timeout=10):
    """返回 {'name', 'vip', 'vip_label', 'mid'}；cookies 失效或未登录返回 None。"""
    if not is_logged_in():
        return None
    data = _session_with_cookies().get(
        'https://api.bilibili.com/x/web-interface/nav', timeout=timeout).json().get('data') or {}
    if not data.get('isLogin'):
        return None
    label = (data.get('vip_label') or {}).get('text') or ''
    vip = data.get('vipStatus') == 1
    return {'name': data.get('uname') or '', 'mid': data.get('mid'), 'vip': vip,
            'vip_label': label or ('大会员' if vip else '')}


def _correspond_path(ts):
    from Cryptodome.Cipher import PKCS1_OAEP
    from Cryptodome.Hash import SHA256
    from Cryptodome.PublicKey import RSA
    cipher = PKCS1_OAEP.new(RSA.import_key(_REFRESH_PUBKEY), SHA256)
    return cipher.encrypt(f'refresh_{ts}'.encode()).hex()


def refresh(force=False, timeout=12):
    """按 B站网页端流程续期登录。返回 'ok' / 'not_needed' / 'no_token' / 'failed: 原因'。"""
    with _lock:
        meta = _load_meta()
        token = meta.get('refresh_token')
        csrf = _cookie('bili_jct')
        if not token or not csrf:
            return 'no_token'
        s = _session_with_cookies()
        info = s.get('https://passport.bilibili.com/x/passport-login/web/cookie/info',
                     params={'csrf': csrf}, timeout=timeout).json()
        data = info.get('data') or {}
        exp = sessdata_expires()
        soon = bool(exp and exp - time.time() < 36 * 3600)
        if info.get('code') == 0 and not data.get('refresh') and not force and not soon:
            return 'not_needed'
        ts = data.get('timestamp') or int(time.time() * 1000)
        html = s.get(f'https://www.bilibili.com/correspond/1/{_correspond_path(ts)}', timeout=timeout).text
        m = re.search(r'<div id="1-name">([^<]+)</div>', html)
        if not m:
            return 'failed: 拿不到 refresh_csrf'
        r = s.post('https://passport.bilibili.com/x/passport-login/web/cookie/refresh',
                   data={'csrf': csrf, 'refresh_csrf': m.group(1).strip(), 'source': 'main_web',
                         'refresh_token': token}, timeout=timeout).json()
        if r.get('code') != 0:
            return f'failed: {r.get("code")} {r.get("message")}'
        new_token = (r.get('data') or {}).get('refresh_token')
        _save_session_cookies(s)
        new_csrf = _cookie('bili_jct')
        try:   # 让旧的 refresh_token 失效（网页端同样会做这一步）
            s.post('https://passport.bilibili.com/x/passport-login/web/confirm/refresh',
                   data={'csrf': new_csrf, 'refresh_token': token}, timeout=timeout)
        except Exception:  # noqa: BLE001
            pass
        meta.update(refresh_token=new_token, last_refresh=time.time())
        _save_meta(meta)
        return 'ok'


def ensure_fresh():
    """启动 / 定时调用：需要时续期，然后返回账号信息（失效返回 {'expired': True}）。"""
    if not is_logged_in():
        return None
    result = ''
    try:
        result = refresh()
    except Exception as e:  # noqa: BLE001 - 网络问题不当作失效
        result = f'failed: {e}'
    info = account_info()
    if info is None and not result.startswith('ok'):
        try:     # 已经过期时再强制试一次续期
            if refresh(force=True) == 'ok':
                info = account_info()
        except Exception:  # noqa: BLE001
            pass
    if info is None:
        return {'expired': True}
    info['refresh'] = result
    return info


class QrLogin:
    """一次扫码登录流程：generate() 取二维码链接，poll() 查询状态，成功后自动保存 cookies 和续期令牌。

    用 curl_cffi 模拟真实 Chrome 并带上浏览器指纹（buvid3/buvid4）：普通脚本请求会被 B站风控，
    只给 24 小时的登录；像浏览器一样请求时能拿到更长的有效期。
    """
    _H = {'Referer': 'https://www.bilibili.com/', 'Origin': 'https://www.bilibili.com'}

    def __init__(self):
        try:
            from curl_cffi import requests as cffi_requests
            self.session = cffi_requests.Session(impersonate='chrome')
            self.headers = dict(self._H)
        except ImportError:
            import requests
            self.session = requests.Session()
            self.session.headers.update(_HEADERS)
            self.headers = dict(self._H)
        self.key = None

    def generate(self):
        # 先访问主页、取浏览器指纹，登录后一并保存
        try:
            self.session.get('https://www.bilibili.com/', headers=self.headers, timeout=10)
            spi = self.session.get('https://api.bilibili.com/x/frontend/finger/spi', headers=self.headers,
                                   timeout=10).json().get('data') or {}
            for name, key in (('buvid3', 'b_3'), ('buvid4', 'b_4')):
                if spi.get(key):
                    self.session.cookies.set(name, spi[key], domain='.bilibili.com')
        except Exception:  # noqa: BLE001 - 拿不到指纹也能扫码，只是有效期可能较短
            pass
        r = self.session.get('https://passport.bilibili.com/x/passport-login/web/qrcode/generate',
                             params={'source': 'main-fe-header'}, headers=self.headers, timeout=10).json()
        data = r.get('data') or {}
        if r.get('code') != 0 or not data.get('url'):
            raise RuntimeError(r.get('message') or '获取二维码失败')
        self.key = data['qrcode_key']
        return data['url']

    def poll(self):
        r = self.session.get('https://passport.bilibili.com/x/passport-login/web/qrcode/poll',
                             params={'qrcode_key': self.key, 'source': 'main-fe-header'},
                             headers=self.headers, timeout=10).json()
        data = r.get('data') or {}
        status = _POLL_CODES.get(data.get('code'), ERROR)
        if status == SUCCESS:
            with _lock:
                _save_session_cookies(self.session)
                _save_meta({'refresh_token': data.get('refresh_token'), 'login_time': time.time(),
                            'last_refresh': time.time()})
        return status, data.get('message') or r.get('message') or ''


def _make_cookie(name, value, expires, domain='.bilibili.com'):
    return http.cookiejar.Cookie(
        version=0, name=name, value=value, port=None, port_specified=False, domain=domain,
        domain_specified=True, domain_initial_dot=domain.startswith('.'), path='/', path_specified=True,
        secure=False, expires=int(expires) if expires else None, discard=False, comment=None, comment_url=None,
        rest={})


class TvQrLogin:
    """「长效登录」：用 B站电视端（云视听小电视）的扫码登录。

    登录成功后服务器会同时下发一套网页 cookies（SESSDATA 等）；软件只用这套 cookies，解析仍走网页接口，
    能下的清晰度、弹幕、字幕和网页扫码完全一样，区别只是登录有效期更长。
    """
    _APPKEY = '4409e2ce8ffd12b8'
    _APPSEC = '59b43e04ad6965f34319062b478f83dd'
    _CODES = {86039: WAITING, 86090: SCANNED, 86038: EXPIRED, 0: SUCCESS}

    def __init__(self):
        import requests
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'Mozilla/5.0 BiliTV', 'Content-Type': 'application/x-www-form-urlencoded'})
        self.key = None
        self.result = {}

    def _signed(self, **params):
        import hashlib
        import urllib.parse
        params.update(appkey=self._APPKEY, local_id='0', ts=str(int(time.time())))
        query = urllib.parse.urlencode(sorted(params.items()))
        params['sign'] = hashlib.md5((query + self._APPSEC).encode()).hexdigest()
        return params

    def generate(self):
        r = self.session.post('https://passport.bilibili.com/x/passport-tv-login/qrcode/auth_code',
                              data=self._signed(), timeout=10).json()
        data = r.get('data') or {}
        if r.get('code') != 0 or not data.get('url'):
            raise RuntimeError(r.get('message') or '获取二维码失败')
        self.key = data['auth_code']
        return data['url']

    def poll(self):
        r = self.session.post('https://passport.bilibili.com/x/passport-tv-login/qrcode/poll',
                              data=self._signed(auth_code=self.key), timeout=10).json()
        status = self._CODES.get(r.get('code'), ERROR)
        if status == SUCCESS:
            data = r.get('data') or {}
            self.result = data
            cookies = (data.get('cookie_info') or {}).get('cookies') or []
            if not any(c.get('name') == 'SESSDATA' for c in cookies):
                return ERROR, 'B站没有返回网页登录信息'
            with _lock:
                jar = [_make_cookie(c['name'], c.get('value', ''), c.get('expires')) for c in cookies]
                # 网页接口还需要 buvid3 一类的设备标识，沿用旧登录里的或新取一个
                for c in load_cookies():
                    if c.name in ('buvid3', 'buvid4', 'b_nut') and not any(x.name == c.name for x in jar):
                        jar.append(c)
                save_cookies(jar)
                _save_meta({'login_type': 'tv', 'login_time': time.time(), 'last_refresh': time.time(),
                            'tv_refresh_token': data.get('refresh_token'),
                            'tv_expires_at': time.time() + int(data.get('expires_in') or 0)})
        return status, r.get('message') or ''


def logout():
    """通知服务器注销当前登录（尽力而为），并删除本地登录信息。"""
    csrf = _cookie('bili_jct')
    if csrf:
        try:
            _session_with_cookies().post('https://passport.bilibili.com/login/exit/v2',
                                         data={'biliCSRF': csrf}, timeout=8)
        except Exception:  # noqa: BLE001 - 注销失败也要删本地信息
            pass
    with _lock:
        for path in (COOKIE_PATH, META_PATH, _OLD_META, *_OLD_PATHS):
            secure_store.remove(path)
        secure_store.forget(COOKIE_PATH)
