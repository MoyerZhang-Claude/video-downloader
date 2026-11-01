"""安卓端桥接层：复用电脑版的解析核心（app/core.py 等，打包时从电脑版源码同步过来）。

手机上没有 ffmpeg：音视频分离的格式（B站、YouTube 高清）由这里分别下载视频流和音频流，
再交给 Kotlin 端用系统 MediaMuxer 合并成 MP4。
"""
import glob
import json
import os
import re
import threading
import time

_ready = False
_infos = {}          # key -> (info, options)
_cols = {}           # key -> 所属合集/播放列表（见 app/collection.py）
_lock = threading.Lock()
_core = None
_history = None


def init(files_dir, cache_dir):
    """App 启动时调用：把登录信息、下载历史等放到 App 私有目录。"""
    global _ready, _core, _history
    if _ready:
        return
    os.environ['APPDATA'] = files_dir          # bilibili.py 把登录信息存在 %APPDATA%\\VideoDownloader
    os.environ.setdefault('HOME', files_dir)
    os.environ['TMPDIR'] = cache_dir
    from app import core
    from app.history import History
    _core = core
    # 导入的登录信息（如 YouTube cookies）放在 App 私有目录，解析核心自动读取（和电脑版的 cookies 文件夹同一机制）
    core.COOKIE_DIR = os.path.join(files_dir, 'cookies')
    os.makedirs(core.COOKIE_DIR, exist_ok=True)
    _use_android_error_hints()
    _history = History(os.path.join(files_dir, 'history.db'))
    _ready = True


def _use_android_error_hints():
    """电脑版的部分提示（「设置里」「cookies 文件夹」）在手机上找不到，换成手机版的入口。"""
    orig = _core.friendly_error
    if getattr(orig, '_android', False):
        return

    def friendly(exc):
        msg, raw = orig(exc)
        if re.search(r"not a bot|confirm you.re not a bot", raw, re.I):
            if _yt_cookie_count():
                msg = ('YouTube 仍要求人机验证：导入的登录信息可能已失效，请重新导出后再导入'
                       '（「下载选项 → 网站登录信息」），或换一个 VPN 节点')
            else:
                msg = ('YouTube 暂时拦截了当前网络（多半是 VPN 节点被风控）：换一个节点后重试，'
                       '或在「下载选项 → 网站登录信息」里导入 YouTube 的 cookies')
        elif msg.startswith('该内容需要登录'):
            msg = '该内容需要登录：在「下载选项 → 网站登录信息」导入这个网站的 cookies（B站 点右上角「B站登录」）'
        return msg, raw

    friendly._android = True
    _core.friendly_error = friendly


def _settings():
    from app.config import Settings
    s = Settings()
    s.codec_preference = 'compat'
    return s


# MediaMuxer 能装进 MP4 的编码（VP9 只能进 WebM，暂不支持）
_MUX_VIDEO = {'h264', 'h265', None}


def _usable(info, opt):
    """手机端能处理的选项：不需要 ffmpeg 转换，且两条流都能被系统合并。"""
    if opt.convert:
        return False
    if len(opt.streams) == 1:
        return True
    fmts = {f.get('format_id'): f for f in info.get('formats') or []}
    v = fmts.get(opt.streams[0][0]) or {}
    a = fmts.get(opt.streams[1][0]) or {}
    acodec = (a.get('acodec') or '').lower()
    return _core.codec_family(v.get('vcodec')) in _MUX_VIDEO and (acodec.startswith('mp4a') or a.get('ext') == 'm4a')


def _col_summary(col):
    if not col:
        return None
    entries = col.get('entries')
    return {'kind': col['kind'], 'title': col.get('title') or '', 'current': col.get('current', -1),
            'count': len(entries) if entries is not None else -1}


def parse(url, probe=True):
    """解析链接，返回 JSON 字符串。

    单个视频：{key, title, platform, uploader, duration, thumbnail, options, subs, history, collection?}
    播放列表/收藏夹等：{playlist: {kind, title, entries}}
    出错：{error}
    """
    core = _core
    s = _settings()
    logger = core._Logger()
    opts = core.base_options(s, logger)
    opts['extract_flat'] = 'in_playlist'
    opts['writesubtitles'] = True              # 只取字幕列表，不下载
    try:
        with core.new_ydl(opts) as ydl:
            raw_info = ydl.sanitize_info(ydl.extract_info(url, download=False))
    except Exception as e:  # noqa: BLE001
        msg, raw = core.friendly_error(e)
        return json.dumps({'error': msg, 'raw': raw[:500]}, ensure_ascii=False)
    if raw_info.get('_type') == 'playlist':
        # 注意：sanitize_info(…, True) 会删掉 entries，所以播放列表要在清理前处理
        from app import collection
        col = collection.from_playlist({'title': raw_info.get('title'), 'entries': raw_info.get('entries') or []})
        if not col:
            return json.dumps({'error': '播放列表是空的'}, ensure_ascii=False)
        return json.dumps({'playlist': col}, ensure_ascii=False)
    info = core.yt().YoutubeDL.sanitize_info(raw_info, True)
    options = [o for o in core.build_options(info, s.codec_preference) if _usable(info, o)]
    if not options:
        return json.dumps({'error': '这个视频的格式手机版暂时处理不了'}, ensure_ascii=False)
    key = f'{info.get("extractor_key")}:{info.get("id")}'
    col = None
    if probe:
        from app import collection
        col = collection.probe(info, url, lambda u: core.Parser._flat(u, s))
    with _lock:
        _infos[key] = (info, options)
        if col:
            _cols[key] = col
    human, autos = core.pick_subtitles(info, 'zh_orig')
    subs = [core.sub_name(x) for x in human] + [core.sub_name(x, True) for x in autos]
    hist = []
    try:
        hist = [{'quality': h['quality'], 'uri': h['filepath'], 'ts': h['ts']} for h in _history.lookup(info)]
    except Exception:  # noqa: BLE001
        pass
    return json.dumps({
        'key': key,
        'url': info.get('webpage_url') or url,
        'title': info.get('title') or info.get('id') or url,
        'platform': core.platform_name(info),
        'uploader': info.get('uploader') or info.get('channel') or '',
        'duration': info.get('duration') or 0,
        'thumbnail': _cover_url(info),
        'locked': logger.locked_formats,
        'subs': subs,
        'danmaku': core.has_danmaku(info),
        'history': hist,
        'collection': _col_summary(col),
        'options': [{'label': o.label, 'tag': o.tag, 'kind': o.kind, 'size': o.size, 'res': o.res,
                     'streams': len(o.streams)} for o in options],
    }, ensure_ascii=False)


def collection_entries(key):
    """选集窗口用：返回 {kind, title, current, entries:[{url,title,duration}]}（频道视频这类会在这时才去获取）。"""
    with _lock:
        col = _cols.get(key)
    if not col:
        return json.dumps({'error': '没有找到这个合集'}, ensure_ascii=False)
    if col.get('entries') is None:
        from app import collection
        try:
            collection.load_lazy(col, lambda u: _core.Parser._flat(u, _settings()))
        except Exception as e:  # noqa: BLE001
            return json.dumps({'error': _core.friendly_error(e)[0]}, ensure_ascii=False)
    return json.dumps({'kind': col['kind'], 'title': col.get('title') or '', 'current': col.get('current', -1),
                       'entries': col['entries']}, ensure_ascii=False)


def download(key, index, out_dir, callback, want_subs=True):
    """下载第 index 个选项的各条流到 out_dir（需要时再下字幕）。

    callback（Kotlin 对象）需要有：
      on_progress(done_bytes: long, total_bytes: long, speed: long, stage: str)
      is_cancelled() -> bool
    选中的格式下载失败时，自动换同一视频的其他格式再试（同清晰度的其他下载方式优先，再降一档）。
    返回 JSON：{files, subs, title, tag, index, fallback, ext, nodes} 或 {error}
      tag / index：实际下载的格式；fallback：换了格式时，原来选的清晰度
    """
    core = _core
    callback = _SafeCallback(callback)
    with _lock:
        info, options = _infos[key]
    opt = options[index]
    os.makedirs(out_dir, exist_ok=True)
    first_error = None
    for cand in [opt] + _fallback_options(options, opt):
        if callback.is_cancelled():
            return json.dumps({'error': '已取消', 'cancelled': True}, ensure_ascii=False)
        if cand is not opt:
            _clear_dir(out_dir)
            callback.on_progress(0, 0, 0, f'换成 {cand.tag} 重试')
        try:
            files, nodes = _download_option(info, cand, out_dir, callback)
        except _Cancelled:
            return json.dumps({'error': '已取消', 'cancelled': True}, ensure_ascii=False)
        except Exception as e:  # noqa: BLE001
            first_error = first_error or e
            continue
        subs = _download_subs(info, out_dir, callback) if want_subs and cand.kind == 'video' else []
        ext = os.path.splitext(files[0])[1].lstrip('.') if len(files) == 1 else 'mp4'
        return json.dumps({'files': files, 'subs': subs, 'title': info.get('title') or info.get('id'),
                           'tag': cand.tag, 'index': options.index(cand),
                           'fallback': opt.tag if cand is not opt else '',
                           'ext': ext or 'mp4', 'nodes': nodes}, ensure_ascii=False)
    return json.dumps({'error': core.friendly_error(first_error)[0]}, ensure_ascii=False)


class _Cancelled(Exception):
    pass


def _fallback_options(options, opt, limit=2):
    """备选格式：同类型（视频/音频）；同清晰度的其他下载方式优先，其次比它低的清晰度从高到低。"""
    same = [o for o in options if o is not opt and o.kind == opt.kind and o.res == opt.res]
    lower = sorted((o for o in options if o is not opt and o.kind == opt.kind and 0 < o.res < (opt.res or 1 << 30)),
                   key=lambda o: -o.res)
    seen, out = set(), []
    for o in same + lower:
        if tuple(o.streams) not in seen and tuple(o.streams) != tuple(opt.streams):
            seen.add(tuple(o.streams))
            out.append(o)
    return out[:limit]


def _clear_dir(path):
    import shutil
    for name in os.listdir(path):
        full = os.path.join(path, name)
        try:
            shutil.rmtree(full) if os.path.isdir(full) else os.remove(full)
        except OSError:
            pass


def _download_option(info, opt, out_dir, callback):
    """下载一个格式选项的各条流。返回 (文件列表, 节点列表)；取消抛 _Cancelled，失败抛原始异常。"""
    core = _core
    from app import fastdl
    s = _settings()
    files, nodes = [], []
    streams = opt.streams
    fmts = {f.get('format_id'): f for f in info.get('formats') or []}
    totals = {fid: est or 0 for fid, est in streams}
    done = {fid: 0 for fid, _ in streams}
    last = [0.0]

    def stage_of(fid):
        ids = [f for f, _ in streams]
        idx = ids.index(fid) + 1 if fid in ids else 1
        return f'{"视频" if idx == 1 else "音频"} {idx}/{len(streams)}' if len(streams) > 1 else ''

    def hook(d):
        if callback.is_cancelled():
            raise core.yt().DownloadCancelled()
        fid = (d.get('info_dict') or {}).get('format_id')
        if d.get('status') == 'downloading':
            done[fid] = d.get('downloaded_bytes') or 0
            totals[fid] = d.get('total_bytes') or d.get('total_bytes_estimate') or totals.get(fid) or 0
            now = time.monotonic()
            if now - last[0] < 0.3:
                return
            last[0] = now
        elif d.get('status') == 'finished':
            done[fid] = totals[fid] = d.get('total_bytes') or d.get('downloaded_bytes') or done.get(fid, 0)
        else:
            return
        callback.on_progress(sum(done.values()), sum(totals.values()), d.get('speed') or 0, stage_of(fid))

    for fid, _ in streams:
        # 直链（绝大多数网站）：多连接分段 + 测速看门狗；B站 先测速挑官方节点
        f = fmts.get(fid) or {}
        if _is_direct(f):
            def on_prog(d_, t_, sp, fid=fid):
                done[fid] = d_
                if t_:
                    totals[fid] = t_
                callback.on_progress(sum(done.values()), sum(totals.values()), sp, stage_of(fid))
            try:
                path, node = _fast_download(info, f, fid, out_dir, callback, on_prog, stage_of(fid))
                files.append(path)
                nodes.append(node)
                continue
            except fastdl.Cancelled:
                raise _Cancelled() from None
            except Exception:  # noqa: BLE001 - 加速下载失败：交给 yt-dlp 再试一次
                done[fid] = 0
        o = core.base_options(s, core._Logger())
        o.update({'format': fid, 'outtmpl': {'default': os.path.join(out_dir, f'%(id)s.{fid}.%(ext)s')},
                  'progress_hooks': [hook], 'continuedl': True, 'concurrent_fragment_downloads': 4})
        try:
            with core.new_ydl(o) as ydl:
                r = ydl.process_ie_result(dict(info), download=True)
        except core.yt().DownloadCancelled:
            raise _Cancelled() from None
        path = (r.get('requested_downloads') or [{}])[0].get('filepath') or r.get('filepath')
        if not path or not os.path.exists(path):
            raise RuntimeError('下载结束但没有找到文件')
        files.append(path)
    return files, nodes


class _SafeCallback:
    """Python → Kotlin 的进度回调做类型保护。

    Kotlin 端是 on_progress(Long, Long, Long, String)，Chaquopy 不会把小数自动转成整数；
    yt-dlp 不同下载方式汇报的数值类型不统一（如 HLS 的 total_bytes_estimate 是小数），
    这里统一转成整数。进度回调本身出错也不影响下载，只有「用户取消」会让下载停下。
    """

    def __init__(self, cb):
        self._cb = cb

    @staticmethod
    def _int(x):
        try:
            return max(0, int(x or 0))
        except (TypeError, ValueError, OverflowError):
            return 0

    def on_progress(self, done, total, speed, stage=''):
        try:
            self._cb.on_progress(self._int(done), self._int(total), self._int(speed), str(stage or ''))
        except Exception:  # noqa: BLE001 - 进度显示失败不能拖垮下载
            pass

    def is_cancelled(self):
        try:
            return bool(self._cb.is_cancelled())
        except Exception:  # noqa: BLE001
            return False


def _is_direct(f):
    """单个文件的直链（不是 HLS/DASH 分片），可以用多连接分段下载。"""
    return bool(f.get('url')) and (f.get('protocol') or 'https') in ('http', 'https') and not f.get('fragments')


def _headers(info, f=None):
    h = (f or {}).get('http_headers') or info.get('http_headers') or {}
    return {k: v for k, v in h.items() if k.lower() not in ('cookie', 'range')}


def _fast_download(info, f, fid, out_dir, callback, on_prog, stage):
    from urllib.parse import urlparse

    from app import fastdl
    core = _core
    with core.new_ydl(core.base_options(_settings(), core._Logger())) as ydl:
        opener = core.ydl_opener(ydl)
        headers = dict(_headers(info, f), **{'Accept-Encoding': 'identity'})
        urls = f.get('url_candidates') or [f['url']]
        if len(urls) > 1:
            callback.on_progress(0, 0, 0, '挑选最快的下载节点')
            urls = fastdl.rank_urls(urls, headers, opener, is_cancelled=callback.is_cancelled)
        safe_fid = re.sub(r'[^\w.-]+', '_', str(fid))
        path = os.path.join(out_dir, f'{info.get("id") or "video"}.{safe_fid}.{f.get("ext") or "mp4"}')
        fastdl.FastDownloader(urls, headers, path, opener, on_progress=on_prog, is_cancelled=callback.is_cancelled,
                              total_hint=f.get('filesize') or f.get('filesize_approx') or 0).run()
    return path, urlparse(urls[0]).hostname or ''


# ---------------------------------------------------------------- 封面

def cover(key, out_dir):
    """下载封面原图到 out_dir。返回 {path, ext} 或 {error}（格式转换交给安卓端）。"""
    with _lock:
        info, _ = _infos.get(key, (None, None))
    if not info:
        return json.dumps({'error': '请重新解析这个视频'}, ensure_ascii=False)
    url = _cover_url(info)
    if not url:
        return json.dumps({'error': '这个视频没有封面'}, ensure_ascii=False)
    core = _core
    from yt_dlp.networking import Request
    try:
        with core.new_ydl(core.base_options(_settings(), core._Logger())) as ydl:
            resp = ydl.urlopen(Request(url, headers=_headers(info)))
            data = resp.read()
            ctype = (resp.headers.get('Content-Type') or '').lower()
    except Exception as e:  # noqa: BLE001
        return json.dumps({'error': f'下载封面失败：{core.friendly_error(e)[0]}'}, ensure_ascii=False)
    ext = 'webp' if 'webp' in ctype else 'png' if 'png' in ctype else 'jpg'
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f'cover.{ext}')
    with open(path, 'wb') as fp:
        fp.write(data)
    return json.dumps({'path': path, 'ext': ext}, ensure_ascii=False)


# ---------------------------------------------------------------- 弹幕版

def danmaku_prepare(key, index, out_dir):
    """下载 B站 弹幕并排好位置，供安卓端烧录进视频。

    返回 {xml, layout, count}：xml 原始弹幕文件（存档用），layout 排好位置的 JSON 文件；或 {error}
    """
    with _lock:
        info, options = _infos.get(key, (None, None))
    if not info:
        return json.dumps({'error': '请重新解析这个视频'}, ensure_ascii=False)
    if not _core.has_danmaku(info):
        return json.dumps({'error': '这个视频没有弹幕'}, ensure_ascii=False)
    core = _core
    from app import danmaku
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, 'dm')
    o = core.base_options(_settings(), core._Logger())
    o.update({'skip_download': True, 'writesubtitles': True, 'subtitleslangs': ['danmaku'],
              'outtmpl': {'default': base + '.%(ext)s'}})
    try:
        with core.new_ydl(o) as ydl:
            ydl.process_ie_result(dict(info), download=True)
    except Exception as e:  # noqa: BLE001
        return json.dumps({'error': f'获取弹幕失败：{core.friendly_error(e)[0]}'}, ensure_ascii=False)
    xml = next(iter(glob.glob(glob.escape(base) + '.danmaku.*')), None)
    if not xml:
        return json.dumps({'error': '获取弹幕失败'}, ensure_ascii=False)
    fmts = {f.get('format_id'): f for f in info.get('formats') or []}
    v = fmts.get(options[index].streams[0][0]) or {}
    with open(xml, 'rb') as fp:
        lay = danmaku.layout(fp.read(), v.get('width') or 1920, v.get('height') or 1080)
    path = os.path.join(out_dir, 'danmaku_layout.json')
    with open(path, 'w', encoding='utf-8') as fp:
        json.dump(lay, fp, ensure_ascii=False)
    return json.dumps({'xml': xml, 'layout': path, 'count': len(lay['items'])}, ensure_ascii=False)


def _download_subs(info, out_dir, callback):
    """字幕单独下载（SRT）：失败不影响视频。返回 [{path, lang, name}]"""
    core = _core
    human, autos = core.pick_subtitles(info, 'zh_orig')
    if not (human or autos):
        return []
    callback.on_progress(0, 0, 0, '下载字幕')
    base = os.path.join(out_dir, 'subs')
    o = core.base_options(_settings(), core._Logger())
    o.update({'skip_download': True, 'writesubtitles': True, 'writeautomaticsub': bool(autos),
              'subtitleslangs': human + autos, 'subtitlesformat': 'srt/best', 'ignoreerrors': True,
              'outtmpl': {'default': base + '.%(ext)s'}})
    try:
        with core.new_ydl(o) as ydl:
            ydl.process_ie_result(dict(info), download=True)
    except Exception:  # noqa: BLE001
        pass
    out = []
    for lang in human + autos:
        for p in glob.glob(glob.escape(f'{base}.{lang}.') + '*'):
            out.append({'path': p, 'lang': lang, 'name': core.sub_name(lang, lang in autos),
                        'ext': os.path.splitext(p)[1].lstrip('.')})
    return out


# ---------------------------------------------------------------- 下载历史

def history_add(key, index, uri, size, suffix=''):
    with _lock:
        info, options = _infos.get(key, (None, None))
    if not info:
        return
    v_title = info.get('title') or info.get('id')
    from app.history import video_key
    _history.add_record(video_key(info), v_title, _core.platform_name(info), info.get('uploader') or '',
                        info.get('webpage_url') or '', options[index].tag + suffix, uri, int(size or 0))


def history_list(search=''):
    return json.dumps(_history.recent(search), ensure_ascii=False)


def history_delete(row_id):
    _history.delete(int(row_id))


def history_clear():
    _history.clear()


def _cover_url(info):
    url = info.get('thumbnail') or ''
    if not url:
        thumbs = [t for t in info.get('thumbnails') or [] if t.get('url')]
        url = thumbs[-1]['url'] if thumbs else ''
    if url.startswith('//'):
        url = 'https:' + url
    elif url.startswith('http://'):
        url = 'https://' + url[7:]
    return url


def extract_urls(text):
    return json.dumps(_core.extract_urls(text))


# ---------------------------------------------------------------- B站登录（App 内网页登录后，把 cookies 交给这里）

def save_bilibili_cookies(cookie_header):
    """cookie_header：WebView 里 CookieManager.getCookie() 得到的 'a=1; b=2' 字符串。返回 bili_status()。"""
    import http.cookiejar
    from app import bilibili
    pairs = [p.split('=', 1) for p in cookie_header.split(';') if '=' in p]
    if not any(k.strip() == 'SESSDATA' for k, _ in pairs):
        return json.dumps({'logged_in': False, 'error': '没有拿到登录信息，请确认已经登录成功'}, ensure_ascii=False)
    os.makedirs(bilibili.DATA_DIR, exist_ok=True)
    jar = http.cookiejar.MozillaCookieJar(bilibili.COOKIE_PATH)
    expires = int(time.time()) + 180 * 86400       # WebView 不提供过期时间：按半年记，真正失效时会提示重新登录
    for k, v in pairs:
        jar.set_cookie(bilibili._make_cookie(k.strip(), v.strip(), expires))
    jar.save(ignore_discard=True, ignore_expires=True)
    bilibili._save_meta({'login_type': 'webview', 'login_time': time.time()})
    return bili_status()


def bili_status():
    """{logged_in, name, vip, vip_label} 或 {logged_in: false, expired?}"""
    from app import bilibili
    if not bilibili.is_logged_in():
        return json.dumps({'logged_in': False}, ensure_ascii=False)
    try:
        info = bilibili.account_info()
    except Exception as e:  # noqa: BLE001 - 网络问题时先当作已登录
        return json.dumps({'logged_in': True, 'offline': True, 'error': str(e)[:100]}, ensure_ascii=False)
    if not info:
        return json.dumps({'logged_in': False, 'expired': True}, ensure_ascii=False)
    return json.dumps({'logged_in': True, **info}, ensure_ascii=False)


def bili_logout():
    from app import bilibili
    bilibili.logout()
    return bili_status()


# ---------------------------------------------------------------- B站扫码登录（长效登录：电视端扫码，约 180 天）
# 手机无法扫自己屏幕上的码：二维码可以保存到相册，再用 B站 App「扫一扫 → 相册」识别。

_qr_login = None


def qr_start(out_png):
    """生成新的登录二维码，图片写到 out_png。"""
    global _qr_login
    import segno
    from app import bilibili
    _qr_login = bilibili.TvQrLogin()
    try:
        url = _qr_login.generate()
    except Exception as e:  # noqa: BLE001
        return json.dumps({'error': f'获取二维码失败：{e}'}, ensure_ascii=False)
    segno.make(url, error='m').save(out_png, scale=12, border=4, dark='#1e1b4b')
    return json.dumps({'ok': True}, ensure_ascii=False)


def qr_poll():
    """{status: waiting / scanned / expired / success / error, ...账号信息}"""
    if _qr_login is None:
        return json.dumps({'status': 'error', 'msg': '还没有生成二维码'}, ensure_ascii=False)
    try:
        status, msg = _qr_login.poll()
    except Exception as e:  # noqa: BLE001 - 网络抖动：下次再查
        return json.dumps({'status': 'error', 'msg': str(e)[:100]}, ensure_ascii=False)
    res = {'status': status, 'msg': msg}
    if status == 'success':
        res.update(json.loads(bili_status()))
    return json.dumps(res, ensure_ascii=False)


# ---------------------------------------------------------------- 网站登录信息（导入电脑浏览器导出的 cookies.txt）
# 很多网站在 App 里没法直接登录（谷歌禁止内嵌网页登录，TikTok 等需要验证码），
# 所以在电脑浏览器登录后用扩展导出 cookies，再导入到手机。每个网站存一个文件：cookies/<网站>.txt，
# 解析核心会自动读取 cookies 文件夹里的所有文件（和电脑版同一机制）。

_YT_DOMAINS = ('youtube.com', 'google.com')
_YT_LOGIN_NAMES = {'SID', 'SAPISID', 'LOGIN_INFO', '__Secure-1PSID', '__Secure-3PSID', '__Secure-1PAPISID',
                   '__Secure-3PAPISID'}
# 这些域名只是登录的「附属」（YouTube 的登录 cookie 在 google.com 上），不单独算一个网站
_AUX_DOMAINS = {'google.com', 'gstatic.com', 'googleusercontent.com'}
_SITE_NAMES = {'youtube.com': 'YouTube', 'tiktok.com': 'TikTok', 'x.com': 'X', 'twitter.com': 'X',
               'instagram.com': 'Instagram', 'facebook.com': 'Facebook', 'vimeo.com': 'Vimeo',
               'bilibili.com': 'B站', 'douyin.com': '抖音', 'kuaishou.com': '快手', 'twitch.tv': 'Twitch',
               'reddit.com': 'Reddit', 'nicovideo.jp': 'niconico', 'weibo.com': '微博', 'xiaohongshu.com': '小红书'}


def _site_of(domain):
    """cookie 的域名 → 网站（取最后两段，如 .m.youtube.com → youtube.com）"""
    parts = domain.lstrip('.').lower().split('.')
    return '.'.join(parts[-2:]) if len(parts) >= 2 else domain.lstrip('.').lower()


def _site_label(site):
    return _SITE_NAMES.get(site, site)


def _cookie_files():
    try:
        return sorted(n for n in os.listdir(_core.COOKIE_DIR) if n.lower().endswith('.txt'))
    except OSError:
        return []


def _yt_cookie_count():
    """有没有导入过 YouTube 登录信息（报错提示用）"""
    return 1 if 'youtube.com.txt' in _cookie_files() or 'youtube.txt' in _cookie_files() else 0


def _load_cookie_text(path):
    """读取 Netscape 格式 cookies.txt：兼容没有文件头、Windows 换行、UTF-8 BOM。"""
    import http.cookiejar
    import tempfile
    with open(path, 'rb') as fp:
        text = fp.read().decode('utf-8-sig', 'replace').replace('\r\n', '\n').replace('\r', '\n')
    if not text.lstrip().startswith(('# Netscape HTTP Cookie File', '# HTTP Cookie File')):
        text = '# Netscape HTTP Cookie File\n' + text
    fd, tmp = tempfile.mkstemp(suffix='.txt')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fp:
            fp.write(text)
        jar = http.cookiejar.MozillaCookieJar()
        jar.load(tmp, ignore_discard=True, ignore_expires=True)
        return jar
    finally:
        os.remove(tmp)


def cookies_import(src_path):
    """导入用户选的 cookies 文件（任意网站）。返回 cookies_status()（含 imported：这次导入的网站）或 {error}"""
    import collections
    import http.cookiejar
    try:
        cookies = list(_load_cookie_text(src_path))
    except Exception:  # noqa: BLE001
        return json.dumps({'error': '这不是有效的 cookies 文件：请用浏览器扩展「Get cookies.txt LOCALLY」导出'},
                          ensure_ascii=False)
    if not cookies:
        return json.dumps({'error': '文件里没有 cookies：请在要下载的网站页面上点扩展导出'}, ensure_ascii=False)
    by_site = collections.defaultdict(list)
    for c in cookies:
        by_site[_site_of(c.domain)].append(c)
    # 附属域名（google.com）归到 YouTube；文件里只有附属域名时也按 YouTube 处理
    aux = [c for site in list(by_site) if site in _AUX_DOMAINS for c in by_site.pop(site)]
    if aux:
        by_site['youtube.com'].extend(aux)
    if 'youtube.com' in by_site and not {c.name for c in by_site['youtube.com']} & _YT_LOGIN_NAMES:
        if len(by_site) == 1:
            return json.dumps({'error': '文件里的 YouTube cookies 不含登录状态：请先在浏览器里登录 YouTube 再导出'},
                              ensure_ascii=False)
        by_site.pop('youtube.com')
    os.makedirs(_core.COOKIE_DIR, exist_ok=True)
    for site, items in by_site.items():
        path = os.path.join(_core.COOKIE_DIR, f'{site}.txt')
        out = http.cookiejar.MozillaCookieJar(path)
        for c in items:
            out.set_cookie(c)
        out.save(ignore_discard=True, ignore_expires=True)
        _core._cookie_cache.pop(path, None)
    old = os.path.join(_core.COOKIE_DIR, 'youtube.txt')       # 旧版本只存 YouTube 的文件名
    if 'youtube.com' in by_site and os.path.exists(old):
        os.remove(old)
        _core._cookie_cache.pop(old, None)
    res = json.loads(cookies_status())
    res['imported'] = [_site_label(s) for s in sorted(by_site, key=lambda x: -len(by_site[x]))]
    return json.dumps(res, ensure_ascii=False)


def cookies_status():
    """{sites: [{site, name, count, age_days, expires}]}；expires：最早过期的 cookie（秒，0 表示未知）"""
    sites = []
    for name in _cookie_files():
        path = os.path.join(_core.COOKIE_DIR, name)
        site = name[:-4]
        if site == 'youtube':
            site = 'youtube.com'
        try:
            jar = list(_load_cookie_text(path))
        except Exception:  # noqa: BLE001
            continue
        login = [c for c in jar if c.expires and (site != 'youtube.com' or c.name in _YT_LOGIN_NAMES)]
        sites.append({'site': name[:-4], 'name': _site_label(site), 'count': len(jar),
                      'age_days': int((time.time() - os.path.getmtime(path)) // 86400),
                      'expires': min((c.expires for c in login), default=0)})
    return json.dumps({'sites': sites}, ensure_ascii=False)


def cookies_clear(site=''):
    """清除某个网站的登录信息（site 为空：全部清除）"""
    for name in _cookie_files():
        if site and name[:-4] != site:
            continue
        path = os.path.join(_core.COOKIE_DIR, name)
        try:
            os.remove(path)
        except OSError:
            pass
        _core._cookie_cache.pop(path, None)
    return cookies_status()


# ---------------------------------------------------------------- YouTube 的 JS 验证（用手机系统自带的 V8 引擎）
# 电脑版靠 Node.js 解 YouTube 的 JS 验证；手机上没有 Node.js，改用系统 WebView 的 V8（Kotlin 端 JsEngine）。
# 没有它时，YouTube 登录后会「找不到视频格式」，不登录时部分格式也会缺失。

_js_engine = None


def set_js_engine(engine):
    """App 启动时由 Kotlin 传入 JsEngine（需要有 evaluate(code) -> str 和 available() -> bool）。"""
    global _js_engine
    _js_engine = engine
    _register_v8_provider()


def _register_v8_provider():
    from yt_dlp.extractor.youtube.jsc._builtin.ejs import EJSBaseJCP
    from yt_dlp.extractor.youtube.jsc.provider import (
        JsChallengeProviderError,
        register_preference,
        register_provider,
    )
    if globals().get('_V8_REGISTERED'):
        return

    class _RuntimeInfo:
        name, path, version, version_tuple, supported = 'android-v8', '', '1', (1,), True

    @register_provider
    class AndroidV8JCP(EJSBaseJCP):
        PROVIDER_NAME = 'android-v8'
        JS_RUNTIME_NAME = 'android-v8'
        PROVIDER_VERSION = '1.0'
        BUG_REPORT_LOCATION = '视频解析下载器'

        @property
        def runtime_info(self):
            return _RuntimeInfo()

        def is_available(self):
            try:
                return self._available and _js_engine is not None and bool(_js_engine.available())
            except Exception:  # noqa: BLE001
                return False

        def _run_js_runtime(self, stdin, /):
            # 验证脚本最后用 console.log 输出结果：V8 沙盒里没有 console，这里接住它并作为返回值
            code = ('var __vd_out = null;\n'
                    'globalThis.console = {log: function (s) { __vd_out = String(s); },'
                    ' error: function () {}, warn: function () {}, info: function () {}, debug: function () {}};\n'
                    + stdin + '\n;__vd_out;')
            try:
                out = _js_engine.evaluate(code)
            except Exception as e:  # noqa: BLE001
                raise JsChallengeProviderError(f'手机 JS 引擎执行失败：{e}') from e
            if not out:
                raise JsChallengeProviderError('手机 JS 引擎没有返回结果')
            return str(out)

    @register_preference(AndroidV8JCP)
    def _preference(provider, requests):
        return 1000

    globals()['_V8_REGISTERED'] = True
