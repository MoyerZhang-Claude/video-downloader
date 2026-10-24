"""安卓端桥接层：复用电脑版的解析核心（app/core.py 等，打包时从电脑版源码同步过来）。

手机上没有 ffmpeg：音视频分离的格式（B站、YouTube 高清）由这里分别下载视频流和音频流，
再交给 Kotlin 端用系统 MediaMuxer 合并成 MP4。
"""
import glob
import json
import os
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
    _history = History(os.path.join(files_dir, 'history.db'))
    _ready = True


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
    返回 JSON：{files: [视频流路径, 音频流路径?], subs: [{path, lang, name}], title, tag, ext} 或 {error}
    """
    core = _core
    with _lock:
        info, options = _infos[key]
    opt = options[index]
    s = _settings()
    os.makedirs(out_dir, exist_ok=True)
    files = []
    streams = opt.streams
    totals = {fid: est or 0 for fid, est in streams}
    done = {fid: 0 for fid, _ in streams}
    last = [0.0]

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
        ids = [f for f, _ in streams]
        idx = ids.index(fid) + 1 if fid in ids else 1
        stage = f'{"视频" if idx == 1 else "音频"} {idx}/{len(streams)}' if len(streams) > 1 else ''
        callback.on_progress(sum(done.values()), sum(totals.values()), int(d.get('speed') or 0), stage)

    for fid, _ in streams:
        o = core.base_options(s, core._Logger())
        o.update({'format': fid, 'outtmpl': {'default': os.path.join(out_dir, f'%(id)s.{fid}.%(ext)s')},
                  'progress_hooks': [hook], 'continuedl': True, 'concurrent_fragment_downloads': 4})
        try:
            with core.new_ydl(o) as ydl:
                r = ydl.process_ie_result(dict(info), download=True)
            path = (r.get('requested_downloads') or [{}])[0].get('filepath') or r.get('filepath')
            files.append(path)
        except core.yt().DownloadCancelled:
            return json.dumps({'error': '已取消', 'cancelled': True}, ensure_ascii=False)
        except Exception as e:  # noqa: BLE001
            return json.dumps({'error': core.friendly_error(e)[0]}, ensure_ascii=False)
    subs = _download_subs(info, out_dir, callback) if want_subs and opt.kind == 'video' else []
    ext = os.path.splitext(files[0])[1].lstrip('.') if len(files) == 1 else 'mp4'
    return json.dumps({'files': files, 'subs': subs, 'title': info.get('title') or info.get('id'), 'tag': opt.tag,
                       'ext': ext or 'mp4'}, ensure_ascii=False)


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

def history_add(key, index, uri, size):
    with _lock:
        info, options = _infos.get(key, (None, None))
    if not info:
        return
    v_title = info.get('title') or info.get('id')
    from app.history import video_key
    _history.add_record(video_key(info), v_title, _core.platform_name(info), info.get('uploader') or '',
                        info.get('webpage_url') or '', options[index].tag, uri, int(size or 0))


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
