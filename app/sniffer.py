"""网页资源嗅探：在浏览器里打开网页（默认后台无界面，不弹窗口），自动静音播放网页里的视频，
监听它的网络请求，把视频地址找出来。网页源码里直接写着地址的情况由 page_sniff.sniff_static 先处理，
找不到才用到这里。

和链接解析互补：链接解析靠 yt-dlp 针对网站写的解析器，没有解析器的网站只能静态分析网页，
而很多网站的视频地址是播放器脚本运行后才去请求的，只有在浏览器里播放时才看得到。

- 监听：CDP 的 Network.requestWillBeSent（记下请求头：Referer、User-Agent、Origin）和 Network.responseReceived
  （按类型 video/*、audio/*、HLS、DASH 和地址后缀 .m3u8 .mpd .mp4 .flv .webm… 过滤）。子框架（iframe）、新开的标签页也跟着听。
- 归并：.ts、.m4s 等分片不列；HLS 主列表读一次，解析出各个清晰度，它下面的子列表不单独列；很小的文件（广告、预览）折叠。
- 只认媒体（视频、音频、播放列表），别的类型一概不要。
- 不处理 blob: 地址本身（抓的是它背后的 m3u8 / 分片）；页面出现 DRM 许可证请求、或者播放列表声明了 DRM 加密时，
  标为「受版权保护，无法下载」，不做任何绕过。
"""
import base64
import hashlib
import re
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from . import browser

_HLS_MIME = {'application/vnd.apple.mpegurl', 'application/x-mpegurl', 'audio/mpegurl', 'audio/x-mpegurl',
             'application/mpegurl'}
_DASH_MIME = {'application/dash+xml'}
_SEG_MIME = {'video/mp2t', 'video/iso.segment', 'audio/aac'}
_SEG_EXT = {'.ts', '.m4s', '.m4f', '.cmfv', '.cmfa', '.aac', '.vtt', '.webvtt', '.key', '.init'}
_FILE_EXT = {'.mp4': 'MP4', '.flv': 'FLV', '.webm': 'WebM', '.mov': 'MOV', '.m4v': 'MP4', '.mkv': 'MKV',
             '.mp3': 'MP3', '.m4a': 'M4A', '.ogg': 'OGG', '.wav': 'WAV'}
_DRM_URL = re.compile(r'widevine|playready|fairplay|/license|licen[cs]e[_-]?server|/drm[/?]|getlicense', re.I)
_DRM_KEY = re.compile(r'com\.widevine|com\.microsoft\.playready|com\.apple\.streamingkeydelivery|urn:uuid:edef8ba9|'
                      r'urn:uuid:9a04f079|METHOD=SAMPLE-AES', re.I)
SMALL = 1_500_000           # 小于 1.5 MB 的单个文件：多半是广告、预览，折叠起来


@dataclass
class Resource:
    key: str
    kind: str                   # hls / dash / file
    url: str
    label: str                  # HLS / DASH / MP4 / FLV …
    page_url: str = ''
    page_title: str = ''
    headers: dict = field(default_factory=dict)     # 下载时要带上的请求头（Referer、User-Agent、Origin）
    mime: str = ''
    size: int = 0
    duration: float = 0.0
    heights: list = field(default_factory=list)     # 有哪些清晰度（短边 / 高度，像素）
    codec: str = ''             # 视频编码（H.264 / H.265 / AV1…），知道的话
    live: bool = False
    protected: bool = False     # DRM：不能下载
    small: bool = False
    ts: float = 0.0

    @property
    def host(self):
        return urlparse(self.url).hostname or ''

    def stable_id(self):
        """同一个资源（不看地址里会变的签名参数）得到同一个 ID，给下载历史用。"""
        u = urlparse(self.url)
        return 'sniff_' + hashlib.sha1(f'{u.hostname}{u.path}'.encode('utf-8', 'replace')).hexdigest()[:12]


def _ext(url):
    path = urlparse(url).path.lower()
    dot = path.rfind('.')
    return path[dot:] if dot > path.rfind('/') else ''


def _template(url):
    """分片地址的「模板」：把数字都换掉。同一个模板出现很多次，就是一堆分片。"""
    u = urlparse(url)
    return (u.hostname or '') + re.sub(r'\d+', '#', u.path)


def parse_m3u8(text, base):
    """→ {'master': bool, 'variants': [(高度, 绝对地址)], 'duration': 秒, 'live': bool, 'protected': bool}"""
    out = {'master': False, 'variants': [], 'duration': 0.0, 'live': False, 'protected': bool(_DRM_KEY.search(text))}
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for i, line in enumerate(lines):
        if line.startswith('#EXT-X-STREAM-INF'):
            out['master'] = True
            m = re.search(r'RESOLUTION=(\d+)x(\d+)', line)
            height = min(int(m.group(1)), int(m.group(2))) if m else 0
            uri = next((x for x in lines[i + 1:i + 3] if not x.startswith('#')), None)
            if uri:
                out['variants'].append((height, urljoin(base, uri)))
        elif line.startswith('#EXTINF:'):
            try:
                out['duration'] += float(line[8:].split(',')[0])
            except ValueError:
                pass
    if not out['master']:
        out['live'] = '#EXT-X-ENDLIST' not in text and bool(out['duration'])
    return out


def parse_mpd(text):
    out = {'duration': 0.0, 'heights': [], 'live': 'type="dynamic"' in text,
           'protected': '<ContentProtection' in text and bool(re.search(r'cenc|widevine|playready|edef8ba9|9a04f079', text, re.I))}
    m = re.search(r'mediaPresentationDuration="PT(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?"', text)
    if m:
        out['duration'] = float(m.group(1) or 0) * 3600 + float(m.group(2) or 0) * 60 + float(m.group(3) or 0)
    sizes = {min(int(w), int(h)) for w, h in re.findall(r'width="(\d+)"[^>]*?height="(\d+)"', text)} | \
            {min(int(w), int(h)) for h, w in re.findall(r'height="(\d+)"[^>]*?width="(\d+)"', text)}
    out['heights'] = sorted(sizes, reverse=True)
    return out


# 自动播放：网页里的 <video> 静音后播放；再点一下常见播放器的「播放」按钮（不点会跳到别的网页的链接）
_AUTOPLAY_JS = r"""(() => {
  let n = 0;
  document.querySelectorAll('video,audio').forEach(v => {
    try { v.muted = true; v.volume = 0; const p = v.play(); if (p && p.catch) p.catch(() => {}); n++; } catch (e) {}
  });
  const sel = '.dplayer-play-icon,.dplayer-mobile-play,.vjs-big-play-button,.plyr__control--overlaid,.jw-icon-display,' +
    '.xgplayer-start,.prism-big-play-btn,.art-control-play,.fp-play,.mejs__overlay-button,.ckplayer-play,' +
    '[class*="play-btn"],[class*="playBtn"],[class*="btn-play"],[class*="big-play"],[aria-label="Play"],[aria-label="播放"]';
  document.querySelectorAll(sel).forEach(b => {
    if (b.dataset.vdClicked) return;
    b.dataset.vdClicked = '1';
    const a = b.closest('a[href]');
    if (a && !/^(#|javascript:)/i.test(a.getAttribute('href') || '')) return;
    try { b.click(); } catch (e) {}
  });
  return n;
})()"""


class Sniffer:
    """一次嗅探：打开浏览器 → 监听 → resources 里是找到的资源。
    headless=True：后台无界面地打开（不弹窗口，自己的浏览器进程，stop() 时关掉）；False：用看得见的浏览器窗口。
    on_change() 在有新发现 / 标题变了时调用，on_closed() 在浏览器被关掉时调用（都在后台线程里，界面要自己转回界面线程）。"""

    def __init__(self, url, on_change=None, on_closed=None, headless=False):
        self.url = url
        self.headless = headless
        self._own = None            # headless 时自己启动的浏览器
        self.on_change = on_change or (lambda: None)
        self.on_closed = on_closed or (lambda: None)
        self.resources = []         # 按发现的先后
        self.title = ''
        self.drm = False            # 页面上出现了 DRM 许可证请求
        self.running = False
        self.error = ''
        self._by_key = {}
        self._reqs = {}             # (会话, 请求编号) -> {'url', 'headers', 'doc'}
        self._pending = {}          # (会话, 请求编号) -> 等着读内容的播放列表资源
        self._variant_of = {}       # 子播放列表地址 -> 主列表资源
        self._templates = {}        # 分片模板 -> 出现次数
        self._segments = set()      # 已认定是分片的模板
        self._root = {}             # 会话 -> 它所属的页面会话
        self._page_url = {}         # 页面会话 -> 网址
        self._lock = threading.RLock()
        self._b = None
        self._session = None
        self._handlers = []

    # ------------------------------------------------------------ 开始 / 结束
    def start(self):
        """打开浏览器并开始监听（要等浏览器启动，放在后台线程里调用）。失败抛 BrowserError。"""
        if self.headless:
            b = self._own = browser.Browser(headless=True)
            pages = b.pages()
            tid = pages[0]['targetId'] if pages else b.cdp.call('Target.createTarget', {'url': 'about:blank'})['targetId']
        else:
            b, tid = browser.window('about:blank')
        self._b = b
        cdp = b.cdp
        for name, fn in (('Target.attachedToTarget', self._on_attached), ('Target.targetInfoChanged', self._on_info),
                         ('Network.requestWillBeSent', self._on_request), ('Network.responseReceived', self._on_response),
                         ('Network.loadingFinished', self._on_finished)):
            cdp.on(name, fn)
            self._handlers.append((name, fn))
        self.running = True
        cdp.call('Target.setDiscoverTargets', {'discover': True})       # 这样才会收到页面标题变化的通知
        # 以后新开的标签页（弹出的播放页）也自动跟上
        cdp.call('Target.setAutoAttach', {'autoAttach': True, 'waitForDebuggerOnStart': False, 'flatten': True})
        self._session = cdp.call('Target.attachToTarget', {'targetId': tid, 'flatten': True})['sessionId']
        self._setup(self._session, None)
        cdp.call('Page.navigate', {'url': self.url}, session=self._session, timeout=30)
        threading.Thread(target=self._watch, daemon=True, name='sniff-watch').start()

    def _setup(self, session, parent):
        with self._lock:
            self._root[session] = self._root.get(parent, session) if parent else session
        cdp = self._b.cdp
        for method, params in (('Network.enable', {}), ('Page.enable', {}),
                               ('Target.setAutoAttach', {'autoAttach': True, 'waitForDebuggerOnStart': False,
                                                         'flatten': True})):
            try:
                cdp.call(method, params, session=session, timeout=8)
            except browser.BrowserError:
                pass

    def _watch(self):
        n = 0
        while self.running and self._b is not None and self._b.alive:
            time.sleep(0.5)
            n += 1
            if n % 2 == 0 and self.running:
                self._poll_title()
        if self.running:            # 浏览器被用户关掉了
            self.running = False
            self.on_closed()

    def stop(self, close_browser=True):
        """停止监听。close_browser：顺便关掉浏览器窗口（用完立即关闭）。"""
        was = self.running
        self.running = False
        b = self._b
        if b is not None:
            for name, fn in self._handlers:
                b.cdp.off(name, fn)
        self._handlers = []
        if self._own is not None:
            own, self._own = self._own, None
            own.close(wait=3)
        elif close_browser and browser.current_window() is b:
            browser.close_window()
        return was

    def autoplay(self):
        """让网页里的视频播放起来（静音），播放器才会去请求视频地址。返回找到了几个 <video>。"""
        b = self._b
        if b is None or not self.running:
            return 0
        with self._lock:
            sessions = list(self._root)
        n = 0
        for session in sessions:
            try:
                r = b.cdp.call('Runtime.evaluate', {'expression': _AUTOPLAY_JS, 'returnByValue': True},
                               session=session, timeout=5)
                n += int(((r.get('result') or {}).get('value')) or 0)
            except (browser.BrowserError, ValueError, TypeError):
                continue
        return n

    # ------------------------------------------------------------ CDP 事件（读线程里）
    def _on_attached(self, params, parent):
        info = params.get('targetInfo') or {}
        session = params.get('sessionId')
        if not session or info.get('type') not in ('page', 'iframe', 'worker'):
            return
        if info.get('type') == 'page':
            with self._lock:
                self._page_url[session] = info.get('url') or ''
        # 在读线程里不能直接发命令等结果（结果也要靠这个线程读）：换个线程做
        threading.Thread(target=self._setup, args=(session, parent), daemon=True).start()

    def _on_info(self, params, _session):
        info = params.get('targetInfo') or {}
        if info.get('type') == 'page':
            self._set_title(info.get('title') or '', info.get('url') or '')

    def _set_title(self, title, url):
        changed = False
        with self._lock:
            # 页面还没加载出 <title> 时，浏览器拿网址当标题：不算
            if url.startswith('http') and title and title != self.title and title not in url \
                    and not title.startswith(('http://', 'https://', 'about:')):
                self.title = title
                for r in self.resources:
                    if not r.page_title:
                        r.page_title = title
                changed = True
        if changed:
            self.on_change()

    def _poll_title(self):
        """标题变化的通知不一定来（页面加载完才有标题）：隔一会儿直接问一次。"""
        try:
            pages = [p for p in self._b.pages() if (p.get('url') or '').startswith('http')]
        except browser.BrowserError:
            return
        if pages:
            self._set_title(pages[-1].get('title') or '', pages[-1].get('url') or '')

    def _on_request(self, params, session):
        req = params.get('request') or {}
        url = req.get('url') or ''
        if not url.startswith('http'):
            return
        if req.get('method') == 'POST' and _DRM_URL.search(url) and not self.drm:
            self.drm = True
            self._mark_protected()
        headers = {k: v for k, v in (req.get('headers') or {}).items()
                   if k.lower() in ('referer', 'user-agent', 'origin')}
        with self._lock:
            if len(self._reqs) > 4000:
                self._reqs.clear()
            self._reqs[(session, params.get('requestId'))] = {'url': url, 'headers': headers,
                                                             'doc': params.get('documentURL') or ''}

    def _on_response(self, params, session):
        resp = params.get('response') or {}
        url = resp.get('url') or ''
        status = resp.get('status') or 0
        if not url.startswith('http') or not 200 <= status < 300:
            return
        mime = (resp.get('mimeType') or '').lower().split(';')[0].strip()
        ext = _ext(url)
        if mime in _HLS_MIME or ext == '.m3u8':
            kind, label = 'hls', 'HLS'
        elif mime in _DASH_MIME or ext == '.mpd':
            kind, label = 'dash', 'DASH'
        elif ext in _SEG_EXT or mime in _SEG_MIME:
            return                                  # 分片：归它所属的播放列表，不单独列
        elif mime.startswith(('video/', 'audio/')) or (ext in _FILE_EXT and not mime.startswith(('text/', 'image/'))):
            kind, label = 'file', _FILE_EXT.get(ext) or mime.split('/')[-1].upper()[:5] or '视频'
        else:
            return                                  # 只认媒体：网页、脚本、图片、程序一概不要
        rid = (session, params.get('requestId'))
        with self._lock:
            req = self._reqs.get(rid) or {}
            u = urlparse(url)
            key = f'{kind}:{u.hostname}{u.path}'
            if key in self._by_key:
                return
            if kind == 'file':
                tpl = _template(url)
                if tpl in self._segments:
                    return
                self._templates[tpl] = self._templates.get(tpl, 0) + 1
                if self._templates[tpl] >= 4 and re.search(r'\d', u.path):
                    # 同一种地址只是数字不同、来了一大串：是分片（MSE 播放器逐段请求），不是完整的文件
                    self._segments.add(tpl)
                    self.resources = [r for r in self.resources if not (r.kind == 'file' and _template(r.url) == tpl)]
                    self._by_key = {r.key: r for r in self.resources}
                    self.on_change()
                    return
            hdrs = {k.lower(): v for k, v in (resp.get('headers') or {}).items()}
            size = 0
            m = re.search(r'/(\d+)\s*$', hdrs.get('content-range') or '')
            if m:
                size = int(m.group(1))
            elif (hdrs.get('content-length') or '').isdigit():
                size = int(hdrs['content-length'])
            root = self._root.get(session, session)
            res = Resource(key, kind, url, label, page_url=req.get('doc') or self._page_url.get(root) or self.url,
                           page_title=self.title,
                           headers=dict(req.get('headers') or {}), mime=mime, size=size if kind == 'file' else 0,
                           small=kind == 'file' and 0 < size < SMALL, ts=time.time(),
                           protected=self.drm and kind != 'file')
            if kind in ('hls', 'dash'):
                master = self._variant_of.get(url)
                if master is not None:
                    self._pending[rid] = (res, master)      # 主列表下面的子列表：只用来补时长，不单独列
                    return
                self._pending[rid] = (res, None)
            self.resources.append(res)
            self._by_key[key] = res
        self.on_change()

    def _on_finished(self, params, session):
        rid = (session, params.get('requestId'))
        with self._lock:
            item = self._pending.pop(rid, None)
        if item is not None:
            threading.Thread(target=self._read_playlist, args=(rid, *item), daemon=True).start()

    def _read_playlist(self, rid, res, master):
        try:
            body = self._b.cdp.call('Network.getResponseBody', {'requestId': rid[1]}, session=rid[0], timeout=8)
        except browser.BrowserError:
            return
        data = body.get('body') or ''
        if body.get('base64Encoded'):
            try:
                data = base64.b64decode(data).decode('utf-8', 'replace')
            except ValueError:
                return
        with self._lock:
            if res.kind == 'hls':
                info = parse_m3u8(data, res.url)
                target = master or res
                if info['protected']:
                    target.protected = True
                if info['master']:
                    res.heights = sorted({h for h, _u in info['variants'] if h}, reverse=True)
                    for _h, u in info['variants']:
                        self._variant_of[u] = res
                        # 子列表比主列表的内容先到的话，已经被单独列出来了：收回去
                        dup = self._by_key.pop(f'hls:{urlparse(u).hostname}{urlparse(u).path}', None)
                        if dup is not None and dup is not res:
                            self.resources = [r for r in self.resources if r is not dup]
                            target.duration = target.duration or dup.duration
                else:
                    if not target.duration:
                        target.duration, target.live = info['duration'], info['live']
            else:
                info = parse_mpd(data)
                res.duration, res.heights, res.live = info['duration'], info['heights'], info['live']
                if info['protected']:
                    res.protected = True
        self.on_change()

    def _mark_protected(self):
        with self._lock:
            for r in self.resources:
                if r.kind != 'file':
                    r.protected = True
        self.on_change()

    # ------------------------------------------------------------ 下载要用的 Cookie
    def cookies_for(self, url):
        """浏览器里会发给这个地址的 Cookie（CDP 的格式）。浏览器已经关了就无界面读一次，再按域名挑。"""
        b = self._b
        try:
            if b is not None and b.alive and self._session:
                return b.cdp.call('Network.getCookies', {'urls': [url]}, session=self._session).get('cookies') or []
        except browser.BrowserError:
            pass
        host = (urlparse(url).hostname or '').lower()
        try:
            return [c for c in browser.read_cookies()
                    if host == (c.get('domain') or '').lstrip('.') or host.endswith('.' + (c.get('domain') or '').lstrip('.'))]
        except browser.BrowserError:
            return []
