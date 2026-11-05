"""未完成任务的保存与恢复：关闭软件时把排队/下载中/暂停的任务写进 tasks.json，下次启动恢复为「已暂停」。"""
import json
import os
from dataclasses import asdict

from . import core
from .config import DATA

PATH = os.path.join(DATA, 'tasks.json')
UNFINISHED = (core.QUEUED, core.RUNNING, core.MERGING, core.PAUSED)


def save(tasks):
    items = []
    for t in tasks:
        if t.status not in UNFINISHED:
            continue
        v = t.video
        items.append({
            'video': {'url': v.url, 'info': v.info, 'title': v.title, 'platform': v.platform,
                      'duration': v.duration, 'uploader': v.uploader, 'locked': v.locked},
            'option': asdict(t.option),
            'out_dir': t.out_dir, 'save_cover': t.save_cover, 'save_subs': t.save_subs,
            'save_danmaku': t.save_danmaku, 'overwrite': t.overwrite, 'section': t.section, 'precise': t.precise,
            'progress': t.progress, 'downloaded': t.downloaded, 'total': t.total,
        })
    tmp = PATH + '.tmp'
    if not items:
        for p in (PATH, tmp):
            try:
                os.remove(p)
            except OSError:
                pass
        return 0
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False)
    os.replace(tmp, PATH)
    return len(items)


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
