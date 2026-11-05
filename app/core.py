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


from . import bilibili, collection, danmaku
from .config import DATA, FILENAME_TEMPLATES, ROOT, ffmpeg_dir

# yt-dlp 首次导入较慢（冷启动可达数秒），延迟到第一次使用或后台预加载时再导入，让窗口先显示出来
_yt = None
_yt_lock = threading.Lock()


def yt():
    global _yt
    if _yt is None:
        with _yt_lock:
            if _yt is None:
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
     '经常出现的话，把 YouTube 的 cookies 放进 cookies 文件夹（设置 → 网络与登录 有说明）'),
    (r'Log ?in|login|logged|sign in|cookies|authentication|premium member|会员',
     '该内容需要登录：把该网站导出的 cookies.txt 放进 cookies 文件夹（设置 → 网络与登录 里打开；或已过期，请重新导出）'),
    (r'Private video', '视频为私密内容，需要有权限的账号 cookies'),
    (r'geo.?restrict|not available in your (?:country|region)|in your country', '地区限制：请尝试更换代理节点'),
    (r'Video unavailable|has been removed|This video is not available|已删除|不存在',
     '视频不可用（可能已删除、私密或地区限制）'),
    (r'HTTP Error 403|Forbidden', '服务器拒绝访问 (403)，可稍后重试或配置 cookies'),
    (r'HTTP Error 404', '资源不存在 (404)'),
    (r'HTTP Error 429|Too Many Requests', '请求过于频繁 (429)，请稍后再试'),
    (r'timed out|Timeout|Connection|getaddrinfo|Network|SSL|EOF occurred|ProxyError|Unable to download',
     '网络连接失败：请检查网络或代理设置'),
    (r'ffmpeg|ffprobe', 'ffmpeg 处理失败（请确认程序目录下的 ffmpeg 文件夹完整）'),
    (r'No space left|磁盘', '磁盘空间不足'),
]


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


def _js_runtimes():
    runtimes = {}
    for name in ('deno', 'node', 'bun'):
        bundled = os.path.join(ROOT, 'runtime', f'{name}.exe')
        if os.path.isfile(bundled):
            runtimes[name] = {'path': bundled}
        elif shutil.which(name):
            runtimes[name] = {}
    return runtimes or {'deno': {}}


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
        opts['cookiesfrombrowser'] = (settings.cookie_browser,)
    elif settings.cookie_mode == 'file' and settings.cookie_file and os.path.isfile(settings.cookie_file):
        opts['cookiefile'] = settings.cookie_file
    return opts


COOKIE_DIR = os.path.join(DATA, 'cookies')
_cookie_cache = {}      # 路径 -> (修改时间, cookies 列表)


def folder_cookies():
    """cookies 文件夹里所有 Netscape 格式的 .txt（B站扫码登录、浏览器扩展导出的 TikTok 等）都自动生效。"""
    import http.cookiejar
    cookies = []
    try:
        names = sorted(n for n in os.listdir(COOKIE_DIR)
                       if n.lower().endswith('.txt') and n.lower() != 'bilibili.txt')
    except OSError:
        names = []
    cookies.extend(bilibili.load_cookies())     # B站扫码登录（保存在用户数据目录）
    if not names:
        return cookies
    for name in names:
        path = os.path.join(COOKIE_DIR, name)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        cached = _cookie_cache.get(path)
        if not cached or cached[0] != mtime:
            jar = http.cookiejar.MozillaCookieJar(path)
            try:
                jar.load(ignore_discard=True, ignore_expires=True)
                cached = (mtime, list(jar))
            except (OSError, http.cookiejar.LoadError):
                cached = (mtime, [])
            _cookie_cache[path] = cached
        cookies.extend(cached[1])
    return cookies


def cookie_file_names():
    try:
        return sorted(n for n in os.listdir(COOKIE_DIR) if n.lower().endswith('.txt') and n.lower() != 'bilibili.txt')
    except OSError:
        return []


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


def new_ydl(opts):
    y = yt()
    ydl = y.YoutubeDL(opts, auto_init=False)
    y.extractors.register(ydl)
    extra = folder_cookies()
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


def _proto_rank(f):
    p = f.get('protocol') or ''
    if p in ('https', 'http'):
        return 0
    if 'dash' in p:
        return 1
    if 'm3u8' in p:
        return 2
    return 3


def build_options(info, codec_pref='compat'):
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
        key = (f.get('quality') or 0, 0) if tiered else (res, fps_tag)
        fam = codec_family(f.get('vcodec'))
        score = (rank.get(fam, 3), _proto_rank(f), -(f.get('tbr') or 0), -_size(f, duration))
        if key not in groups or score < groups[key][0]:
            groups[key] = (score, f)

    options = []
    for (_, fps_tag), (_, f) in sorted(groups.items(), key=lambda kv: kv[0], reverse=True):
        fam = codec_family(f.get('vcodec'))
        res = short_side(f)
        note = f.get('format_note') if f.get('format_note') in ('原画',) else ''
        note_raw = f.get('format_note') or ''
        res_txt = f'{res}p{fps_tag or ""}' if res else (
            '带水印版' if 'watermark' in note_raw.lower() else (note_raw or f.get('format_id') or '默认'))
        tag = f'原画 {res_txt}' if note else res_txt
        if tiered:
            tag = f['format']
        fid = f.get('format_id')
        streams = [(fid, _size(f, duration))]
        if _has_audio(f) or not best_audio:
            selector = fid
        else:
            selector = f'{fid}+{best_audio["format_id"]}'
            streams.append((best_audio['format_id'], _size(best_audio, duration)))
        total = sum(s for _, s in streams)
        parts = [tag]
        if fam:
            parts.append(_CODEC_LABEL.get(fam, fam))
        parts.append(fmt_size(total) if total else '大小未知')
        if not _has_audio(f) and not best_audio:
            parts.append('无音轨')
        options.append(QualityOption(label=' · '.join(parts), tag=tag.replace(' ', ''), format=selector,
                                     kind='video', res=res, size=total, streams=streams))

    if best_audio:
        a = best_audio
        sz = _size(a, duration)
        abr = a.get('abr') or a.get('tbr')
        abr_txt = f' {abr:.0f}kbps' if abr else ''
        options.append(QualityOption(label=f'仅音频 · {a.get("ext", "").upper()}{abr_txt} · {fmt_size(sz)}',
                                     tag='音频', format=a['format_id'], kind='audio', size=sz,
                                     streams=[(a['format_id'], sz)]))
    for a in sorted(special_audios, key=lambda f: -(f.get('tbr') or 0)):
        sz = _size(a, duration)
        lossless = (a.get('acodec') or '').lower().startswith(('flac', 'alac'))
        name, tag = ('FLAC 无损', 'FLAC') if lossless else ('杜比音效', '杜比音效')
        options.append(QualityOption(label=f'仅音频 · {name} · {fmt_size(sz)}', tag=tag, format=a['format_id'],
                                     kind='audio', size=sz, streams=[(a['format_id'], sz)],
                                     convert='flac' if lossless else ''))
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

    def submit(self, url, on_result, on_error, on_expand=None, probe=True):
        self.pool.submit(self._run, url, on_result, on_error, on_expand, probe)

    def _run(self, url, on_result, on_error, on_expand, probe=True):
        settings = self.get_settings()
        logger = _Logger(self.log)
        opts = base_options(settings, logger)
        opts['extract_flat'] = 'in_playlist'
        opts['writesubtitles'] = True      # 只取字幕/弹幕列表，不会下载
        for attempt in (1, 2):
            try:
                with new_ydl(opts) as ydl:
                    info = ydl.sanitize_info(ydl.extract_info(url, download=False))
                break
            except Exception as e:  # noqa: BLE001 - 任何异常都要回到界面
                msg, raw = friendly_error(e)
                # 网络偶发超时/断连时自动再试一次
                if attempt == 1 and msg.startswith('网络连接失败'):
                    self.log('warning', f'解析 {url} 网络错误，重试中：{raw[:120]}')
                    time.sleep(1.5)
                    continue
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
        video = self._make(url, yt().YoutubeDL.sanitize_info(info, True), settings, logger.locked_formats)
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
            options=build_options(info, settings.codec_preference),
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
