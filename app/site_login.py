"""网站登录信息：用内置浏览器（browser.py）登录网站，把浏览器解密好的 Cookie 取出来加密保存。

每个网站一个文件：cookies\\site_<域名>.bin（加密，见 secure_store.py），解析核心读 cookies 文件夹时自动带上。
cookies\\sites.json 只记状态（哪些网站登录过、什么时候读的），不含 Cookie。

自动刷新：解析 / 下载某个登录过的网站之前，如果距上次从浏览器读取超过 20 分钟，就再读一次
（浏览器窗口开着就直接问它，没开就后台无界面启动一下，读完立即退出）。
"""
import http.cookiejar
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlparse

from . import browser, secure_store
from .config import DATA

COOKIE_DIR = os.path.join(DATA, 'cookies')
META_PATH = os.path.join(COOKIE_DIR, 'sites.json')
PREFIX = 'site_'
MAX_AGE = 20 * 60           # 超过这么久没从浏览器读过，用之前先刷新一次


@dataclass(frozen=True)
class Site:
    key: str                # 主域名，也是文件名的一部分
    name: str
    url: str                # 登录页
    domains: tuple          # 这些域名（及其子域名）的 Cookie 都属于这个网站
    names: tuple = ()       # 出现在主域名上就说明已经登录的 Cookie（空 = 认不出来，由用户点「完成」）


# 常用网站。YouTube 的登录在 google.com 上完成，再跳回 youtube.com：两个域名的 Cookie 都要。
SITES = [
    Site('youtube.com', 'YouTube',
         'https://accounts.google.com/ServiceLogin?service=youtube&continue=https%3A%2F%2Fwww.youtube.com%2F',
         ('youtube.com', 'google.com'), ('LOGIN_INFO', 'SAPISID', '__Secure-3PAPISID')),
    Site('tiktok.com', 'TikTok', 'https://www.tiktok.com/login', ('tiktok.com',), ('sessionid',)),
    Site('x.com', 'X', 'https://x.com/i/flow/login', ('x.com', 'twitter.com'), ('auth_token',)),
    Site('instagram.com', 'Instagram', 'https://www.instagram.com/accounts/login/', ('instagram.com',), ('sessionid',)),
    Site('vimeo.com', 'Vimeo', 'https://vimeo.com/log_in', ('vimeo.com',), ('vimeo',)),
    Site('facebook.com', 'Facebook', 'https://www.facebook.com/login', ('facebook.com',), ('c_user',)),
    Site('twitch.tv', 'Twitch', 'https://www.twitch.tv/login', ('twitch.tv',), ('auth-token',)),
    Site('nicovideo.jp', 'niconico', 'https://account.nicovideo.jp/login', ('nicovideo.jp',), ('user_session',)),
    Site('weibo.com', '微博', 'https://weibo.com/login.php', ('weibo.com', 'weibo.cn', 'sina.com.cn'), ('SUB',)),
    Site('xiaohongshu.com', '小红书', 'https://www.xiaohongshu.com/explore', ('xiaohongshu.com',), ('web_session',)),
    Site('douyin.com', '抖音', 'https://www.douyin.com/', ('douyin.com',), ('sessionid',)),
    Site('reddit.com', 'Reddit', 'https://www.reddit.com/login/', ('reddit.com',), ('reddit_session',)),
]
COMMON = ('youtube.com', 'tiktok.com', 'x.com', 'instagram.com', 'vimeo.com')     # 设置页里固定列出的
_BY_KEY = {s.key: s for s in SITES}
# 两段式的公共后缀（example.co.uk 的主域名是三段）
_SUFFIX2 = {'co.uk', 'org.uk', 'ac.uk', 'com.cn', 'net.cn', 'org.cn', 'com.hk', 'com.tw', 'co.jp', 'ne.jp', 'or.jp',
            'co.kr', 'com.au', 'com.br', 'com.sg', 'co.in', 'com.mx', 'com.tr', 'com.ru'}

_lock = threading.RLock()
_refresh_lock = threading.Lock()     # 同时只让一个线程去读浏览器，其余的等它读完（读完就是新的了，不用再读）
_meta = None


def _load_meta():
    global _meta
    if _meta is None:
        try:
            with open(META_PATH, encoding='utf-8') as f:
                _meta = json.load(f)
        except (OSError, ValueError):
            _meta = {}
        _meta.setdefault('sites', {})
    return _meta


def _save_meta():
    os.makedirs(COOKIE_DIR, exist_ok=True)
    tmp = META_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(_meta, f, ensure_ascii=False)
    os.replace(tmp, META_PATH)


def main_domain(host):
    """www.m.example.com → example.com"""
    parts = (host or '').lower().strip('.').split('.')
    if len(parts) >= 3 and '.'.join(parts[-2:]) in _SUFFIX2:
        return '.'.join(parts[-3:])
    return '.'.join(parts[-2:]) if len(parts) >= 2 else (host or '').lower()


def _match(host, domain):
    host = (host or '').lower().lstrip('.')
    return host == domain or host.endswith('.' + domain)


def get(key):
    """按主域名取网站：常用网站，或者用户用「其它网址」登录过的。"""
    if key in _BY_KEY:
        return _BY_KEY[key]
    with _lock:
        info = _load_meta()['sites'].get(key)
    if info:
        return Site(key, info.get('name') or key, info.get('url') or f'https://{key}/', tuple(info.get('domains') or (key,)))
    return None


def custom(text):
    """用户输入的网址 / 域名 → 网站（不在常用列表里的）。输入不像网址时返回 None。"""
    text = (text or '').strip()
    if not text:
        return None
    url = text if re.match(r'https?://', text, re.I) else 'https://' + text
    host = urlparse(url).hostname or ''
    if '.' not in host or ' ' in host:
        return None
    key = main_domain(host)
    return for_host(host) or Site(key, key, url, (key,))


def for_host(host):
    """这个域名属于哪个网站（常用网站 + 登录过的自定义网站）；都不是返回 None。"""
    for s in SITES:
        if any(_match(host, d) for d in s.domains):
            return s
    key = main_domain(host)
    return get(key)


def for_url(url):
    try:
        return for_host(urlparse(url).hostname or '')
    except ValueError:
        return None


def guess(url):
    """给「去登录」按钮用：认得的网站直接返回，不认得的按网址的主域名当成自定义网站。"""
    return for_url(url) or custom(url)


def path_of(key):
    return os.path.join(COOKIE_DIR, f'{PREFIX}{key}.bin')


def status():
    """{主域名: {'saved': 上次从浏览器读取的时间, 'count': Cookie 条数}}：登录过的网站。"""
    with _lock:
        sites = _load_meta()['sites']
        return {k: dict(v) for k, v in sites.items() if os.path.isfile(path_of(k))}


def logged_in(key):
    with _lock:
        return key in _load_meta()['sites'] and os.path.isfile(path_of(key))


def listed():
    """设置页里要列出来的网站：固定的几个常用网站 + 登录过的其它网站。"""
    keys = list(COMMON) + [k for k in status() if k not in COMMON]
    return [s for s in (get(k) for k in keys) if s]


def _to_cookie(c):
    exp = c.get('expires')
    exp = int(exp) if isinstance(exp, (int, float)) and exp > 0 and not c.get('session') else None
    domain = c.get('domain') or ''
    return http.cookiejar.Cookie(
        0, c.get('name') or '', c.get('value') or '', None, False, domain, domain.startswith('.'),
        domain.startswith('.'), c.get('path') or '/', True, bool(c.get('secure')), exp, exp is None, None, None,
        {'HttpOnly': None} if c.get('httpOnly') else {})


def of_site(site, cookies):
    return [c for c in cookies if any(_match(c.get('domain'), d) for d in site.domains)]


def has_login(site, cookies):
    """浏览器的 Cookie 里有没有这个网站的「已登录」标志（出现在主域名上的关键 Cookie）。"""
    if not site.names:
        return False
    return any(c.get('name') in site.names and c.get('value') and _match(c.get('domain'), site.key) for c in cookies)


def save(site, cookies):
    """把浏览器 Cookie 里属于这个网站的加密保存。返回保存了多少条。"""
    mine = of_site(site, cookies)
    with _lock:
        meta = _load_meta()
        if not mine:
            return 0
        secure_store.save_cookies(path_of(site.key), [_to_cookie(c) for c in mine])
        meta['sites'][site.key] = {'name': site.name, 'url': site.url, 'domains': list(site.domains),
                                   'saved': time.time(), 'count': len(mine)}
        _save_meta()
    return len(mine)


def refresh(force=False):
    """从内置浏览器重新读一遍 Cookie，更新所有登录过的网站。返回是否读了。
    force=False 时，20 分钟内读过就不再读。读不出来（浏览器打不开）抛 BrowserError。"""
    def due():
        with _lock:
            meta = _load_meta()
            keys = [k for k in meta['sites'] if os.path.isfile(path_of(k))]
            return keys if keys and (force or time.time() - (meta.get('read') or 0) >= MAX_AGE) else []
    if not due() or not browser.available():
        return False
    with _refresh_lock:
        keys = due() if not force else due()
        if not keys:
            return False            # 等锁的时候别的线程已经读过了
        cookies = browser.read_cookies()        # 要一两秒：不占着状态锁，界面线程查登录状态不会被卡住
        with _lock:
            _load_meta()['read'] = time.time()
            for key in keys:
                site = get(key)
                if site and of_site(site, cookies):
                    save(site, cookies)    # 浏览器里已经没有这个网站的 Cookie（被清掉了）时保留上次的，不清空
            _save_meta()
    return True


def ensure_fresh(url, force=False):
    """解析 / 下载 url 之前调用：这个网站登录过、而且有一阵没读了（或者 force）就刷新。返回是否刷新了。"""
    site = for_url(url)
    if site is None or not logged_in(site.key):
        return False
    return refresh(force)


def logout(site):
    """退出登录：删掉保存的 Cookie，并把内置浏览器里这个网站的 Cookie 也清掉（下次登录从头来）。"""
    with _lock:
        secure_store.remove(path_of(site.key))
        secure_store.forget(path_of(site.key))
        meta = _load_meta()
        meta['sites'].pop(site.key, None)
        _save_meta()
    if not browser.available() or not os.path.isdir(os.path.join(browser.PROFILE_DIR, 'Default')):
        return
    b = browser.current_window()
    own = b is None
    try:
        if own:
            b = browser.Browser(headless=True)
        pages = b.pages()
        if not pages:
            return
        session = b.cdp.call('Target.attachToTarget', {'targetId': pages[0]['targetId'], 'flatten': True})['sessionId']
        for c in of_site(site, b.cookies()):
            b.cdp.call('Network.deleteCookies', {'name': c['name'], 'domain': c['domain'], 'path': c.get('path') or '/'},
                       session=session)
    except (browser.BrowserError, KeyError):
        pass
    finally:
        if own and b is not None:
            b.close(wait=3)
