"""合集 / 分P / 播放列表：识别视频所属的列表，返回可以勾选下载的各集。

返回格式：{'kind': '合集'|'分P'|'播放列表', 'title': 列表名, 'current': 当前视频在列表中的下标（-1 表示没有）,
          'entries': [{'url', 'title', 'duration', 'id'}]}
"""
import re
import urllib.parse

from . import bilibili

MAX_ENTRIES = 1000


def probe(info, url, flat_extract):
    """flat_extract(url) -> yt-dlp 平铺解析的播放列表 info（只取标题/链接，不解析每个视频）。"""
    key = info.get('extractor_key') or ''
    try:
        if key.startswith('BiliBili'):
            return _bilibili(info, url)
        if key == 'Youtube':
            return _youtube(info, url, flat_extract)
    except Exception:  # noqa: BLE001 - 识别失败只是不显示「选集」，不影响当前视频
        return None
    return None


def _bilibili(info, url):
    m = re.search(r'BV[0-9A-Za-z]{10}', url) or re.search(r'BV[0-9A-Za-z]{10}', info.get('webpage_url') or '')
    if not m:
        return None
    bvid = m.group(0)
    data = bilibili._session_with_cookies().get(
        'https://api.bilibili.com/x/web-interface/view', params={'bvid': bvid}, timeout=10).json().get('data') or {}
    season = data.get('ugc_season')
    if season:
        eps = [e for sec in season.get('sections') or [] for e in sec.get('episodes') or []]
        if len(eps) > 1:
            entries = [{'url': f'https://www.bilibili.com/video/{e["bvid"]}/', 'id': e['bvid'],
                        'title': e.get('title') or e['bvid'], 'duration': (e.get('arc') or {}).get('duration')}
                       for e in eps if e.get('bvid')][:MAX_ENTRIES]
            cur = next((i for i, e in enumerate(entries) if e['id'] == bvid), -1)
            return {'kind': '合集', 'title': season.get('title') or '', 'current': cur, 'entries': entries}
    pages = data.get('pages') or []
    if len(pages) > 1:
        p = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get('p', ['1'])[0]
        entries = [{'url': f'https://www.bilibili.com/video/{bvid}/?p={pg["page"]}', 'id': f'{bvid}_p{pg["page"]}',
                    'title': f'P{pg["page"]}  {pg.get("part") or ""}'.strip(), 'duration': pg.get('duration')}
                   for pg in pages][:MAX_ENTRIES]
        cur = next((i for i, pg in enumerate(pages) if str(pg.get('page')) == p), 0)
        return {'kind': '分P', 'title': data.get('title') or '', 'current': cur, 'entries': entries}
    return None


def _youtube(info, url, flat_extract):
    lst = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get('list', [''])[0]
    if not lst or lst.startswith(('RD', 'UL')):      # RD = 自动生成的「合辑」电台，没有尽头
        # YouTube 的「分享」按钮会去掉 list 参数：退而提供频道全部视频，点按钮时才去获取（不拖慢解析）
        cid = info.get('channel_id') or ''
        if cid.startswith('UC'):
            return {'kind': '频道视频', 'title': info.get('channel') or '', 'current': -1, 'entries': None,
                    'lazy': f'https://www.youtube.com/playlist?list=UU{cid[2:]}', 'video_id': info.get('id')}
        return None
    pl = flat_extract(f'https://www.youtube.com/playlist?list={lst}')
    col = from_playlist(pl)
    if col:
        col['current'] = next((i for i, e in enumerate(col['entries']) if e['id'] == info.get('id')), -1)
    return col


def load_lazy(col, flat_extract):
    """获取「频道视频」这类延迟加载的列表，填好 entries / current。"""
    full = from_playlist(flat_extract(col['lazy']), col['kind'])
    if not full:
        raise RuntimeError('没有获取到视频列表')
    col['entries'] = full['entries']
    col['current'] = next((i for i, e in enumerate(full['entries']) if e['id'] == col.get('video_id')), -1)
    return col


def from_playlist(pl, kind='播放列表'):
    """把 yt-dlp 平铺的播放列表 info 转成选集格式。"""
    entries = []
    for e in (pl or {}).get('entries') or []:
        if not e:
            continue
        u = e.get('url') or e.get('webpage_url')
        if not u:
            continue
        if not u.startswith('http') and e.get('ie_key') == 'Youtube':
            u = f'https://www.youtube.com/watch?v={u}'
        entries.append({'url': u, 'id': e.get('id') or u, 'title': e.get('title') or e.get('id') or u,
                        'duration': e.get('duration')})
        if len(entries) >= MAX_ENTRIES:
            break
    if len(entries) < 1:
        return None
    return {'kind': kind, 'title': (pl or {}).get('title') or '', 'current': -1, 'entries': entries}
