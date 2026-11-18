"""网页资源嗅探（不打开浏览器窗口）：发一个网页地址，把里面的视频地址找出来。电脑版和安卓版共用。

分两步：
1. 读网页源码找：很多网站的播放器配置直接写在网页里（DPlayer 的 data-config、<video src>、
   JSON 里的 "url": "…m3u8"），读一次网页就能找到，又快又不用浏览器，手机上也能用。
   同一网站的播放页 iframe 也跟进去看一层。
2. 源码里找不到（地址是播放器脚本运行后才去请求的）：电脑版再用后台无界面的浏览器打开网页、自动静音播放，
   监听它的网络请求（见 sniffer.Sniffer(headless=True)）。

找到的每个地址都带着这个网页的 Referer / User-Agent（很多视频站有防盗链）。只认视频、音频、播放列表；
播放列表声明了 DRM 加密的标为「受版权保护」，不做任何绕过。
"""
import html
import json
import re
from urllib.parse import urljoin, urlparse

from .sniffer import SMALL, Resource, _ext, parse_m3u8, parse_mpd

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/131.0.0.0 Safari/537.36')
_MEDIA_EXT = {'.m3u8': ('hls', 'HLS'), '.mpd': ('dash', 'DASH'), '.mp4': ('file', 'MP4'), '.m4v': ('file', 'MP4'),
              '.webm': ('file', 'WebM'), '.flv': ('file', 'FLV'), '.mov': ('file', 'MOV'), '.mkv': ('file', 'MKV')}
_TYPE_KIND = {'hls': ('hls', 'HLS'), 'm3u8': ('hls', 'HLS'), 'application/x-mpegurl': ('hls', 'HLS'),
              'application/vnd.apple.mpegurl': ('hls', 'HLS'), 'dash': ('dash', 'DASH'),
              'application/dash+xml': ('dash', 'DASH'), 'mp4': ('file', 'MP4'), 'video/mp4': ('file', 'MP4'),
              'flv': ('file', 'FLV'), 'video/x-flv': ('file', 'FLV'), 'webm': ('file', 'WebM'),
              'video/webm': ('file', 'WebM')}
# 网页里的绝对地址（后缀是媒体格式的）
_ABS = re.compile(r'''https?://[^\s"'<>()\\]+?\.(?:m3u8|mpd|mp4|m4v|webm|flv|mov|mkv)(?:\?[^\s"'<>\\]*)?(?=["'\s<>\\)]|$)''', re.I)
# <video src> / <source src> 的相对地址
_TAG_SRC = re.compile(r'''<(?:video|source)\b[^>]*?\bsrc\s*=\s*["']([^"']+)["']''', re.I)
# 播放器配置里的 {"url": "…", "type": "hls"}（地址可能没有后缀）
_URL_TYPE = re.compile(r'''"(?:url|src|file)"\s*:\s*"([^"]+)"\s*,\s*"type"\s*:\s*"([^"]+)"''', re.I)
_TYPE_URL = re.compile(r'''"type"\s*:\s*"([^"]+)"\s*,\s*"(?:url|src|file)"\s*:\s*"([^"]+)"''', re.I)
_IFRAME = re.compile(r'''<iframe\b[^>]*?\bsrc\s*=\s*["']([^"']+)["']''', re.I)
_TITLE = re.compile(r'''<meta[^>]+property=["']og:title["'][^>]+content=["']([^"']+)''', re.I)
_TITLE2 = re.compile(r'<title[^>]*>([^<]+)</title>', re.I)
# 明显不是正片的：广告、片头预告
_AD = re.compile(r'(?:^|[/._-])(?:ads?|advert|promo|preroll|pre-roll|trailer)(?:[/._-]|$)', re.I)


class SniffError(Exception):
    pass


def _session():
    try:
        from curl_cffi import requests as cr
        return cr.Session(impersonate='chrome'), True
    except ImportError:
        import requests
        s = requests.Session()
        s.headers['User-Agent'] = UA
        return s, False


def _variants(text):
    """同一段网页文字的几种写法：原文、HTML 实体解开、JSON 转义（\\/、\\u002F）解开。"""
    out = [text]
    t = html.unescape(text)
    if t != text:
        out.append(t)
    t2 = t.replace('\\/', '/').replace('\\u002F', '/').replace('\\u002f', '/').replace('\\u0026', '&') \
        .replace('\\x2F', '/').replace('\\x26', '&')
    if t2 != t:
        out.append(t2)
    return out


def page_title(text):
    m = _TITLE.search(text) or _TITLE2.search(text)
    title = html.unescape(m.group(1)).strip() if m else ''
    return re.sub(r'\s+', ' ', title)[:200]


def find_in_html(text, base):
    """网页源码 → [(地址, 种类, 标签, 字段名)]，按出现顺序去重。"""
    found, seen = [], set()

    def add(u, kind, label, hint=''):
        # 原文里 JSON 转义过的地址（https:\/\/…）先还原，不然会被当成相对地址拼到网页地址后面
        u = html.unescape(u.strip()).replace('\\/', '/').replace('\\u002F', '/').replace('\\u0026', '&')
        if not u or u.startswith(('data:', 'blob:', 'javascript:')) or '\\' in u:
            return
        u = urljoin(base, u)
        if not u.startswith(('http://', 'https://')):
            return
        p = urlparse(u)
        key = (p.hostname, p.path)
        if key in seen:
            return
        seen.add(key)
        found.append((u, kind, label, hint))

    for t in _variants(text):
        for m in _URL_TYPE.finditer(t):
            k = _TYPE_KIND.get(m.group(2).strip().lower())
            if k:
                add(m.group(1), *k, hint=_key_before(t, m.start()))
        for m in _TYPE_URL.finditer(t):
            k = _TYPE_KIND.get(m.group(1).strip().lower())
            if k:
                add(m.group(2), *k)
        for m in _ABS.finditer(t):
            k = _MEDIA_EXT.get(_ext(m.group(0)))
            if k:
                add(m.group(0), *k)
        for m in _TAG_SRC.finditer(t):
            k = _MEDIA_EXT.get(_ext(m.group(1))) or ('file', '视频')
            add(m.group(1), *k)
    return found


def _key_before(text, pos):
    """播放器配置里这个地址所在对象的字段名（"video_h265": {"url": …} 里的 video_h265），用来认出 H.265 版本。"""
    m = re.search(r'"([\w.-]{1,40})"\s*:\s*\{[^{}]{0,40}$', text[max(0, pos - 80):pos])
    return m.group(1) if m else ''


def _codec_of_hint(hint):
    h = hint.lower()
    if re.search(r'h\.?265|hevc', h):
        return 'H.265'
    if re.search(r'h\.?264|avc', h):
        return 'H.264'
    if 'av1' in h:
        return 'AV1'
    return ''


def _init_info(data):
    """fMP4 初始化段（几 KB，只有编码参数，不含画面）→ (编码, 高度)。"""
    codec = ''
    for tag, name in ((b'hvc1', 'H.265'), (b'hev1', 'H.265'), (b'avc1', 'H.264'), (b'avc3', 'H.264'),
                      (b'av01', 'AV1'), (b'vp09', 'VP9')):
        if tag in data:
            codec = name
            break
    height = 0
    for m in re.finditer(b'tkhd', data):
        start = m.start() - 4
        if start < 0:
            continue
        size = int.from_bytes(data[start:start + 4], 'big')
        if not 84 <= size <= 120 or start + size > len(data):
            continue
        w = int.from_bytes(data[start + size - 8:start + size - 4], 'big') >> 16
        h = int.from_bytes(data[start + size - 4:start + size], 'big') >> 16
        if w and h:
            height = max(height, min(w, h))
    return codec, height


# 两段式后缀：a.sina.com.cn 的主域名是 sina.com.cn，不是 com.cn（和 site_login.main_domain 一致；手机版没有 site_login.py）
_SUFFIX2 = {'co.uk', 'org.uk', 'ac.uk', 'com.cn', 'net.cn', 'org.cn', 'gov.cn', 'edu.cn', 'com.hk', 'com.tw', 'co.jp',
            'ne.jp', 'or.jp', 'co.kr', 'com.au', 'com.br', 'com.sg', 'co.in', 'com.mx', 'com.tr', 'com.ru'}


def _main_domain(host):
    parts = (host or '').lower().strip('.').split('.')
    if len(parts) >= 3 and '.'.join(parts[-2:]) in _SUFFIX2:
        return '.'.join(parts[-3:])
    return '.'.join(parts[-2:]) if len(parts) >= 2 else (host or '').lower()


def _same_site(a, b):
    ha, hb = urlparse(a).hostname or '', urlparse(b).hostname or ''
    return bool(ha and hb) and _main_domain(ha) == _main_domain(hb)


def _path_match(req_path, cookie_path):
    """RFC 6265 5.1.4：/a 匹配 /a、/a/、/a/b，不匹配 /ab"""
    cp = cookie_path or '/'
    if req_path == cp:
        return True
    return req_path.startswith(cp) and (cp.endswith('/') or req_path[len(cp)] == '/')


def _cookie_matches(c, url):
    u = urlparse(url)
    host = (u.hostname or '').lower()
    dom = (c.domain or '').lower().lstrip('.')
    if not host or not dom:
        return False
    if c.domain_specified or (c.domain or '').startswith('.'):
        if host != dom and not host.endswith('.' + dom):
            return False
    elif host != dom:                          # 只给这个主机的 Cookie
        return False
    if c.secure and u.scheme != 'https':
        return False
    if c.is_expired():
        return False
    return _path_match(u.path or '/', c.path)


def _cookies_for(url, cookies):
    """这个地址该带哪些 Cookie。cookies 是 http.cookiejar.Cookie 列表（带域名、路径、Secure），
    逐个按 RFC 6265 的规则匹配；同名的取路径最长的那个。旧的「名字 → 值」字典原样返回。"""
    if not cookies:
        return None
    if isinstance(cookies, dict):
        return dict(cookies)
    picked = {}
    for c in sorted((c for c in cookies if c.value and _cookie_matches(c, url)), key=lambda c: len(c.path or '/')):
        picked[c.name] = c.value
    return picked or None


_REDIRECTS = (301, 302, 303, 307, 308)
_CHARSET = re.compile(rb'charset\s*=\s*["\x27]?([\w.:-]+)', re.I)


def _read(r, limit):
    """流式读最多 limit 字节就关掉连接：网址实际是几百 MB 的视频、服务器又不理 Range 时，也不会整个读进内存"""
    data = bytearray()
    try:
        for chunk in r.iter_content(64 * 1024):
            if chunk:
                data += chunk
            if len(data) >= limit:
                break
    finally:
        try:
            r.close()
        except Exception:  # noqa: BLE001
            pass
    return bytes(data[:limit])


def _text(r, limit):
    """读最多 limit 字节并解码：编码按响应头 → 网页里的 <meta charset> → UTF-8 的顺序认"""
    data = _read(r, limit)
    m = _CHARSET.search((r.headers.get('Content-Type') or '').encode('latin-1', 'replace')) or _CHARSET.search(data[:4096])
    enc = m.group(1).decode('ascii', 'replace') if m else 'utf-8'
    try:
        return data.decode(enc, errors='replace')
    except LookupError:
        return data.decode('utf-8', errors='replace')


def _fetch(session, url, referer=None, cookies=None, timeout=20):
    """只拿响应头（stream），正文由 _text / _read 按上限读"""
    headers = {'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8', 'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.6'}
    if referer:
        headers['Referer'] = referer
    if not cookies:
        return session.get(url, headers=headers, timeout=timeout, allow_redirects=True, stream=True)
    # 带登录 Cookie 时自己跟重定向：每一跳重新挑 Cookie，跳到别的网站就不会带上这个网站的登录信息
    for _ in range(8):
        r = session.get(url, headers=headers, cookies=_cookies_for(url, cookies), timeout=timeout,
                        allow_redirects=False, stream=True)
        loc = r.headers.get('Location')
        if r.status_code not in _REDIRECTS or not loc:
            return r
        r.close()
        url = urljoin(str(r.url or url), loc)
    return r


def _probe(session, res, timeout=12):
    """读一下播放列表：清晰度、时长、是否 DRM；单个文件看大小。读不到也保留（下载时 yt-dlp 会带着请求头再试）。"""
    try:
        if res.kind == 'file':
            r = session.head(res.url, headers=res.headers, timeout=timeout, allow_redirects=True)
            n = r.headers.get('Content-Length') or ''
            if n.isdigit():
                res.size = int(n)
                res.small = 0 < res.size < SMALL
            ctype = (r.headers.get('Content-Type') or '').split(';')[0].strip().lower()
            if ctype.startswith(('text/', 'image/')):
                return False
            return True
        r = session.get(res.url, headers=res.headers, timeout=timeout, stream=True)
        if r.status_code >= 400:
            r.close()
            return True
        body = _text(r, 2_000_000)
        if res.kind == 'hls':
            if '#EXTM3U' not in body[:2000]:
                return True             # 地址没有后缀、配置里写着 hls：内容不像播放列表也先留着
            info = parse_m3u8(body, str(r.url))
            res.protected = info['protected']
            if info['master']:
                res.heights = sorted({h for h, _u in info['variants'] if h}, reverse=True)
                # 主列表本身没有时长：读第一个子列表补上
                if info['variants']:
                    try:
                        sub = session.get(info['variants'][0][1], headers=res.headers, timeout=timeout, stream=True)
                        si = parse_m3u8(_text(sub, 2_000_000), str(sub.url))
                        res.duration, res.live = si['duration'], si['live']
                        res.protected = res.protected or si['protected']
                    except Exception:  # noqa: BLE001
                        pass
            else:
                res.duration, res.live = info['duration'], info['live']
                # fMP4 分片：读一下初始化段（几 KB），认出编码和分辨率
                m = re.search(r'#EXT-X-MAP:URI="([^"]+)"', body)
                if m:
                    try:
                        init = session.get(urljoin(str(r.url), m.group(1)),
                                           headers=dict(res.headers, Range='bytes=0-65535'), timeout=timeout, stream=True)
                        if init.status_code < 400:
                            codec, height = _init_info(_read(init, 65536))       # 服务器不理 Range 也只读 64 KB
                        else:
                            init.close()
                            res.codec = res.codec or codec
                            if height and not res.heights:
                                res.heights = [height]
                    except Exception:  # noqa: BLE001
                        pass
        else:
            info = parse_mpd(body)
            res.duration, res.heights, res.live, res.protected = (info['duration'], info['heights'], info['live'],
                                                                  info['protected'])
    except Exception:  # noqa: BLE001 - 读不到只是少了清晰度信息
        pass
    return True


def sniff_static(url, cookies=None, log=None, timeout=20):
    """读网页源码找视频地址。返回 (标题, [Resource])；网页打不开抛 SniffError。"""
    session, _impersonate = _session()
    try:
        r = _fetch(session, url, cookies=cookies, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        raise SniffError(f'网页打不开：{e}') from e
    if r.status_code >= 400:
        r.close()
        raise SniffError(f'网页打不开（HTTP {r.status_code}）')
    page = str(r.url)
    ctype = (r.headers.get('Content-Type') or '').lower()
    if 'html' not in ctype and 'text' not in ctype and 'json' not in ctype and 'javascript' not in ctype:
        r.close()                       # 只看了响应头就断开：不是网页（比如直接是视频文件）就不读内容
        raise SniffError('这个地址不是网页')
    text = _text(r, 6_000_000)
    title = page_title(text)
    found = find_in_html(text, page)
    if not found:
        # 视频放在同一网站的播放页 iframe 里：跟进去看一层（最多 3 个）
        frames = [urljoin(page, html.unescape(f)) for f in _IFRAME.findall(text)]
        frames = [f for f in frames if f.startswith('http') and _same_site(f, page)][:3]
        for f in frames:
            try:
                fr = _fetch(session, f, referer=page, cookies=cookies, timeout=timeout)
                if fr.status_code < 400:
                    found += find_in_html(_text(fr, 3_000_000), str(fr.url))
                else:
                    fr.close()
            except Exception:  # noqa: BLE001
                continue
    origin = f'{urlparse(page).scheme}://{urlparse(page).netloc}'
    out = []
    for u, kind, label, hint in found[:20]:
        if _AD.search(urlparse(u).path):
            continue
        res = Resource(f'{kind}:{urlparse(u).hostname}{urlparse(u).path}', kind, u, label, page_url=page,
                       page_title=title, headers={'Referer': page, 'Origin': origin, 'User-Agent': UA},
                       codec=_codec_of_hint(hint))
        if _probe(session, res):
            out.append(res)
    if log:
        log('info', f'网页嗅探（读网页源码）：{page} → 找到 {len(out)} 个视频地址')
    return title, out


def ordered(resources):
    """排序：能下载的在前；正片（不是小文件）在前；有清晰度信息的、时长长的在前。"""
    return sorted(resources, key=lambda r: (r.protected, r.small, -(max(r.heights) if r.heights else 0),
                                            -(r.duration or 0)))


def describe(res):
    """一行说明：HLS · 最高 1080p（3 档）· 12:34"""
    bits = [res.label] + ([res.codec] if res.codec else [])
    if res.heights:
        bits.append(f'最高 {res.heights[0]}p' + (f'（{len(res.heights)} 档）' if len(res.heights) > 1 else ''))
    if res.duration:
        d = int(res.duration)
        bits.append(f'{d // 3600}:{d % 3600 // 60:02d}:{d % 60:02d}' if d >= 3600 else f'{d // 60}:{d % 60:02d}')
    if res.live:
        bits.append('直播')
    if res.protected:
        bits.append('受版权保护（DRM），无法下载')
    return ' · '.join(bits)


def extra_for(res, cookies=None):
    """给解析器用的 extra（见 core.Parser.submit）：下载时带上的请求头、Cookie、页面标题、稳定 ID。"""
    return {'headers': dict(res.headers), 'cookies': list(cookies or []), 'title': res.page_title or '',
            'page': res.page_url, 'id': res.stable_id()}


def dump(resources):
    return json.dumps([{'url': r.url, 'kind': r.kind, 'label': r.label, 'heights': r.heights, 'duration': r.duration,
                        'protected': r.protected, 'small': r.small, 'size': r.size} for r in resources],
                      ensure_ascii=False)


def sniff_dynamic(url, on_change=None, stopped=lambda: False, max_wait=30, quiet=6, log=None, _attach=None):
    """后台无界面地打开网页、自动静音播放，监听网络请求找视频地址（电脑版，要有 Edge / Chrome）。
    找到资源后再等 quiet 秒没有新的就结束，最多 max_wait 秒；stopped() 返回 True 时提前结束。
    返回 (Sniffer, {资源 key: 下载这个资源要带的 Cookie（CDP 格式）})。打不开浏览器抛 BrowserError。"""
    import time
    from . import browser, sniffer
    with browser._lock:             # 软件自己的浏览器配置同一时间只能开一个浏览器
        sn = sniffer.Sniffer(url, on_change=on_change, headless=True)
        if _attach:
            _attach(sn)
        cookies = {}
        try:
            sn.start()
            t0 = last_t = time.monotonic()
            last_n = 0
            while not stopped() and sn.running and time.monotonic() - t0 < max_wait:
                time.sleep(1.5)
                now = time.monotonic()
                if now - t0 > 2:
                    sn.autoplay()
                n = len(sn.resources)
                if n != last_n:
                    last_n, last_t = n, now
                if n and now - last_t > quiet and now - t0 > 8:
                    break
            for r in list(sn.resources):
                try:
                    cookies[r.key] = sn.cookies_for(r.url)
                except Exception:  # noqa: BLE001
                    cookies[r.key] = []
        finally:
            sn.stop()
    if log:
        log('info', f'网页嗅探（后台浏览器）：{url} → 找到 {len(sn.resources)} 个视频地址')
    return sn, cookies
