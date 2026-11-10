"""解析与下载核心（与界面无关，通过回调汇报状态）。"""
import copy
import glob
import os
import re
import shutil
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field


from . import bilibili, collection, core_update, danmaku, runtime_update, secure_store
from .config import DATA, FILENAME_TEMPLATES, ROOT, ffmpeg_dir

# yt-dlp 首次导入较慢（冷启动可达数秒），延迟到第一次使用或后台预加载时再导入，让窗口先显示出来
_yt = None
_yt_lock = threading.Lock()
startup_log = []        # 界面还没接上日志时产生的消息：[(级别, 文字)]，界面启动后取走


def yt():
    global _yt
    if _yt is None:
        with _yt_lock:
            if _yt is None:
                # 在线更新过的内核：导入前先核对文件（坏了就自动回到内置版本）
                core_update.verify_override(lambda level, msg: startup_log.append((level, msg)))
                import yt_dlp
                from yt_dlp.utils import DownloadCancelled, DownloadError

                from . import extractors
                _yt = types.SimpleNamespace(YoutubeDL=yt_dlp.YoutubeDL, DownloadCancelled=DownloadCancelled,
                                            DownloadError=DownloadError, extractors=extractors)
    return _yt


def preload():
    yt()

# ---------------------------------------------------------------- 链接提取

_URL_RE = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+", re.I)


def extract_urls(text):
    """从分享文本中提取所有链接（去重、保持顺序）。"""
    urls, seen = [], set()
    for m in _URL_RE.finditer(text or ''):
        u = m.group(0).rstrip('.,;:!?\'")]}')
        if u.count('(') < u.count(')'):
            u = u.rstrip(')')
        if len(u) > 10 and u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


PLATFORM_NAMES = {
    'Youtube': 'YouTube', 'YoutubeTab': 'YouTube', 'BiliBili': 'B站', 'BilibiliBangumi': 'B站番剧',
    'BiliBiliPlayer': 'B站', 'BilibiliSpaceVideo': 'B站', 'Twitter': 'X', 'TwitterBroadcast': 'X',
    'TikTok': 'TikTok', 'DouyinShare': '抖音', 'Douyin': '抖音', 'KuaishouShare': '快手',
    'Instagram': 'Instagram', 'Weibo': '微博', 'WeiboVideo': '微博', 'XiaoHongShu': '小红书',
    'Vimeo': 'Vimeo', 'Facebook': 'Facebook', 'Reddit': 'Reddit', 'Twitch': 'Twitch',
    'Ixigua': '西瓜视频', 'Youku': '优酷', 'IqiyiIE': '爱奇艺', 'Generic': '网页',
}


def platform_name(info):
    key = info.get('extractor_key') or info.get('ie_key') or ''
    return PLATFORM_NAMES.get(key, key or '未知')


# ---------------------------------------------------------------- 错误提示

_ERROR_HINTS = [
    (r'Unsupported URL', '暂不支持这个网站/链接'),
    (r'Requested format is not available', '所选清晰度已不可用，请重新解析'),
    (r"not a bot|confirm you.re not a bot",
     'YouTube 暂时拦截了当前网络（多半是代理 / VPN 节点被风控）：换一个节点后重试；'
     '经常出现的话，在 设置 → 网络与登录 里登录 YouTube'),
    (r'Log ?in|login|logged|sign in|cookies|authentication|premium member|会员',
     '该内容需要登录：在 设置 → 网络与登录 里登录这个网站'),
    (r'Private video', '视频为私密内容：需要用有权限的账号登录（设置 → 网络与登录）'),
    (r'geo.?restrict|not available in your (?:country|region)|in your country', '地区限制：请尝试更换代理节点'),
    (r'Video unavailable|has been removed|This video is not available|已删除|不存在',
     '视频不可用（可能已删除、私密或地区限制）'),
    (r'HTTP Error 403|Forbidden', '服务器拒绝访问 (403)：可以稍后重试，或在 设置 → 网络与登录 里登录这个网站'),
    (r'HTTP Error 404', '资源不存在 (404)'),
    (r'HTTP Error 429|Too Many Requests', '请求过于频繁 (429)，请稍后再试'),
    (r'timed out|Timeout|Connection|getaddrinfo|Network|SSL|EOF occurred|ProxyError|Unable to download',
     '网络连接失败：请检查网络或代理设置'),
    (r'ffmpeg|ffprobe', 'ffmpeg 处理失败（请确认程序目录下的 ffmpeg 文件夹完整）'),
    (r'No space left|磁盘', '磁盘空间不足'),
]


_LOGIN_RE = re.compile(r"Log ?in|login|logged|sign in|cookies|authentication|premium member|会员|not a bot|"
                       r"confirm you.re not a bot|Private video|age.?restrict|members.only", re.I)
LOGIN_EXPIRED = '登录已过期'


def needs_login(raw):
    """这个报错是不是「要登录才能看」一类（卡片上给「去登录」按钮用）"""
    return bool(raw) and bool(_LOGIN_RE.search(raw))


def site_cookies_fresh(url, log=None, force=False):
    """解析 / 下载之前：这个网站是用内置浏览器登录的话，Cookie 有一阵没读了就从浏览器再读一次（见 site_login.py）。
    返回是否重新读了。只有 Windows 电脑版有内置浏览器。"""
    if os.name != 'nt':
        return False
    try:
        from . import site_login
        return site_login.ensure_fresh(url, force)
    except Exception as e:  # noqa: BLE001 - 刷新失败就用上次保存的
        if log:
            log('warning', f'从内置浏览器刷新登录信息失败：{e}')
        return False


def site_logged_in(url):
    if os.name != 'nt':
        return False
    try:
        from . import site_login
        site = site_login.for_url(url)
        return bool(site and site_login.logged_in(site.key))
    except Exception:  # noqa: BLE001
        return False


def friendly_error(exc):
    msg = re.sub(r'\x1b\[[0-9;]*m', '', str(exc)).strip()
    msg = re.sub(r'^ERROR:\s*', '', msg)
    raw = msg
    # 自定义提取器抛出的中文提示直接使用
    m = re.search(r'\]\s*(?:[A-Za-z0-9_-]+:)?\s*([^\x00-\x7f].*)$', msg)
    if m and re.search(r'[一-鿿]', m.group(1)):
        return m.group(1).split('; please report')[0].strip(), raw
    for pattern, hint in _ERROR_HINTS:
        if re.search(pattern, msg, re.I):
            return hint, raw
    return (msg[:120] + '…' if len(msg) > 120 else msg) or '未知错误', raw


# ---------------------------------------------------------------- yt-dlp 参数

class _Logger:
    def __init__(self, sink=None, prefix=''):
        self.sink, self.prefix = sink, prefix
        self.already_downloaded = False
        self.locked_formats = ''

    def _emit(self, level, msg):
        if 'has already been downloaded' in msg:
            self.already_downloaded = True
        m = re.search(r'Format\(s\) (.+?) are missing; you have to become a premium member', msg)
        if m:
            self.locked_formats = m.group(1)
        if self.sink:
            self.sink(level, f'{self.prefix}{msg}')

    def debug(self, msg):
        if not msg.startswith('[debug] '):
            self._emit('info', msg)

    def info(self, msg):
        self._emit('info', msg)

    def warning(self, msg):
        self._emit('warning', msg)

    def error(self, msg):
        self._emit('error', msg)


def _find_js_runtimes(verify=True):
    """电脑上能用的 JS 运行时（YouTube 的验证要用）。优先级：数据目录里按需下载的 Deno > 程序目录 > 系统 PATH。
    verify=False：只看有没有，不核对 deno.exe 的哈希（界面线程里显示状态用，核对要读 100 多 MB）。"""
    runtimes = {}
    deno = None
    if runtime_update.supported():
        deno = runtime_update.deno_path() if verify else (os.path.isfile(runtime_update.DENO) and runtime_update.DENO)
    if deno:
        runtimes['deno'] = {'path': deno}
    for name in ('deno', 'node', 'bun'):
        if name in runtimes:
            continue
        bundled = os.path.join(ROOT, 'runtime', f'{name}.exe')
        if os.path.isfile(bundled):
            runtimes[name] = {'path': bundled}
        elif shutil.which(name):
            runtimes[name] = {}
    return runtimes


def _js_runtimes():
    return _find_js_runtimes() or {'deno': {}}


_NEEDS_JS_RE = re.compile(r'https?://(?:[\w-]+\.)*(?:youtube\.com|youtu\.be|youtube-nocookie\.com)/', re.I)
JS_RUNTIME_TEXT = '正在准备 YouTube 组件（约 40MB，只需一次）'


def ensure_js_runtime(url, settings, status=None, log=None):
    """解析 YouTube 之前：电脑上没有任何 JS 运行时就先下载 Deno（只有第一次需要）。
    下载失败不拦着解析（只是可能少清晰度），原因写进日志。"""
    if not _NEEDS_JS_RE.match(url or '') or not runtime_update.supported() or _find_js_runtimes():
        return
    if status:
        status(JS_RUNTIME_TEXT)
    last = [0.0]

    def progress(p):
        now = time.monotonic()
        if status and (now - last[0] >= 0.3 or p >= 1):
            last[0] = now
            status(f'{JS_RUNTIME_TEXT} {p * 100:.0f}%')
    try:
        runtime_update.ensure(progress, (settings.proxy or '').strip())
    except Exception as e:  # noqa: BLE001
        if log:
            log('warning', f'YouTube 组件（Deno）下载失败：{e}。这次先不用它解析，YouTube 可能缺清晰度或失败；'
                           '可以检查网络 / 代理后重新解析')
    if status:
        status('')


# 这些报错多半是网站改版、解析内核跟不上了（YouTube 最常见）：更新内核通常就能好
_OUTDATED_RE = re.compile(
    r'nsig|n-sig|signature|Requested format is not available|Unable to extract|No video formats found|'
    r'Failed to (?:parse|extract)|please report this issue|challenge solving failed|player response', re.I)


def looks_outdated(raw):
    return bool(raw) and bool(_OUTDATED_RE.search(raw)) and not re.search(r'not a bot|Sign in to confirm', raw, re.I)


def base_options(settings, logger):
    opts = {
        'quiet': True,
        'no_warnings': False,
        'noprogress': True,
        'logger': logger,
        'color': {'stdout': 'no_color', 'stderr': 'no_color'},
        'noplaylist': True,
        'playlistend': 200,
        'socket_timeout': 20,
        'retries': 10,
        'fragment_retries': 10,
        'extractor_retries': 2,
        'js_runtimes': _js_runtimes(),
        'windowsfilenames': True,
    }
    ff = ffmpeg_dir()
    if ff:
        opts['ffmpeg_location'] = ff
        # yt-dlp 的片段下载只在 PATH 里找 ffmpeg（不认 ffmpeg_location），把自带的加到本进程 PATH 前面
        if ff not in os.environ.get('PATH', '').split(os.pathsep):
            os.environ['PATH'] = ff + os.pathsep + os.environ.get('PATH', '')
    if settings.proxy.strip():
        opts['proxy'] = settings.proxy.strip()
    if settings.cookie_mode == 'browser' and settings.cookie_browser:
        # 不把 cookiesfrombrowser 交给 yt-dlp：那样每创建一个 YoutubeDL（解析、下载、字幕、过期重试各一个）
        # 都要重新读一遍浏览器的 Cookie 数据库，又慢，浏览器开着时还常被锁。改成读一次缓存 10 分钟（new_ydl 里加进去）。
        opts[_BROWSER_KEY] = (settings.cookie_browser, logger)
    elif settings.cookie_mode == 'file' and settings.cookie_file and os.path.isfile(settings.cookie_file):
        opts['cookiefile'] = settings.cookie_file
    return opts


_BROWSER_KEY = '_vd_cookies_from_browser'
_browser_cookies = {}       # 浏览器名 -> (读取时间, Cookie 列表)
_browser_lock = threading.Lock()
BROWSER_COOKIE_TTL = 600


def browser_mode_cookies(name, logger=None):
    """「从浏览器读取」模式：读出这个浏览器的 Cookie，10 分钟内重复用同一份。读不出来返回空（1 分钟后再试）。"""
    with _browser_lock:
        cached = _browser_cookies.get(name)
        now = time.monotonic()
        if cached and now - cached[0] < (BROWSER_COOKIE_TTL if cached[1] else 60):
            return cached[1]
        try:
            from yt_dlp.cookies import extract_cookies_from_browser
            cookies = list(extract_cookies_from_browser(name, logger=logger))
        except Exception as e:  # noqa: BLE001
            cookies = []
            if logger:
                logger.warning(f'从浏览器 {name} 读取 cookies 失败：{e}（新版 Chrome / Edge 读不出来，建议在 设置 → 网络与登录 里用内置浏览器登录）')
        _browser_cookies[name] = (now, cookies)
        return cookies


COOKIE_DIR = os.path.join(DATA, 'cookies')
imported_plain = []     # 刚刚自动加密的明文 cookies 文件名（界面取走后提示用户一次）
_not_cookies = {}       # 路径 -> 修改时间：放在 cookies 文件夹里、但不是 cookies 的 .txt，不反复去读


def _encrypt_plain_cookies():
    """用户手动放进 cookies 文件夹的 .txt（浏览器扩展导出的）：加密存成同名 .bin，再删掉明文文件。
    登录信息在磁盘上不留明文（数据文件夹被别的程序读走、或发给别人排查问题时不会把账号交出去）。"""
    try:
        names = [n for n in os.listdir(COOKIE_DIR) if n.lower().endswith('.txt')]
    except OSError:
        return
    for name in names:
        path = os.path.join(COOKIE_DIR, name)
        if name.lower() == 'bilibili.txt':       # 很早的版本把 B站登录放在这里：交给 bilibili.py 处理
            bilibili.adopt_plain(path)
            continue
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if _not_cookies.get(path) == mtime:
            continue
        try:
            n = secure_store.import_plain_cookies(path, os.path.splitext(path)[0] + '.bin')
        except OSError:
            n = -1
        if n > 0:
            imported_plain.append(name)
        else:
            _not_cookies[path] = mtime


def folder_cookies():
    """cookies 文件夹里保存的网站登录信息（加密的 .bin：内置浏览器登录的、导入的 cookies.txt）和 B站扫码登录都自动生效。
    只解密到内存里。浏览器登录得到的（文件名就是网站域名）比手动导入的新，后加载、同名时以它为准。"""
    cookies = list(bilibili.load_cookies())     # B站扫码登录（保存在用户数据目录）
    _encrypt_plain_cookies()
    for name in cookie_file_names():
        cookies.extend(secure_store.load_cookies(os.path.join(COOKIE_DIR, name + '.bin')))
    return cookies


def cookie_file_names():
    """已保存登录信息的名字（不带扩展名）。手动导入的在前，内置浏览器登录的（site_ 开头）在后。"""
    try:
        names = [n[:-4] for n in os.listdir(COOKIE_DIR) if n.lower().endswith('.bin')]
    except OSError:
        return []
    return sorted(names, key=lambda n: (n.startswith(SITE_PREFIX), n.lower()))


SITE_PREFIX = 'site_'       # 内置浏览器登录的网站：cookies\site_youtube.com.bin


def adopt_stray_cookie_files(folder=DATA):
    """直接放在数据文件夹里（没放进 cookies 子文件夹）的 cookies.txt：挪进 cookies 文件夹，下次读取时会自动加密。
    只认文件开头写着「Netscape HTTP Cookie File」的，别的 .txt 不动。返回挪了哪些。"""
    moved = []
    try:
        names = [n for n in os.listdir(folder) if n.lower().endswith('.txt')]
    except OSError:
        return moved
    for name in names:
        path = os.path.join(folder, name)
        if name.lower() == 'bilibili.txt' or not os.path.isfile(path):
            continue
        try:
            with open(path, 'rb') as f:
                head = f.read(200)
        except OSError:
            continue
        if b'HTTP Cookie File' not in head:
            continue
        try:
            os.makedirs(COOKIE_DIR, exist_ok=True)
            os.replace(path, os.path.join(COOKIE_DIR, name))
            moved.append(name)
        except OSError:
            pass
    return moved


def ydl_opener(ydl):
    """给 fastdl 用的 urlopen(url, headers)：走 yt-dlp 的网络层（代理、Cookie 和解析时一致）。"""
    from yt_dlp.networking import Request

    def urlopen(url, headers):
        return ydl.urlopen(Request(url, headers=headers))
    return urlopen


def pick_fast_urls(info, format_ids, ydl, is_cancelled=None):
    """有多个候选地址的格式（目前是 B站）：并行测速，把最快的地址设为下载地址。返回选中的域名列表。"""
    from urllib.parse import urlparse

    from . import fastdl
    fmts = {f.get('format_id'): f for f in info.get('formats') or []}
    chosen = []
    for fid in format_ids:
        f = fmts.get(fid)
        cands = (f or {}).get('url_candidates') or []
        if len(cands) < 2:
            continue
        headers = {k: v for k, v in (f.get('http_headers') or info.get('http_headers') or {}).items()
                   if k.lower() != 'cookie'}
        try:
            ranked = fastdl.rank_urls(cands, headers, ydl_opener(ydl), is_cancelled=is_cancelled)
        except Exception:  # noqa: BLE001 - 测速失败就用原来的顺序
            continue
        f['url_candidates'] = ranked
        f['url'] = ranked[0]
        chosen.append(urlparse(ranked[0]).hostname)
    return chosen


def new_ydl(opts, cookies=()):
    """cookies：只给这一次用的额外 Cookie（网页嗅探到的资源，带着浏览器里那个页面的 Cookie）。"""
    y = yt()
    from_browser = opts.pop(_BROWSER_KEY, None)
    ydl = y.YoutubeDL(opts, auto_init=False)
    y.extractors.register(ydl)
    extra = (browser_mode_cookies(*from_browser) if from_browser else []) + folder_cookies() + list(cookies)
    if extra:
        jar = ydl.cookiejar
        for c in extra:
            jar.set_cookie(c)
    return ydl


# ---------------------------------------------------------------- 清晰度选项

@dataclass
class QualityOption:
    label: str             # 下拉框显示
    tag: str               # 写入文件名，如 1080p60
    format: str            # yt-dlp 格式选择器
    kind: str              # video / audio / image
    res: int = 0           # 短边像素，用于排序和「全部设为」
    size: int = 0          # 预估字节数
    streams: list = field(default_factory=list)   # [(format_id, 预估大小)]
    convert: str = ''      # 下载后转换的音频格式：mp3 / flac


_STD_RES = (144, 240, 360, 480, 540, 576, 720, 1080, 1440, 2160, 4320)
_CODEC_FAMILY = (('avc', 'h264'), ('h264', 'h264'), ('hvc', 'h265'), ('hev', 'h265'), ('h265', 'h265'),
                 ('vp09', 'vp9'), ('vp9', 'vp9'), ('vp8', 'vp8'), ('av01', 'av1'), ('av1', 'av1'))
_CODEC_LABEL = {'h264': 'H.264', 'h265': 'H.265', 'vp9': 'VP9', 'vp8': 'VP8', 'av1': 'AV1'}
_CODEC_RANK = {
    'compat': {'h264': 0, None: 0, 'vp9': 1, 'vp8': 2, 'h265': 2, 'av1': 3},
    'quality': {'av1': 0, 'vp9': 1, 'h265': 1, 'h264': 2, None: 2, 'vp8': 3},
    'best': {},          # 画质优先：不看编码，同一清晰度选码率最高的
}


def codec_family(vcodec):
    v = (vcodec or '').lower()
    for prefix, fam in _CODEC_FAMILY:
        if v.startswith(prefix):
            return fam
    return None


def short_side(f):
    w, h = f.get('width'), f.get('height')
    s = min(w, h) if w and h else (h or w or 0)
    for std in _STD_RES:
        if abs(s - std) <= std * 0.015:
            return std
    return s


def fmt_size(n):
    if not n:
        return '未知'
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n:.0f} {unit}' if unit in ('B', 'KB') else f'{n:.1f} {unit}'
        n /= 1024


def _size(f, duration=None):
    s = f.get('filesize') or f.get('filesize_approx')
    if not s and f.get('tbr') and duration:
        s = f['tbr'] * 1000 / 8 * duration
    return int(s or 0)


def _is_video(f):
    if f.get('format_note') == 'storyboard' or f.get('ext') == 'mhtml' or f.get('protocol') == 'mhtml':
        return False
    vc = f.get('vcodec')
    if vc == 'none':
        return False
    return vc is not None or bool(f.get('height') or f.get('width')) or f.get('ext') in ('mp4', 'webm', 'flv', 'mkv', 'mov')


def _has_audio(f):
    return f.get('acodec') != 'none'


def _special_audio(f):
    return (f.get('acodec') or '').lower().startswith(('flac', 'alac', 'ec-3', 'ac-3', 'eac3'))


def _audio_name(f, info=None):
    """音轨的编码名：AAC / Opus / FLAC 无损 / 杜比全景声 …"""
    a = (f.get('acodec') or '').lower()
    if a.startswith('flac'):
        return 'FLAC 无损'
    if a.startswith('alac'):
        return 'ALAC 无损'
    if a.startswith(('ec-3', 'eac3')):
        # B站的杜比音轨是杜比全景声（Dolby Atmos，E-AC-3 JOC）
        bili = ((info or {}).get('extractor_key') or '').startswith('BiliBili')
        return '杜比全景声' if bili or 'atmos' in (f.get('format_note') or '').lower() else '杜比数字+'
    if a.startswith('ac-3'):
        return '杜比数字'
    for prefix, name in (('mp4a', 'AAC'), ('aac', 'AAC'), ('opus', 'Opus'), ('vorbis', 'Vorbis'), ('mp3', 'MP3')):
        if a.startswith(prefix):
            return name
    return (f.get('ext') or '音频').upper()


def _rate(f):
    """码率 kbps（总码率，没有就用视频 / 音频码率）"""
    return f.get('tbr') or f.get('vbr') or f.get('abr') or 0


def fmt_rate(kbps):
    if not kbps:
        return ''
    return f'{kbps / 1000:.1f} Mbps' if kbps >= 1000 else f'{kbps:.0f} kbps'


def _proto_rank(f):
    p = f.get('protocol') or ''
    if p in ('https', 'http'):
        return 0
    if 'dash' in p:
        return 1
    if 'm3u8' in p:
        return 2
    return 3


HDR_ONLY_MSG = '这个视频只有 HDR / 杜比视界版本：打开输入框下方的「HDR」开关（或 设置 → 下载 里的「解析 HDR / 杜比视界版本」）后可以下载'


def hdr_only(info):
    """视频只有 HDR 版本（没有普通 SDR 画面）"""
    vids = [f for f in info.get('formats') or [] if _is_video(f)]
    return bool(vids) and all((f.get('dynamic_range') or 'SDR').upper() != 'SDR' for f in vids)


def _res_name(res):
    return '8K' if res >= 4320 else '4K' if res >= 2160 else '2K' if res >= 1440 else f'{res}P'


def build_options(info, codec_pref='compat', merge_audio=None, hdr=True):
    """merge_audio：限制哪些音轨可以和视频合并（手机版：MP4 只能配 AAC）；所有音轨仍会列在「仅音频」里。
    hdr=False：不列 HDR / 杜比视界版本（设置里关掉「解析 HDR 版本」）。"""
    formats = info.get('formats') or ([info] if info.get('url') else [])
    duration = info.get('duration')
    rank = _CODEC_RANK.get(codec_pref, _CODEC_RANK['compat'])

    images = [f for f in formats if f.get('vcodec') == 'none' and f.get('acodec') == 'none'
              and f.get('ext') in ('jpg', 'jpeg', 'png', 'webp')]
    if images:
        f = images[-1]
        return [QualityOption(label=f'图片 · {f["ext"].upper()}', tag='图片', format=f.get('format_id') or 'best',
                              kind='image', streams=[(f.get('format_id'), 0)])]

    audios = [f for f in formats if f.get('vcodec') == 'none' and f.get('acodec') not in (None, 'none')]
    # 无损 / 杜比音轨只作为单独的音频选项，合并视频时用兼容性好的 AAC/Opus
    special_audios = [f for f in audios if _special_audio(f)]
    merge_audios = [f for f in audios if not _special_audio(f)] or audios
    if merge_audio:
        merge_audios = [f for f in merge_audios if merge_audio(f)]

    def audio_key(f):
        is_aac = (f.get('acodec') or '').startswith('mp4a') or f.get('ext') == 'm4a'
        return (-(f.get('language_preference') or 0) if (f.get('language_preference') or 0) > -1 else 99,
                'drc' in (f.get('format_id') or ''),
                0 if (is_aac or codec_pref != 'compat') else 1,
                -(f.get('abr') or f.get('tbr') or 0))
    best_audio = min(merge_audios, key=audio_key) if merge_audios else None

    # B站的清晰度是官方档位（1080P 高码率 / 1080P 60帧 / 4K / HDR / 杜比视界…），同分辨率也要分开列
    tiered = (info.get('extractor_key') or '').startswith('BiliBili') and any(
        isinstance(f.get('quality'), int) and f.get('format') and _is_video(f) for f in formats)

    groups = {}
    for f in formats:
        if not _is_video(f):
            continue
        res = short_side(f)
        fps = f.get('fps') or 0
        fps_tag = round(fps) if fps and fps > 32 else 0
        # HDR 和普通版本分开列（同分辨率同帧率，不然 HDR 会被合并掉）；B站的 HDR 本来就是单独的档位
        dr = (f.get('dynamic_range') or 'SDR').upper()
        if not hdr and dr != 'SDR':
            continue
        key = (f.get('quality') or 0, 0, '') if tiered else (res, fps_tag, '' if dr == 'SDR' else dr)
        fam = codec_family(f.get('vcodec'))
        score = (rank.get(fam, 3), _proto_rank(f), -(f.get('tbr') or 0), -_size(f, duration))
        if key not in groups or score < groups[key][0]:
            groups[key] = (score, f)

    options = []
    for (_, fps_tag, dr), (_, f) in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1], bool(kv[0][2])),
                                           reverse=True):
        fam = codec_family(f.get('vcodec'))
        res = short_side(f)
        note = f.get('format_note') if f.get('format_note') in ('原画',) else ''
        note_raw = f.get('format_note') or ''
        res_txt = f'{res}p{fps_tag or ""}' if res else (
            '带水印版' if 'watermark' in note_raw.lower() else (note_raw or f.get('format_id') or '默认'))
        if dr:
            res_txt += ' HDR' if dr == 'HDR10' else f' {dr}'      # HDR10 / HLG / DV（杜比视界）
        tag = f'原画 {res_txt}' if note else res_txt
        if tiered:
            # B站的档位名：「杜比视界」「HDR 真彩」看不出分辨率，补上；高帧率也标出来（如「4K 超高清 120帧」）
            tag = f['format']
            if not any(ch.isdigit() for ch in tag) and res:
                tag += f' {_res_name(res)}'
            fps = f.get('fps') or 0
            if fps > 32 and '帧' not in tag:
                tag += f' {round(fps)}帧'
        fid = f.get('format_id')
        streams = [(fid, _size(f, duration))]
        rate = _rate(f)
        if _has_audio(f) or not best_audio:
            selector = fid
        else:
            selector = f'{fid}+{best_audio["format_id"]}'
            streams.append((best_audio['format_id'], _size(best_audio, duration)))
            rate = rate + _rate(best_audio) if rate else 0
        total = sum(s for _, s in streams)
        if not rate and total and duration:
            rate = total * 8 / duration / 1000
        parts = [tag]
        if fam:
            parts.append(_CODEC_LABEL.get(fam, fam))
        if rate:
            parts.append(fmt_rate(rate))
        parts.append(fmt_size(total) if total else '大小未知')
        if not _has_audio(f) and not best_audio:
            parts.append('无音轨')
        options.append(QualityOption(label=' · '.join(parts), tag=tag.replace(' ', ''), format=selector,
                                     kind='video', res=res, size=total, streams=streams))

    # 仅音频：每条音轨都列出来（编码 · 码率 · 大小），无损 / 杜比放最前；同编码同码率的只留一个
    def audio_label(a, sz, ext=None):
        rate = fmt_rate(a.get('abr') or a.get('tbr'))
        ext = a.get('ext', '').upper() if ext is None else ext
        return ' · '.join(x for x in ('仅音频', _audio_name(a, info), rate, ext, fmt_size(sz)) if x)

    for a in sorted(special_audios, key=lambda f: -(f.get('tbr') or 0)):
        sz = _size(a, duration)
        lossless = (a.get('acodec') or '').lower().startswith(('flac', 'alac'))
        tag = 'FLAC' if lossless else _audio_name(a, info)
        # 无损音轨下载后转存成 .flac（电脑版用 ffmpeg，不重新编码）
        options.append(QualityOption(label=audio_label(a, sz, 'FLAC' if lossless else None), tag=tag,
                                     format=a['format_id'], kind='audio', size=sz, streams=[(a['format_id'], sz)],
                                     convert='flac' if lossless else ''))
    # 合并视频用的那条（best_audio）排第一；可以和视频合并的音轨受 merge_audio 限制，单独下载不受限
    seen = set()
    plain = [a for a in audios if not _special_audio(f=a)]
    plain.sort(key=lambda f: (f is not best_audio, audio_key(f)))
    for a in plain:
        k = (_audio_name(a, info), round((a.get('abr') or a.get('tbr') or 0) / 8))
        if k in seen:
            continue
        seen.add(k)
        sz = _size(a, duration)
        abr = a.get('abr') or a.get('tbr')
        tag = '音频' if a is best_audio else f'{_audio_name(a, info)}{abr:.0f}k' if abr else _audio_name(a, info)
        options.append(QualityOption(label=audio_label(a, sz), tag=tag, format=a['format_id'], kind='audio', size=sz,
                                     streams=[(a['format_id'], sz)]))
    if formats:
        src = best_audio or (min(groups.values(), key=lambda g: _size(g[1], duration))[1] if groups else None)
        if src is not None:
            sz = _size(src, duration)
            options.append(QualityOption(label='仅音频 · 转为 MP3', tag='MP3', format=src.get('format_id') or 'best',
                                         kind='audio', size=sz, streams=[(src.get('format_id'), sz)],
                                         convert='mp3'))
    if not options:
        options.append(QualityOption(label='默认', tag='默认', format='best', kind='video'))
    return options


# ---------------------------------------------------------------- 字幕

SUB_ZH = ('zh-Hans', 'zh-CN', 'zh', 'zh-SG', 'ai-zh', 'zh-Hant', 'zh-TW', 'zh-HK')
SUB_NAMES = {'zh-Hans': '简中', 'zh-CN': '简中', 'zh': '中文', 'zh-SG': '简中', 'ai-zh': 'AI中文', 'zh-Hant': '繁中',
             'zh-TW': '繁中', 'zh-HK': '繁中', 'en': '英文', 'ja': '日文', 'ko': '韩文', 'fr': '法文', 'de': '德文',
             'es': '西文', 'ru': '俄文'}


def sub_name(lang, auto=False):
    base = lang[:-5] if lang.endswith('-orig') else lang
    name = SUB_NAMES.get(base) or SUB_NAMES.get(base.split('-')[0]) or base
    return name + ('(自动)' if auto else '')


def pick_subtitles(info, mode='zh_orig'):
    """按设置挑选要下载的字幕：返回 (人工字幕语言, 自动/机翻字幕语言)。"""
    subs = {k: v for k, v in (info.get('subtitles') or {}).items() if k not in ('danmaku', 'live_chat')}
    auto = info.get('automatic_captions') or {}
    if mode == 'all':
        return list(subs)[:20], []
    human, autos = [], []
    zh = next((lang for lang in SUB_ZH if lang in subs), None)
    if zh:
        human.append(zh)
    else:
        a = next((lang for lang in ('zh-Hans', 'zh-CN', 'zh-Hant') if lang in auto), None)
        if a:
            autos.append(a)
    orig = (info.get('language') or '').split('-')[0]
    if (mode == 'zh_orig' or not (human or autos)) and orig and orig != 'zh':
        o = next((lang for lang in subs if lang.split('-')[0] == orig), None)
        if o:
            human.append(o)
        else:
            a = next((lang for lang in (f'{orig}-orig', orig) if lang in auto), None)
            if a:
                autos.append(a)
    if not (human or autos) and subs:
        human.append(next(iter(subs)))
    return human, autos


def has_danmaku(info):
    return 'danmaku' in (info.get('subtitles') or {})


# ---------------------------------------------------------------- 解析

@dataclass
class ParsedVideo:
    url: str
    info: dict
    title: str
    platform: str
    duration: float
    uploader: str
    options: list
    locked: str = ''       # 需要登录/会员才能获取的清晰度（B站）
    collection: dict = None    # 所属的合集/分P/播放列表（见 collection.py）


class Parser:
    """在线程池里解析链接；播放列表会展开成多个视频。"""

    def __init__(self, get_settings, log, workers=4):
        self.get_settings = get_settings
        self.log = log
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='parse')

    def submit(self, url, on_result, on_error, on_expand=None, probe=True, on_status=None, extra=None):
        """on_status(url, 文字)：解析前要等一会儿的准备工作（如第一次解析 YouTube 时下载组件）显示在卡片上。
        extra：网页嗅探到的资源地址，解析 / 下载时要带上浏览器里那个页面的东西：
               {'headers': 请求头, 'cookies': [Cookie], 'title': 页面标题, 'page': 页面地址, 'id': 稳定的 ID}"""
        self.pool.submit(self._run, url, on_result, on_error, on_expand, probe, on_status, extra)

    def _run(self, url, on_result, on_error, on_expand, probe=True, on_status=None, extra=None):
        settings = self.get_settings()
        logger = _Logger(self.log)
        extra = extra or {}
        ensure_js_runtime(url, settings, (lambda text: on_status(url, text)) if on_status else None, self.log)
        site_cookies_fresh(url, self.log)       # 用内置浏览器登录过这个网站：Cookie 有一阵没读了就先刷新
        opts = base_options(settings, logger)
        opts['extract_flat'] = 'in_playlist'
        opts['writesubtitles'] = True      # 只取字幕/弹幕列表，不会下载
        if extra.get('headers'):
            # 防盗链的网站要看 Referer / Origin：这些请求头会被 yt-dlp 记进每个格式里，下载时也带着
            opts['http_headers'] = dict(extra['headers'])
        refreshed = False
        for attempt in (1, 2, 3):
            try:
                with new_ydl(dict(opts), extra.get('cookies') or ()) as ydl:
                    info = ydl.sanitize_info(ydl.extract_info(url, download=False))
                break
            except Exception as e:  # noqa: BLE001 - 任何异常都要回到界面
                msg, raw = friendly_error(e)
                # 网络偶发超时/断连时自动再试一次
                if attempt == 1 and msg.startswith('网络连接失败'):
                    self.log('warning', f'解析 {url} 网络错误，重试中：{raw[:120]}')
                    time.sleep(1.5)
                    continue
                # 报「需要登录」而这个网站登录过：从内置浏览器重新读一次 Cookie 再试；还不行就是登录过期了
                if needs_login(raw) and site_logged_in(url):
                    if not refreshed:
                        refreshed = True
                        self.log('info', f'解析 {url} 提示需要登录，从内置浏览器刷新登录信息后重试')
                        if site_cookies_fresh(url, self.log, force=True):
                            continue
                    msg = f'{LOGIN_EXPIRED}：请重新登录这个网站（点「重新登录」，或 设置 → 网络与登录）'
                on_error(url, msg, raw)
                return
        if info.get('_type') == 'playlist':
            entries = [e for e in (info.get('entries') or []) if e]
            if not entries:
                on_error(url, '播放列表为空', '')
                return
            refs = []
            for e in entries:
                if e.get('formats') or (e.get('url') and e.get('_type', 'video') == 'video'):
                    # 已经是完整视频（如一条推文里的多个视频、抖音图集）：直接作为结果
                    on_result(url, self._make(url, yt().YoutubeDL.sanitize_info(e, True), settings))
                elif e.get('url'):
                    refs.append(e)
            col = collection.from_playlist({'title': info.get('title'), 'entries': refs}) if refs else None
            if col and on_expand:
                on_expand(url, col)         # 交给界面弹出选集窗口，用户勾选后再逐个解析
            elif not refs and not any(e.get('formats') for e in entries):
                on_error(url, '无法展开播放列表', '')
            return
        if extra:
            # 直接给地址解析出来的标题是文件名（index、master…），ID 也容易撞：换成页面标题和按地址算的稳定 ID
            info = dict(info, **{k: v for k, v in (('title', extra.get('title')), ('id', extra.get('id')),
                                                 ('webpage_url', extra.get('page'))) if v})
        video = self._make(url, yt().YoutubeDL.sanitize_info(info, True), settings, logger.locked_formats)
        if not settings.show_hdr and not any(o.kind == 'video' for o in video.options) and hdr_only(video.info):
            on_error(url, HDR_ONLY_MSG, '')
            return
        if probe:      # 从选集里加入的视频不用再查一遍所属合集
            video.collection = collection.probe(info, url, lambda u: self._flat(u, settings))
        on_result(url, video)

    @staticmethod
    def _flat(url, settings):
        opts = {**base_options(settings, _Logger()), 'extract_flat': True, 'noplaylist': False,
                'playlistend': collection.MAX_ENTRIES}
        with new_ydl(opts) as ydl:
            return ydl.extract_info(url, download=False)

    @staticmethod
    def _make(url, info, settings, locked=''):
        return ParsedVideo(
            url=url, info=info,
            title=info.get('title') or info.get('id') or url,
            platform=platform_name(info),
            duration=info.get('duration') or 0,
            uploader=info.get('uploader') or info.get('channel') or '',
            options=build_options(info, settings.codec_preference, hdr=settings.show_hdr),
            locked=locked,
        )


# ---------------------------------------------------------------- 下载

QUEUED, RUNNING, MERGING, PAUSED, DONE, EXISTS, FAILED, CANCELED = (
    'queued', 'running', 'merging', 'paused', 'done', 'exists', 'failed', 'canceled')
STATUS_TEXT = {QUEUED: '等待中', RUNNING: '下载中', MERGING: '处理中', PAUSED: '已暂停', DONE: '已完成',
               EXISTS: '已存在', FAILED: '失败', CANCELED: '已取消'}


class DownloadTask:
    _next_id = 1

    def __init__(self, video: ParsedVideo, option: QualityOption, out_dir, save_cover=False):
        self.id = DownloadTask._next_id
        DownloadTask._next_id += 1
        self.video = video
        self.option = option
        self.out_dir = out_dir
        self.status = QUEUED
        self.progress = 0.0
        self.speed = 0
        self.eta = None
        self.downloaded = 0
        self.total = option.size
        self.filepath = ''
        self.error = ''
        self.error_raw = ''
        self.stage = ''
        self._stop = None          # None / 'pause' / 'cancel'
        self._stream_progress = {}
        self._expected_name = ''
        self.save_cover = save_cover and bool(video.info.get('thumbnail'))
        self.overwrite = False     # 用户确认「重新下载」时覆盖已有文件
        self.save_subs = True
        self.save_danmaku = True
        self.extras = []
        self.burned_path = ''
        self.danmaku_xml = ''
        self.section = None        # (开始秒, 结束秒)：只下载这一段
        self.precise = False       # 精确剪切（切点附近重新编码）

    @property
    def tag(self):
        """清晰度标签；片段任务带上起止时间（写进文件名、历史记录）。"""
        if not self.section:
            return self.option.tag
        def t(sec):
            sec = int(sec)
            h, rem = divmod(sec, 3600)
            m, s = divmod(rem, 60)
            return f'{h}h{m:02d}m{s:02d}s' if h else f'{m}m{s:02d}s'
        return f'{self.option.tag}] [片段 {t(self.section[0])}-{t(self.section[1])}'

    @property
    def active(self):
        return self.status in (QUEUED, RUNNING, MERGING)


_EMBED_EXTS = {'mp4', 'm4a', 'm4v', 'mov', 'mp3', 'mkv', 'mka', 'ogg', 'opus', 'flac'}


def _final_ext(task):
    """下载完成后文件的扩展名（用来判断能不能嵌入封面）。"""
    if task.option.convert:
        return task.option.convert
    if '+' in task.option.format:
        return 'mp4'
    fmt = next((f for f in task.video.info.get('formats') or [] if f.get('format_id') == task.option.format),
               task.video.info)
    return (fmt.get('ext') or '').lower()


class DownloadManager:
    def __init__(self, get_settings, on_update, log):
        self.get_settings = get_settings
        self.on_update = on_update      # 回调(task)，在下载线程中调用
        self.log = log
        self.tasks = []
        self._lock = threading.Lock()
        self._running = 0

    # 队列控制 ----------------------------------------------------------
    def add(self, task):
        with self._lock:
            self.tasks.append(task)
        self.on_update(task)
        self._schedule()

    def restore(self, task):
        """恢复上次未完成的任务（已暂停，不自动开始）。"""
        with self._lock:
            self.tasks.append(task)
        self.on_update(task)

    def pause(self, task):
        if task.status == QUEUED:
            task.status = PAUSED
            self.on_update(task)
        elif task.status == RUNNING:
            task._stop = 'pause'

    def resume(self, task):
        if task.status in (PAUSED, FAILED, CANCELED):
            task.status, task._stop, task.error = QUEUED, None, ''
            self.on_update(task)
            self._schedule()

    def cancel(self, task):
        if task.status in (RUNNING, MERGING):
            task._stop = 'cancel'
        elif task.status in (QUEUED, PAUSED, FAILED):
            task.status = CANCELED
            self._cleanup_partial(task)
            self.on_update(task)

    def remove(self, task):
        with self._lock:
            if task in self.tasks and task.status not in (RUNNING, MERGING):
                self.tasks.remove(task)
                return True
        return False

    def _schedule(self):
        limit = self.get_settings().max_concurrent
        started = []
        with self._lock:
            while self._running < limit:
                task = next((t for t in self.tasks if t.status == QUEUED), None)
                if not task:
                    break
                task.status = RUNNING
                self._running += 1
                started.append(task)
        for task in started:
            self.on_update(task)
            threading.Thread(target=self._worker, args=(task,), daemon=True, name=f'dl-{task.id}').start()

    def _worker(self, task):
        try:
            self._download(task)
        finally:
            with self._lock:
                self._running -= 1
            self.on_update(task)
            self._schedule()

    # 实际下载 ----------------------------------------------------------
    def _build_opts(self, task, logger):
        s = self.get_settings()
        opts = base_options(s, logger)
        os.makedirs(task.out_dir, exist_ok=True)
        tmpl = FILENAME_TEMPLATES[s.filename_template][1].replace('{quality}', task.tag.replace('%', '%%'))
        # 自动分文件夹：平台名是我们自己的中文名，作者交给 yt-dlp 模板（会自动去掉非法字符）
        platform_dir = re.sub(r'[\\/:*?"<>|]+', ' ', task.video.platform or '其它').strip().replace('%', '%%')
        if s.subfolder == 'platform':
            tmpl = os.path.join(platform_dir, tmpl)
        elif s.subfolder == 'platform_uploader':
            tmpl = os.path.join(platform_dir, '%(uploader,channel,creator|未知作者).40s', tmpl)
        opts.update({
            'format': task.option.format,
            'outtmpl': {'default': os.path.join(task.out_dir, tmpl)},
            'concurrent_fragment_downloads': s.fragment_threads,
            'continuedl': True,
            'progress_hooks': [lambda d: self._on_progress(task, d)],
            'postprocessor_hooks': [lambda d: self._on_postprocess(task, d)],
            'merge_output_format': 'mp4',
        })
        if task.overwrite:
            opts['overwrites'] = True
        if task.section:
            # 只下载一段：yt-dlp 交给 ffmpeg 按时间跳转读取，不用下完整视频
            from yt_dlp.utils import download_range_func
            opts['download_ranges'] = download_range_func(None, [tuple(task.section)])
            opts['force_keyframes_at_cuts'] = task.precise
        embed = s.embed_cover and bool(task.video.info.get('thumbnail')) and task.option.kind != 'image' \
            and _final_ext(task) in _EMBED_EXTS
        pps = []
        if task.save_cover or embed:
            # 封面先转成 JPG（yt-dlp 负责请求头/代理，ffmpeg 负责把 webp 等转成 jpg）
            opts['writethumbnail'] = True
            pps.append({'key': 'FFmpegThumbnailsConvertor', 'format': 'jpg', 'when': 'before_dl'})
        if task.option.convert:
            pps.append({'key': 'FFmpegExtractAudio', 'preferredcodec': task.option.convert, 'preferredquality': '0'})
        if embed:
            # 写进 MP4/MP3 后，资源管理器的缩略图和播放器里都能看到封面；勾选了「保存封面」时保留 JPG
            pps.append({'key': 'EmbedThumbnail', 'already_have_thumbnail': task.save_cover})
        if pps:
            opts['postprocessors'] = pps
        if task.option.convert:
            opts['final_ext'] = task.option.convert
        return opts

    def _download(self, task):
        task.stage, task.error, task._stream_progress = '', '', {}
        task.speed, task.eta = 0, None
        logger = _Logger(self.log, f'[任务{task.id}] ')
        info = task.video.info
        y = yt()
        site_cookies_fresh(info.get('webpage_url') or task.video.url, self.log)
        for attempt in (1, 2):
            try:
                with new_ydl(self._build_opts(task, logger)) as ydl:
                    task._expected_name = ydl.prepare_filename(info)
                    job = copy.deepcopy(info)
                    for host in pick_fast_urls(job, [fid for fid, _ in task.option.streams], ydl):
                        self.log('info', f'[任务{task.id}] 已选用较快的下载节点 {host}')
                    result = ydl.process_ie_result(job, download=True)
                path = (result.get('requested_downloads') or [{}])[0].get('filepath') or result.get('filepath')
                if path:
                    task.filepath = path
                self._download_extras(task, logger)
                task.progress, task.speed, task.eta, task.stage = 1.0, 0, None, ''
                task.status = EXISTS if logger.already_downloaded else DONE
                return
            except y.DownloadCancelled:
                break
            except Exception as e:  # noqa: BLE001 - 任何异常都要回到界面
                if task._stop:
                    break
                error = e
                # 只是封面嵌入失败时视频本身已经下好了，不算失败
                if 'Thumbnail' in str(e) and task.filepath and os.path.exists(task.filepath):
                    self.log('warning', f'[任务{task.id}] 封面嵌入失败（视频已下载）：{e}')
                    task.progress, task.speed, task.eta, task.stage = 1.0, 0, None, ''
                    task.status = DONE
                    return
            # 直链过期时用原网址重新解析一次再试
            if attempt == 1 and isinstance(error, y.DownloadError) and info.get('webpage_url') \
                    and re.search(r'HTTP Error 40[34]|expired|Requested format', str(error)):
                self.log('warning', f'[任务{task.id}] 下载地址可能已过期，重新解析后重试')
                try:
                    ensure_js_runtime(info['webpage_url'], self.get_settings(), None, self.log)
                    with new_ydl(base_options(self.get_settings(), logger)) as ydl:
                        info = y.YoutubeDL.sanitize_info(
                            ydl.extract_info(info['webpage_url'], download=False), True)
                    continue
                except Exception as e:  # noqa: BLE001
                    error = e
            task.status = FAILED
            task.error, task.error_raw = friendly_error(error)
            task.speed, task.eta = 0, None
            return
        # 被暂停或取消
        if task._stop == 'cancel':
            task.status = CANCELED
            self._cleanup_partial(task)
        else:
            task.status = PAUSED
        task.speed, task.eta = 0, None

    def _download_extras(self, task, logger):
        """字幕和弹幕单独下载：失败只记警告，不影响已经下好的视频。"""
        s = self.get_settings()
        info = task.video.info
        if task.option.kind == 'image' or not task.filepath:
            return
        human, autos = pick_subtitles(info, s.sub_langs) if s.save_subs and task.save_subs and not task.section \
            else ([], [])
        want_dm = s.save_danmaku and task.save_danmaku and has_danmaku(info)
        if not (human or autos or want_dm):
            return
        task.status, task.stage = MERGING, '下载字幕/弹幕'
        self.on_update(task)
        base = os.path.splitext(task.filepath)[0]
        opts = base_options(s, logger)
        opts.update({
            'skip_download': True, 'writesubtitles': True, 'writeautomaticsub': bool(autos),
            'subtitleslangs': human + autos + (['danmaku'] if want_dm else []),
            'subtitlesformat': 'srt/best', 'ignoreerrors': True, 'overwrites': True,
            'format': task.option.format,
            'outtmpl': {'default': base.replace('%', '%%') + '.%(ext)s'},
        })
        try:
            with new_ydl(opts) as ydl:
                ydl.process_ie_result(copy.deepcopy(info), download=True)
        except Exception as e:  # noqa: BLE001
            self.log('warning', f'[任务{task.id}] 字幕/弹幕下载失败：{e}')
        got = [lang for lang in human + autos
               if glob.glob(glob.escape(f'{base}.{lang}.') + '*')]
        if got:
            self.log('info', f'[任务{task.id}] 已保存字幕：' + '、'.join(sub_name(lang, lang in autos) for lang in got))
        xml = base + '.danmaku.xml'
        if os.path.exists(xml):
            fmt = next((f for f in info.get('formats') or [] if f.get('format_id') == task.option.streams[0][0]),
                       info) if task.option.streams else info
            try:
                n = danmaku.convert_file(xml, base + '.danmaku.ass', fmt.get('width'), fmt.get('height'),
                                         section=task.section)
                task.danmaku_xml = self._stash_danmaku_xml(xml, base, task.section)
                self.log('info', f'[任务{task.id}] 已保存弹幕 {n} 条 → {os.path.basename(base)}.danmaku.ass')
            except Exception as e:  # noqa: BLE001
                self.log('warning', f'[任务{task.id}] 弹幕转换失败（保留原始 XML）：{e}')
        task.extras = got + (['danmaku'] if os.path.exists(base + '.danmaku.ass') else [])
        if 'danmaku' in task.extras and s.burn_danmaku and task.option.kind == 'video':
            self._burn_danmaku(task, base, s)
        task.stage = ''

    def _burn_danmaku(self, task, base, s):
        """生成「弹幕版」：弹幕画进视频并升到 60/120 帧，VLC 等任何播放器里都和网页版一样顺滑。"""
        out = f'{base} [弹幕版].mp4'
        if os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(task.filepath) and not task.overwrite:
            task.burned_path = out
            task.extras.append('burned')
            return
        ff = os.path.join(ffmpeg_dir() or '', 'ffmpeg.exe')
        if not os.path.isfile(ff):
            return
        fps = s.danmaku_fps if s.danmaku_fps in (60, 120) else 60
        if fps > 60 and s.danmaku_4k_60:
            # 4K 120 帧：软件编码非常慢，而且生成的是 H.264 Level 6，很多手机、电视的硬件解码器放不了
            fmt = next((f for f in task.video.info.get('formats') or []
                        if task.option.streams and f.get('format_id') == task.option.streams[0][0]), task.video.info)
            if min(fmt.get('width') or 0, fmt.get('height') or 0) >= 2160:
                fps = 60
                self.log('info', f'[任务{task.id}] 4K 视频的弹幕版按 60 帧生成：4K 120 帧编码非常慢，生成的文件很多手机、电视也放不了'
                                 '（想要 120 帧可以在 设置 → 字幕与弹幕 里关掉「4K 视频的弹幕版最高 60 帧」）')
        task.stage = f'弹幕版{fps}帧'
        self.on_update(task)
        last = [0.0]

        def progress(p):
            now = time.monotonic()
            if now - last[0] >= 0.3:
                last[0] = now
                task.stage = f'弹幕版{fps}帧 {p * 100:.0f}%'
                self.on_update(task)
        t0 = time.monotonic()
        try:
            dur = (task.section[1] - task.section[0]) if task.section else task.video.info.get('duration')
            danmaku.burn(ff, task.filepath, base + '.danmaku.ass', out, fps=fps,
                         duration=dur, on_progress=progress,
                         should_stop=lambda: bool(task._stop))
            task.burned_path = out
            task.extras.append('burned')
            self.log('info', f'[任务{task.id}] 已生成弹幕版（{fps}帧，用时 {time.monotonic() - t0:.0f} 秒）→ '
                             f'{os.path.basename(out)}')
            if not s.keep_original:
                self._keep_only_burned(task, base, out)
        except InterruptedError:
            raise yt().DownloadCancelled()
        except Exception as e:  # noqa: BLE001 - 弹幕版失败不影响原视频
            self.log('warning', f'[任务{task.id}] 生成弹幕版失败：{e}')

    DANMAKU_DIR = '弹幕数据'

    @classmethod
    def _stash_danmaku_xml(cls, xml, base, section=None):
        """原始弹幕数据单独存到视频旁边的「弹幕数据」文件夹（文件名与视频相同），留给以后的播放器做弹幕开关。
        片段任务只保留片段时间内的弹幕，并把时间平移到从 0 开始，和片段视频对齐。"""
        folder = os.path.join(os.path.dirname(base), cls.DANMAKU_DIR)
        os.makedirs(folder, exist_ok=True)
        target = os.path.join(folder, os.path.basename(base) + '.xml')
        if section:
            danmaku.write_xml_section(xml, target, section)
            os.remove(xml)
        else:
            os.replace(xml, target)
        return target

    def _keep_only_burned(self, task, base, out):
        """只保留弹幕版：删掉原视频和外挂弹幕，字幕改名配给弹幕版（播放器才会自动加载）。"""
        new_base = os.path.splitext(out)[0]
        for path in (task.filepath, base + '.danmaku.ass'):
            try:
                os.remove(path)
            except OSError:
                pass
        for sub in glob.glob(glob.escape(base) + '.*.srt') + glob.glob(glob.escape(base) + '.*.vtt'):
            try:
                os.replace(sub, new_base + sub[len(base):])
            except OSError:
                pass
        if task.danmaku_xml and os.path.exists(task.danmaku_xml):
            # 弹幕数据跟着改名，和弹幕版视频同名
            new_xml = os.path.join(os.path.dirname(task.danmaku_xml), os.path.basename(new_base) + '.xml')
            try:
                os.replace(task.danmaku_xml, new_xml)
                task.danmaku_xml = new_xml
            except OSError:
                pass
        task.filepath = out
        self.log('info', f'[任务{task.id}] 已删除原视频，只保留弹幕版（原始弹幕数据在「{self.DANMAKU_DIR}」文件夹）')

    def _on_progress(self, task, d):
        if task._stop:
            raise yt().DownloadCancelled()
        status = d.get('status')
        fid = (d.get('info_dict') or {}).get('format_id') or '?'
        if status == 'downloading':
            if task.status == MERGING:      # 下载前的封面转换等结束后回到「下载中」
                task.status, task.stage = RUNNING, ''
            done = d.get('downloaded_bytes') or 0
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            task._stream_progress[fid] = (done, total)
            task.speed = d.get('speed') or 0
            task.eta = d.get('eta')
        elif status == 'finished':
            total = d.get('total_bytes') or d.get('downloaded_bytes') or 0
            task._stream_progress[fid] = (total, total)
        else:
            return
        self._recalc(task)
        now = time.monotonic()
        if status == 'finished' or now - getattr(task, '_last_emit', 0) >= 0.25:
            task._last_emit = now
            self.on_update(task)

    @staticmethod
    def _recalc(task):
        streams = task.option.streams or [('?', 0)]
        pairs = [(task._stream_progress.get(fid, (0, 0))[0], task._stream_progress.get(fid, (0, 0))[1] or est)
                 for fid, est in streams]
        if len(streams) == 1 and task._stream_progress and streams[0][0] not in task._stream_progress:
            pairs = [next(iter(task._stream_progress.values()))]
        done_sum = sum(d for d, _ in pairs)
        total_sum = sum(t for _, t in pairs)
        task.downloaded, task.total = done_sum, total_sum
        if all(t for _, t in pairs):
            task.progress = min(done_sum / total_sum, 1.0)
        else:
            task.progress = sum(min(d / t, 1.0) if t else 0 for d, t in pairs) / len(pairs)
        if len(streams) > 1:
            idx = max(1, sum(1 for fid, _ in streams if fid in task._stream_progress))
            task.stage = f'{"视频" if idx == 1 else "音频"} {idx}/{len(streams)}'
        else:
            task.stage = ''

    # 后处理器名字是 yt-dlp 的 pp_key()（类名去掉 FFmpeg 前缀和 PP 后缀）
    _PP_NAMES = {'Merger': '合并音视频', 'ExtractAudio': '转换音频', 'FixupM3u8': '修复封装',
                 'FixupDuplicateMoov': '修复封装', 'FixupStretched': '修复封装', 'FixupTimestamp': '修复封装',
                 'VideoConvertor': '转换格式', 'VideoRemuxer': '转换格式', 'EmbedThumbnail': '写入封面'}
    # 下载开始前就会跑、而且很快的后处理器：只显示阶段，不改状态
    _PRE_DL_PPS = {'ThumbnailsConvertor', 'MoveFiles'}

    def _on_postprocess(self, task, d):
        pp = d.get('postprocessor') or ''
        if pp in self._PRE_DL_PPS:
            return
        if d.get('status') == 'started':
            task.status = MERGING
            task.stage = self._PP_NAMES.get(pp, '处理中')
            task.speed, task.eta = 0, None
            self.on_update(task)
        elif d.get('status') == 'finished':
            path = (d.get('info_dict') or {}).get('filepath')
            if path:
                task.filepath = path

    def _cleanup_partial(self, task):
        base = os.path.splitext(task._expected_name)[0] if task._expected_name else ''
        if not base:
            return
        esc = glob.escape(base)
        for pattern in (esc + '*.part', esc + '*.part-Frag*', esc + '*.ytdl', esc + '.f*.*', esc + '.temp.*'):
            for p in glob.glob(pattern):
                try:
                    os.remove(p)
                except OSError:
                    pass

    def running_count(self):
        return sum(1 for t in self.tasks if t.status in (RUNNING, MERGING))
