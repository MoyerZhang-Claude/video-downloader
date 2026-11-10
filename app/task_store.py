"""未完成任务的保存与恢复：排队 / 下载中 / 暂停的任务写进 tasks.json，下次启动恢复为「已暂停」。

文件里只留断点续传要用的内容：
- 不写登录 Cookie（yt-dlp 会把和视频地址同域名的 Cookie 放进每个格式的 cookies 字段和请求头里）。
  恢复后的下载从加密保存的登录信息里重新取 Cookie；网站临时发的 Cookie 丢了的话，下载报 403 时会自动重新解析一次。
- 只留选中的那几条流、会用到的自动字幕。YouTube 一个视频的完整信息有 1 MB 左右，任务多了每次状态变化都要写一遍。
写文件在后台线程里做，界面不等它。
"""
import json
import os
import threading
from dataclasses import asdict

from . import core
from .config import DATA, SUB_LANG_MODES

PATH = os.path.join(DATA, 'tasks.json')
UNFINISHED = (core.QUEUED, core.RUNNING, core.MERGING, core.PAUSED)

# 下载完才有意义、或者只是页面展示用的大块数据
_DROP = {'cookies', 'heatmap', 'requested_formats', 'requested_downloads', 'requested_subtitles', '__files_to_move',
         '__postprocessors', '__real_download', '__finaldir', '__write_download_archive', 'filepath', '_filename'}


def _headers(h):
    return {k: v for k, v in h.items() if k.lower() != 'cookie'} if isinstance(h, dict) else h


def _clean(d):
    return {k: (_headers(v) if k == 'http_headers' else v) for k, v in d.items() if k not in _DROP}


def slim_info(info, option):
    """这个任务续传需要的视频信息（不含 Cookie）。"""
    out = _clean(info)
    ids = {fid for fid, _ in option.streams or []}
    formats = info.get('formats') or []
    keep = [f for f in formats if f.get('format_id') in ids] or formats
    if formats:
        out['formats'] = [_clean(f) for f in keep]
    auto = info.get('automatic_captions')
    if auto:
        want = set()
        for mode in SUB_LANG_MODES:
            want.update(core.pick_subtitles(info, mode)[1])
        out['automatic_captions'] = {k: v for k, v in auto.items() if k in want}
    return out


def _snapshot(tasks):
    items = []
    for t in tasks:
        if t.status not in UNFINISHED:
            continue
        v = t.video
        slim = getattr(t, '_slim_info', None)
        if slim is None or slim[0] is not v.info:       # 过期重新解析后 info 会换成新的
            slim = t._slim_info = (v.info, slim_info(v.info, t.option))
        items.append({
            'video': {'url': v.url, 'info': slim[1], 'title': v.title, 'platform': v.platform,
                      'duration': v.duration, 'uploader': v.uploader, 'locked': v.locked},
            'option': asdict(t.option),
            'out_dir': t.out_dir, 'save_cover': t.save_cover, 'save_subs': t.save_subs,
            'save_danmaku': t.save_danmaku, 'overwrite': t.overwrite, 'section': t.section, 'precise': t.precise,
            'progress': t.progress, 'downloaded': t.downloaded, 'total': t.total,
        })
    return items


_file_lock = threading.Lock()
_latest = None              # 等着写的最新一份；写的过程中又有新的，只写最后那份
_latest_lock = threading.Lock()
_writer = None


def _write(items):
    tmp = PATH + '.tmp'
    with _file_lock:
        if not items:
            for p in (PATH, tmp):
                try:
                    os.remove(p)
                except OSError:
                    pass
            return
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(items, f, ensure_ascii=False, default=str)
        os.replace(tmp, PATH)


def _drain():
    global _latest, _writer
    while True:
        with _latest_lock:
            items, _latest = _latest, None
            if items is None:
                _writer = None
                return
        try:
            _write(items)
        except (OSError, TypeError, ValueError):
            pass


def save(tasks):
    """立即保存（退出软件时用）。返回保存了几个任务。"""
    global _latest
    items = _snapshot(tasks)
    with _latest_lock:
        _latest = None          # 后台还没写的旧内容作废
    _write(items)
    return len(items)


def save_async(tasks):
    """在后台线程里保存（任务状态变化时用），界面线程只负责收集要写的内容。"""
    global _latest, _writer
    items = _snapshot(tasks)
    with _latest_lock:
        _latest = items
        if _writer is None:
            _writer = threading.Thread(target=_drain, daemon=True, name='tasks-save')
            _writer.start()


def load():
    try:
        with open(PATH, encoding='utf-8') as f:
            items = json.load(f)
    except (OSError, ValueError):
        return []
    tasks = []
    for it in items:
        try:
            v = it['video']
            video = core.ParsedVideo(url=v['url'], info=v['info'], title=v['title'], platform=v['platform'],
                                     duration=v['duration'], uploader=v['uploader'], options=[],
                                     locked=v.get('locked', ''))
            opt = it['option']
            opt['streams'] = [tuple(s) for s in opt.get('streams') or []]
            option = core.QualityOption(**opt)
            video.options = [option]
            t = core.DownloadTask(video, option, it['out_dir'], save_cover=it.get('save_cover', False))
            t.save_subs = it.get('save_subs', True)
            t.save_danmaku = it.get('save_danmaku', True)
            t.overwrite = it.get('overwrite', False)
            t.section = tuple(it['section']) if it.get('section') else None
            t.precise = it.get('precise', False)
            t.progress = it.get('progress', 0.0)
            t.downloaded = it.get('downloaded', 0)
            t.total = it.get('total', 0)
            t.status = core.PAUSED
            tasks.append(t)
        except (KeyError, TypeError):
            continue
    return tasks
