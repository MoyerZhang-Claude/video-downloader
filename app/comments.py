"""评论区抓取（B站、YouTube）+ 导出（网页 / Excel / CSV）。

YouTube：默认用 yt-dlp（软件里已经带着，不需要密钥）；填了 YouTube Data API v3 密钥时改用官方接口
（时间、点赞数准确，也更快），官方接口用不了（额度用完、密钥无效）时自动换回 yt-dlp。

B站：

和 B站网页一样调用公开的评论接口：
  主评论   /x/v2/reply/wbi/main   （需要 WBI 签名；mode 3 = 按热度，2 = 按时间；每页 20 条）
  楼中楼   /x/v2/reply/reply      （每页最多 20 条，按时间从早到晚）
登录 B站后能翻完整个评论区；没登录时 B站只给前几页。抓得太快会被临时限流（412），所以每页之间停一下，
被限流时等一会儿再试，实在不行就停下来，已经抓到的照样导出。

导出放在「下载目录/评论/<视频标题> 的评论 <时间>/」里：网页（评论.html，图片、头像放在旁边的文件夹，
整个文件夹拷到哪里都能打开）、Excel（评论.xlsx，楼中楼可以在 Excel 里折叠）、CSV（评论.csv）。
不依赖界面（Qt），以后 AI 总结等功能也可以直接用 fetch() 的结果。
"""
import csv
import hashlib
import json
import os
import random
import re
import threading
import time
import urllib.parse
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

from . import bilibili

API = 'https://api.bilibili.com'
PAGE = 20                                 # B站每页给 20 条（楼中楼也最多 20）
MAX_IMAGE_BYTES = 30 * 1024 * 1024
_MIXIN_TAB = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39,
              12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63,
              57, 62, 11, 36, 20, 34, 44, 52]
_REPLY_TO = re.compile(r'^回复 @([^:：\s][^:：]*?) ?[:：]\s*')
_ERRORS = {12002: '这个视频的评论区已关闭', 12009: '评论区不存在', 12061: 'UP 主已关闭评论区',
           -404: '视频不存在或已被删除', 62002: '视频不可见（可能已被删除或设为私密）', 62012: '视频仅 UP 主自己可见'}


class CommentError(Exception):
    """fatal：换一种方式也没用（评论区关闭、视频不存在），不要再试别的抓取方式"""

    def __init__(self, msg, fatal=False):
        super().__init__(msg)
        self.fatal = fatal


class Canceled(Exception):
    pass


_BILI = re.compile(r'(bilibili\.com/(video|bangumi/play|list/)|b23\.tv/|bili2233\.cn/|\bBV[0-9A-Za-z]{10}\b'
                   r'|\bav\d+\b|\bep\d+\b)', re.I)
_YT_ID = re.compile(r'(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/|embed/)|youtu\.be/)([0-9A-Za-z_-]{11})')


_AGO_UNITS = {'second': '秒', 'minute': '分钟', 'hour': '小时', 'day': '天', 'week': '周', 'month': '个月', 'year': '年'}


def zh_ago(text):
    """YouTube 的「3 days ago (edited)」→「3 天前（编辑过）」；看不懂的原样返回"""
    t = (text or '').strip()
    m = re.match(r'(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago', t, re.I)
    if not m:
        return t
    return f'{m.group(1)} {_AGO_UNITS[m.group(2).lower()]}前' + ('（编辑过）' if 'edited' in t.lower() else '')


def platform_of(url):
    """'youtube' / 'bili' / ''"""
    if _YT_ID.search(url or ''):
        return 'youtube'
    return 'bili' if _BILI.search(url or '') else ''


def supported(url):
    """能抓评论的链接：B站视频（BV / av）、番剧的某一集（ep）、b23.tv 短链；YouTube 视频（含 Shorts、直播回放）"""
    return bool(platform_of(url))


def video_key(url):
    """同一个视频不同写法的链接 → 同一个键（记住上次抓的评论用）"""
    url = (url or '').strip()
    m = _YT_ID.search(url)
    if m:
        return 'yt:' + m.group(1)
    for pat, pre in ((r'\b(BV[0-9A-Za-z]{10})', 'bili:'), (r'\bav(\d+)', 'bili:av'), (r'\bep(\d+)', 'bili:ep')):
        m = re.search(pat, url, re.I)
        if m:
            return pre + m.group(1)
    return url.split('#')[0]


@dataclass
class Options:
    sort: str = 'hot'                 # hot 热度 / time 最新
    max_main: int = 500               # 主评论最多多少条；0 = 全部
    sub_mode: str = 'all'             # all 每层全部 / limit 每层最多 sub_limit 条 / none 不要楼中楼
    sub_limit: int = 20
    images: bool = True               # 评论里的图片（不含动图）
    image_quality: str = 'compressed'  # compressed 压缩（长边 1600，webp）/ original 原图
    avatars: bool = True
    formats: tuple = ('html',)        # html / xlsx / csv
    out_root: str = ''                # 一般是 下载目录/评论
    embed: bool = False               # 网页把图片、头像、表情嵌进去，只有一个文件（手机版：手机上打开网页读不到旁边文件夹里的图）

    def to_dict(self):
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.__dict__.items() if k != 'out_root'}

    @classmethod
    def from_dict(cls, d):
        o = cls()
        for k, v in (d or {}).items():
            if hasattr(o, k) and k != 'out_root':
                setattr(o, k, tuple(v) if k == 'formats' else v)
        return o


@dataclass
class Result:
    meta: dict
    comments: list
    options: Options
    fetched_at: float = 0
    stopped: str = ''                 # 中途停下的原因（用户停止 / 被限流……）；空 = 抓完了
    notes: list = field(default_factory=list)
    out_dir: str = ''
    files: dict = field(default_factory=dict)      # 格式 → 文件路径
    media: dict = field(default_factory=dict)      # 原网址 → 相对路径

    @property
    def n_main(self):
        return len(self.comments)

    @property
    def n_sub(self):
        return sum(len(c['replies']) for c in self.comments)

    @property
    def n_pics(self):
        return sum(1 for c in self.all() for p in c['pics'] if p.get('local'))

    def all(self):
        for c in self.comments:
            yield c
            yield from c['replies']


# ================================================================ 抓取

class Fetcher:
    """fetch(url) → Result。progress(dict) 在工作线程里调用；cancel 是 threading.Event（停止后返回已抓到的）。"""

    def __init__(self, opts, proxy='', progress=None, cancel=None, settings=None, yt_key=''):
        """settings：软件设置（YouTube 用 yt-dlp 抓时要用同一套网络、Cookie 设置）；yt_key：YouTube Data API v3 密钥（可选）"""
        import requests
        self.opts = opts
        self.settings = settings
        self.yt_key = (yt_key or '').strip()
        self.proxy = (proxy or '').strip()
        self.progress = progress or (lambda d: None)
        self.cancel = cancel or threading.Event()
        s = requests.Session()
        s.headers.update(bilibili._HEADERS)
        proxy = (proxy or '').strip()
        if proxy:
            s.proxies = {'http': proxy, 'https': proxy}
        for c in bilibili.load_cookies():
            s.cookies.set_cookie(c)
        self.s = s
        self.logged_in = bilibili.is_logged_in()
        self._mixin = None
        self._state = {'count': 0, 'count_goal': 0, 'main': 0, 'main_target': 0, 'main_total': 0, 'sub': 0, 'sub_target': 0, 'media': 0, 'media_total': 0,
                       'stage': 'start', 'wait': 0}

    # ------------------------------------------------------------ 网络
    def _report(self, **kw):
        self._state.update(kw)
        self.progress(dict(self._state))

    def _sleep(self, sec):
        """可以被「停止」打断的等待"""
        if self.cancel.wait(sec):
            raise Canceled()

    def _pause(self):
        self._sleep(random.uniform(0.45, 0.9))      # 像人在翻页：太快会被限流

    def _wbi(self, params):
        if self._mixin is None:
            nav = self.s.get(API + '/x/web-interface/nav', timeout=12).json().get('data') or {}
            img = nav.get('wbi_img') or {}
            lookup = ''.join(u.rpartition('/')[2].partition('.')[0] for u in (img.get('img_url', ''), img.get('sub_url', '')))
            if len(lookup) < 64:
                raise CommentError('B站接口有变化，暂时拿不到评论（签名参数获取失败）')
            self._mixin = ''.join(lookup[i] for i in _MIXIN_TAB)[:32]
        p = dict(params, wts=round(time.time()))
        p = {k: ''.join(ch for ch in str(v) if ch not in "!'()*") for k, v in sorted(p.items())}
        p['w_rid'] = hashlib.md5((urllib.parse.urlencode(p) + self._mixin).encode()).hexdigest()
        return p

    def _get(self, path, params, sign=False):
        """GET 接口，返回 data。被限流（412）时等 5 / 15 / 40 秒再试，网络错误重试两次。"""
        import requests
        waits = [5, 15, 40]
        net_fail = 0
        while True:
            if self.cancel.is_set():
                raise Canceled()
            try:
                r = self.s.get(API + path, params=self._wbi(params) if sign else params, timeout=15)
                limited = r.status_code == 412
                j = {} if limited else r.json()
            except (requests.RequestException, ValueError) as e:
                net_fail += 1
                if net_fail > 2:
                    raise CommentError(f'网络错误：{e.__class__.__name__}') from e
                self._sleep(3)
                continue
            code = j.get('code', 0) if not limited else -412
            if code == -412 or limited:
                if not waits:
                    raise CommentError('B站暂时限制了访问（请求太频繁），过一会儿再试')
                w = waits.pop(0)
                self._report(wait=w)
                self._sleep(w)
                self._report(wait=0)
                continue
            if code != 0:
                raise CommentError(_ERRORS.get(code) or f'B站返回错误 {code}：{j.get("message") or ""}'.strip())
            return j.get('data') or {}

    def _buvid(self):
        """没有 buvid3 时（比如没登录）先领一个，不然评论接口容易被拒"""
        if any(c.name == 'buvid3' for c in self.s.cookies):
            return
        try:
            d = self.s.get(API + '/x/frontend/finger/spi', timeout=10).json().get('data') or {}
            if d.get('b_3'):
                self.s.cookies.set('buvid3', d['b_3'], domain='.bilibili.com')
                self.s.cookies.set('buvid4', d.get('b_4', ''), domain='.bilibili.com')
        except Exception:  # noqa: BLE001 - 拿不到也能试
            pass

    # ------------------------------------------------------------ 视频信息
    def resolve(self, url):
        """链接 → {aid, bvid, title, owner, owner_mid, cover, pubdate, reply_total, url}"""
        url = (url or '').strip()
        if re.search(r'b23\.tv|bili2233\.cn', url):
            try:
                url = self.s.get(url, allow_redirects=True, timeout=12).url
            except Exception as e:  # noqa: BLE001
                raise CommentError('短链接打不开，检查一下网络') from e
        ep = re.search(r'\bep(\d+)', url)
        bv = re.search(r'\b(BV[0-9A-Za-z]{10})', url)
        av = re.search(r'\bav(\d+)', url, re.I)
        params = None
        if bv:
            params = {'bvid': bv.group(1)}
        elif av:
            params = {'aid': av.group(1)}
        elif ep:
            d = self._get('/pgc/view/web/season', {'ep_id': ep.group(1)})
            eps = d.get('episodes') or []
            e = next((x for x in eps if str(x.get('id') or x.get('ep_id')) == ep.group(1)), None)
            if not e or not e.get('aid'):
                raise CommentError('没找到这一集')
            params = {'aid': e['aid']}
        if not params:
            raise CommentError('这个链接不是 B站视频，暂时只支持 B站的评论')
        v = self._get('/x/web-interface/view', params)
        owner = v.get('owner') or {}
        return {'aid': v['aid'], 'bvid': v.get('bvid', ''), 'title': v.get('title') or '', 'owner': owner.get('name') or '',
                'owner_mid': owner.get('mid'), 'cover': v.get('pic') or '', 'pubdate': v.get('pubdate') or 0,
                'reply_total': (v.get('stat') or {}).get('reply') or 0,
                'url': f'https://www.bilibili.com/video/{v.get("bvid")}' if v.get('bvid') else url}

    # ------------------------------------------------------------ 评论
    @staticmethod
    def _norm(r, upper_mid, pinned=False):
        m = r.get('member') or {}
        c = r.get('content') or {}
        rc = r.get('reply_control') or {}
        ua = r.get('up_action') or {}
        text = c.get('message') or ''
        reply_to = ''
        if r.get('root') and r.get('parent') and r.get('parent') != r.get('root'):
            mm = _REPLY_TO.match(text)
            if mm:
                reply_to, text = mm.group(1).strip(), text[mm.end():]
        pics = [{'url': p['img_src'].replace('http://', 'https://'), 'w': p.get('img_width') or 0,
                 'h': p.get('img_height') or 0, 'kb': p.get('img_size') or 0}
                for p in (c.get('pictures') or []) if p.get('img_src')]
        return {
            'id': r.get('rpid'), 'uid': r.get('mid'), 'name': m.get('uname') or '',
            'avatar': (m.get('avatar') or '').replace('http://', 'https://'),
            'level': (m.get('level_info') or {}).get('current_level') or 0,
            'vip': (m.get('vip') or {}).get('vipStatus') == 1,
            'up': bool(upper_mid) and r.get('mid') == upper_mid, 'pinned': pinned,
            'text': text, 'reply_to': reply_to,
            'emotes': {k: (v.get('url') or '').replace('http://', 'https://') for k, v in (c.get('emote') or {}).items()
                       if v.get('url')},
            'pics': pics, 'like': r.get('like') or 0, 'time': r.get('ctime') or 0,
            'ip': (rc.get('location') or '').replace('IP属地：', '').strip(),
            'up_like': bool(ua.get('like')), 'up_reply': bool(ua.get('reply')),
            'rcount': r.get('rcount') or 0, 'parent': r.get('parent') or 0, 'root': r.get('root') or 0,
            'dialog': r.get('dialog') or 0, 'replies': [],
        }

    def _main(self, meta, out):
        """主评论逐条追加到 out（就是 result.comments）：中途停止或被限流时，已经抓到的不会丢。"""
        o = self.opts
        aid = meta['aid']
        target = o.max_main or 0
        seen = {c['id'] for c in out}
        offset = ''
        upper_mid = meta.get('owner_mid')
        first = True
        self._report(stage='main', main_target=target, main_total=meta['reply_total'])
        while True:
            pag = json.dumps({'offset': offset}, separators=(',', ':'))
            d = self._get('/x/v2/reply/wbi/main', {'oid': aid, 'type': 1, 'mode': 3 if o.sort == 'hot' else 2,
                                                   'pagination_str': pag, 'plat': 1, 'web_location': 1315875}, sign=True)
            cur = d.get('cursor') or {}
            if first:
                first = False
                upper_mid = (d.get('upper') or {}).get('mid') or upper_mid
                meta['owner_mid'] = upper_mid
                meta['upper_mid'] = upper_mid
                if cur.get('all_count'):
                    meta['reply_total'] = cur['all_count']
                    self._state['main_total'] = cur['all_count']
                tops = list(d.get('top_replies') or [])
                up_top = (d.get('upper') or {}).get('top')
                if up_top:
                    tops.insert(0, up_top)
                for t in tops:
                    if target and len(out) >= target:       # 置顶也算在条数里
                        break
                    if t.get('rpid') not in seen:
                        seen.add(t['rpid'])
                        out.append(self._norm(t, upper_mid, pinned=True))
                        out[-1]['_preview'] = t.get('replies') or []
            page = d.get('replies') or []
            for r in page:
                if target and len(out) >= target:
                    break
                if r.get('rpid') in seen:
                    continue
                seen.add(r['rpid'])
                n = self._norm(r, upper_mid)
                n['_preview'] = r.get('replies') or []
                out.append(n)
            self._report(main=len(out))
            if (target and len(out) >= target) or cur.get('is_end') or not page:
                break
            offset = ((cur.get('pagination_reply') or {}).get('next_offset')) or ''
            if not offset:
                break
            self._pause()
        meta['upper_mid'] = upper_mid
        return out

    def _subs(self, meta, mains, result):
        o = self.opts
        if o.sub_mode == 'none':
            for c in mains:
                c.pop('_preview', None)
            return
        want_of = (lambda c: c['rcount']) if o.sub_mode == 'all' else (lambda c: min(c['rcount'], max(1, o.sub_limit)))
        total = sum(want_of(c) for c in mains)
        self._report(stage='sub', sub_target=total)
        got = 0
        for c in mains:
            preview = c.pop('_preview', [])
            want = want_of(c)
            if not want:
                continue
            # 直接往这一层的结果里加：中途停止或被限流时，这一层已经抓到的回复也保留
            subs = c['replies'] = []
            try:
                if c['rcount'] <= len(preview):        # 回复少（≤3 条）：主评论里已经带着了，不用再请求
                    subs += [self._norm(r, meta['upper_mid']) for r in preview[:want]]
                else:
                    seen, pn = set(), 1
                    while len(subs) < want:
                        try:
                            d = self._get('/x/v2/reply/reply', {'oid': meta['aid'], 'type': 1, 'root': c['id'],
                                                                'ps': PAGE, 'pn': pn})
                        except CommentError as e:
                            if '限制' in str(e):
                                raise
                            result.notes.append(f'有一层楼中楼没抓全（{e}）')
                            break
                        page = d.get('replies') or []
                        for r in page:
                            if r.get('rpid') not in seen and len(subs) < want:
                                seen.add(r['rpid'])
                                subs.append(self._norm(r, meta['upper_mid']))
                        self._report(sub=got + len(subs))
                        if len(page) < PAGE:
                            break
                        pn += 1
                        self._pause()
                    self._pause()
            finally:
                subs.sort(key=lambda x: (x['time'], x['id']))
            got += len(subs)
            self._report(sub=got)

    # ------------------------------------------------------------ 图片
    def _media(self, result, out_dir):
        o = self.opts
        jobs = {}                                        # 网址 → (下载用网址, 相对路径)

        def add(url, kind, fetch_url=None, ext=None):
            if not url or url in jobs:
                return
            path = urllib.parse.urlparse(url).path
            ext = ext or (os.path.splitext(path)[1].lower() or '.jpg')
            name = hashlib.md5(url.encode()).hexdigest()[:16] + ext
            jobs[url] = (fetch_url or url, f'{kind}/{name}')

        def small(url, kind, suffix):
            """B站的图可以在网址后面加 @…w.webp 让服务器缩小；别的网站原样下载"""
            if 'hdslb.com' in url:
                add(url, kind, url + suffix, '.webp')
            else:
                add(url, kind)

        meta = result.meta
        if meta.get('cover'):
            small(meta['cover'], '图片', '@640w.webp')
        for c in result.all():
            for k, u in c['emotes'].items():
                if not u.lower().split('?')[0].endswith('.gif'):      # 动态表情不要
                    small(u, '表情', '@96w.webp')
            if o.avatars and c['avatar']:
                small(c['avatar'], '头像', '@80w_80h.webp')
            if o.images:
                for p in c['pics']:
                    if p['url'].lower().split('?')[0].endswith('.gif'):
                        p['gif'] = True                               # 动图：网页里只留个链接
                        continue
                    if o.image_quality == 'original':
                        add(p['url'], '图片')
                    else:
                        w = min(1600, p['w'] or 1600)
                        add(p['url'], '图片', f'{p["url"]}@{w}w.webp', '.webp')
        if not jobs:
            return
        total = len(jobs)
        self._report(stage='media', media=0, media_total=total)
        done = [0]
        lock = threading.Lock()
        import requests

        def get(item):
            url, (fetch_url, rel) = item
            if self.cancel.is_set():
                return url, None
            dest = os.path.join(out_dir, rel)
            try:
                if not os.path.exists(dest):
                    with self.s.get(fetch_url, timeout=25, stream=True) as r:
                        r.raise_for_status()
                        data, size = [], 0
                        for chunk in r.iter_content(256 * 1024):
                            size += len(chunk)
                            if size > MAX_IMAGE_BYTES:
                                raise ValueError('too large')
                            data.append(chunk)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with open(dest, 'wb') as f:
                        f.write(b''.join(data))
                ok = rel
            except (requests.RequestException, ValueError, OSError):
                ok = None
            with lock:
                done[0] += 1
                if done[0] % 5 == 0 or done[0] == total:
                    self._report(media=done[0])
            return url, ok

        with ThreadPoolExecutor(6) as ex:
            for url, rel in ex.map(get, list(jobs.items())):
                if rel:
                    result.media[url] = rel
        if self.cancel.is_set():
            result.stopped = result.stopped or '已停止'
        for c in result.all():
            for p in c['pics']:
                p['local'] = result.media.get(p['url'], '')

    # ------------------------------------------------------------ YouTube
    def _fetch_youtube(self, url):
        vid = _YT_ID.search(url).group(1)
        notes = []
        if self.yt_key:
            try:
                return self._yt_api(vid)
            except CommentError as e:
                if e.fatal:
                    raise
                notes.append(f'官方接口用不了（{e}），改用内置方式抓取')
                self._report(stage='start')
        result = self._yt_dlp(url, vid)
        result.notes[:0] = notes
        return result

    @staticmethod
    def _yt_threads(mains):
        """YouTube 的回复都挂在主评论下面、正文以「@某人」开头：找出回复的是哪一条（同一层里这个人最近的一条），
        再把来回的回复串成「对话」（和 B站的 dialog 一样，网页里能「查看对话」）"""
        for c in mains:
            seen, ids = [], {r['id']: r for r in c['replies']}
            for r in c['replies']:
                target = ids.get(r['parent']) if r['parent'] != c['id'] else None    # yt-dlp 已经知道回复的是哪一条
                m = re.match(r'^\s*@([^\s@]+)\s*', r['text'])
                if m:
                    r['reply_to'] = m.group(1)
                    r['text'] = r['text'][m.end():].strip() or r['text'].strip()
                    who = '@' + m.group(1)
                    if target is None:          # 官方接口不给：按名字找这一层里这个人最近的一条
                        target = next((x for x in reversed(seen) if x['name'] == who), None)
                    if target is None and c['name'] == who:
                        target = c
                elif target is not None:
                    r['reply_to'] = target['name'].lstrip('@')
                if target is None or target is c:
                    r['parent'] = c['id']
                else:
                    target['dialog'] = target['dialog'] or target['id']
                    r['parent'] = target['id']
                    r['dialog'] = target['dialog']
                r['dialog'] = r['dialog'] or r['id']
                seen.append(r)
            c['up_reply'] = c['up_reply'] or any(r['up'] for r in c['replies'])

    @staticmethod
    def _yt_comment(**kw):
        d = {'id': '', 'uid': '', 'name': '', 'avatar': '', 'level': None, 'vip': False, 'up': False, 'pinned': False,
             'verified': False, 'text': '', 'reply_to': '', 'emotes': {}, 'pics': [], 'like': 0, 'time': 0, 'ip': '',
             'up_like': False, 'up_reply': False, 'rcount': 0, 'parent': 0, 'root': 0, 'dialog': 0, 'replies': [],
             'ago': ''}
        d.update(kw)
        return d

    @classmethod
    def _yt_assemble(cls, raw, o):
        """yt-dlp 的评论列表（主评论和回复混在一起）→ 主评论，每条带着它这一层的回复"""
        mains, by_id = [], {}
        for c in raw:
            n = cls._yt_comment(
                id=c.get('id') or '', uid=c.get('author_id') or '', name=c.get('author') or '',
                avatar=c.get('author_thumbnail') or '', up=bool(c.get('author_is_uploader')),
                pinned=bool(c.get('is_pinned')), verified=bool(c.get('author_is_verified')), text=c.get('text') or '',
                like=c.get('like_count') or 0, time=c.get('timestamp') or 0, up_like=bool(c.get('is_favorited')),
                ago=zh_ago(c.get('_time_text')))
            parent = c.get('parent') or 'root'
            if parent == 'root':
                mains.append(n)
                by_id[n['id']] = n
            else:
                # 回复的 ID 是「主评论ID.回复ID」；回复别人的回复时 parent 是那条回复的 ID：都归到主评论这一层
                root = parent.split('.')[0]
                if root in by_id:
                    n['root'], n['parent'] = root, parent
                    by_id[root]['replies'].append(n)
        for c in mains:
            if o.sub_mode == 'none':
                c['replies'] = []
            elif o.sub_mode == 'limit':
                c['replies'] = c['replies'][:max(1, o.sub_limit)]
            c['rcount'] = len(c['replies'])
        cls._yt_threads(mains)
        return mains

    def _yt_dlp(self, url, vid):
        """yt-dlp 抓（不需要密钥）：和解析视频用同一套网络、Cookie 设置。时间是「3 天前」这样估出来的，点赞数是约数"""
        from . import core
        st = self.settings
        if st is None:
            from .config import Settings
            st = Settings()
        o = self.opts
        fetcher = self
        est = [0]
        warns = []
        stop_sent = [False]

        class Log:
            def debug(self, msg):
                if fetcher.cancel.is_set() and not stop_sent[0]:
                    stop_sent[0] = True              # 只发一次：yt-dlp 收到后停止翻页，已经抓到的照样返回
                    raise KeyboardInterrupt
                if msg.startswith('[debug]'):
                    return
                m = re.search(r'Downloading ~(\d+) comments', msg)
                if m:
                    est[0] = int(m.group(1))
                    fetcher._report(stage='all', count=0, count_goal=est[0], main_target=o.max_main, main_total=est[0])
                m = re.search(r'\((\d+)/~\d+\)', msg)
                if m:
                    t = re.search(r'reply thread (\d+)', msg)      # 正在抓第几条主评论的回复
                    fetcher._report(stage='all', count=int(m.group(1)), **({'main': int(t.group(1))} if t else {}))

            info = debug

            def warning(self, msg):
                warns.append(msg)

            def error(self, msg):
                warns.append(msg)

        try:
            core.ensure_js_runtime(url, st)
        except Exception:  # noqa: BLE001 - 没装好 JS 运行环境也先试试
            pass
        opts = core.base_options(st, Log())
        opts['getcomments'] = True
        opts['skip_download'] = True
        # max_comments：总数, 主评论数, 回复总数, 每条主评论的回复数, 层数。不要回复时要把层数设成 1：
        # yt-dlp 对「嵌在主评论里的回复」不看每层的上限
        per_thread = {'all': 'all', 'limit': str(max(1, o.sub_limit)), 'none': '0'}[o.sub_mode]
        opts['extractor_args'] = {'youtube': {
            'max_comments': ['all', str(o.max_main) if o.max_main else 'all', '0' if o.sub_mode == 'none' else 'all',
                             per_thread, '1' if o.sub_mode == 'none' else 'all'],
            'comment_sort': ['top' if o.sort == 'hot' else 'new']}}
        self._report(stage='start')
        try:
            with core.new_ydl(opts) as ydl:
                info = ydl.extract_info(f'https://www.youtube.com/watch?v={vid}', download=False)
        except KeyboardInterrupt:
            raise CommentError('已停止') from None
        except Exception as e:  # noqa: BLE001
            msg = core.friendly_error(e)[0]
            raise CommentError(msg, fatal='不存在' in msg or '私享' in msg or 'Private' in str(e)) from None
        stopped = info.get('comment_count') is None and bool(info.get('comments'))
        raw = info.get('comments')
        if raw is None:
            raise CommentError('这个视频关闭了评论', fatal=True)
        channel = info.get('channel_id') or ''
        meta = {'platform': 'YouTube', 'video_id': vid, 'aid': None, 'bvid': '', 'title': info.get('title') or vid,
                'owner': info.get('uploader') or info.get('channel') or '', 'owner_mid': channel,
                'cover': info.get('thumbnail') or '', 'pubdate': info.get('timestamp') or 0,
                'reply_total': est[0] or 0, 'url': f'https://www.youtube.com/watch?v={vid}', 'time_approx': True}
        mains = self._yt_assemble(raw, o)
        if not mains:
            raise CommentError('这个视频还没有评论', fatal=True)
        result = Result(meta=meta, comments=mains, options=o, fetched_at=time.time())
        if stopped:
            result.stopped = '已停止'
        result.notes.append('YouTube 内置方式：时间是按「几天前」估算的，点赞数是约数；'
                            '想要准确数字，可以填 YouTube Data API v3 密钥改用官方接口')
        return result

    def _yt_api(self, vid):
        """YouTube Data API v3（需要密钥）：commentThreads 一页 100 条，回复超过 5 条的用 comments 补全"""
        import requests
        s = requests.Session()
        s.headers['User-Agent'] = bilibili._UA
        if self.proxy:
            s.proxies = {'http': self.proxy, 'https': self.proxy}

        def call(ep, **params):
            params['key'] = self.yt_key
            for attempt in range(3):
                if self.cancel.is_set():
                    raise Canceled()
                try:
                    r = s.get('https://www.googleapis.com/youtube/v3/' + ep, params=params, timeout=20)
                except requests.RequestException as e:
                    if attempt == 2:
                        raise CommentError(f'连不上官方接口（{e.__class__.__name__}），检查网络或代理') from None
                    self._sleep(2)
                    continue
                try:
                    j = r.json()
                except ValueError:
                    raise CommentError(f'官方接口返回了看不懂的内容（{r.status_code}）') from None
                err = j.get('error')
                if not err:
                    return j
                reasons = {e.get('reason') for e in err.get('errors') or []}
                msg = err.get('message') or ''
                if 'commentsDisabled' in reasons:
                    raise CommentError('这个视频关闭了评论', fatal=True)
                if 'videoNotFound' in reasons:
                    raise CommentError('视频不存在，或者是私享视频', fatal=True)
                if reasons & {'quotaExceeded', 'dailyLimitExceeded', 'rateLimitExceeded'}:
                    raise CommentError('API 密钥今天的额度用完了')
                if 'keyInvalid' in reasons or 'API key not valid' in msg:
                    raise CommentError('API 密钥无效')
                if 'accessNotConfigured' in reasons or 'has not been used' in msg or 'disabled' in msg.lower():
                    raise CommentError('密钥所在的谷歌云项目还没有启用 YouTube Data API v3')
                raise CommentError(f'官方接口出错：{msg[:120] or r.status_code}')
            raise CommentError('官方接口没有响应')

        def iso(t):
            try:
                return int(datetime.fromisoformat((t or '').replace('Z', '+00:00')).timestamp())
            except ValueError:
                return 0

        o = self.opts
        self._report(stage='start')
        items = call('videos', part='snippet,statistics', id=vid).get('items') or []
        if not items:
            raise CommentError('视频不存在，或者是私享视频', fatal=True)
        sn, stat = items[0].get('snippet') or {}, items[0].get('statistics') or {}
        channel = sn.get('channelId') or ''
        thumbs = sn.get('thumbnails') or {}
        cover = next((thumbs[k]['url'] for k in ('maxres', 'standard', 'high', 'medium', 'default') if k in thumbs), '')
        meta = {'platform': 'YouTube', 'video_id': vid, 'aid': None, 'bvid': '', 'title': sn.get('title') or vid,
                'owner': sn.get('channelTitle') or '', 'owner_mid': channel, 'cover': cover,
                'pubdate': iso(sn.get('publishedAt')), 'reply_total': int(stat.get('commentCount') or 0),
                'url': f'https://www.youtube.com/watch?v={vid}', 'time_approx': False}

        def norm(c):
            p = c.get('snippet') or {}
            author = (p.get('authorChannelId') or {}).get('value') or ''
            return self._yt_comment(
                id=c.get('id') or '', uid=author, name=p.get('authorDisplayName') or '',
                avatar=p.get('authorProfileImageUrl') or '', up=bool(channel) and author == channel,
                text=p.get('textOriginal') or p.get('textDisplay') or '', like=p.get('likeCount') or 0,
                time=iso(p.get('publishedAt')))

        result = Result(meta=meta, comments=[], options=o, fetched_at=time.time())
        mains = result.comments
        target = o.max_main or 0
        self._report(stage='main', main_target=target, main_total=meta['reply_total'])
        token = None
        try:
            while True:
                q = {'part': 'snippet,replies', 'videoId': vid, 'maxResults': 100, 'textFormat': 'plainText',
                     'order': 'relevance' if o.sort == 'hot' else 'time'}
                if token:
                    q['pageToken'] = token
                j = call('commentThreads', **q)
                for it in j.get('items') or []:
                    snip = it.get('snippet') or {}
                    n = norm(snip.get('topLevelComment') or {})
                    n['rcount'] = snip.get('totalReplyCount') or 0
                    n['_inline'] = (it.get('replies') or {}).get('comments') or []
                    mains.append(n)
                    if target and len(mains) >= target:
                        break
                self._report(main=len(mains))
                token = j.get('nextPageToken')
                if not token or (target and len(mains) >= target):
                    break
            if o.sub_mode != 'none':
                want_of = (lambda c: c['rcount']) if o.sub_mode == 'all' else (lambda c: min(c['rcount'], max(1, o.sub_limit)))
                self._report(stage='sub', sub_target=sum(want_of(c) for c in mains))
                got = 0
                for c in mains:
                    want = want_of(c)
                    reps = c['_inline']
                    if want > len(reps):
                        reps, token = [], None
                        while len(reps) < want:
                            q = {'part': 'snippet', 'parentId': c['id'], 'maxResults': 100, 'textFormat': 'plainText'}
                            if token:
                                q['pageToken'] = token
                            j = call('comments', **q)
                            reps += j.get('items') or []
                            token = j.get('nextPageToken')
                            if not token:
                                break
                    subs = sorted((norm(r) for r in reps), key=lambda x: x['time'])[:want] if want else []
                    for r in subs:
                        r['root'] = r['parent'] = c['id']
                    c['replies'] = subs
                    got += len(subs)
                    self._report(sub=got)
        except Canceled:
            result.stopped = '已停止'
        except CommentError as e:
            if not mains or e.fatal:
                raise
            result.stopped = str(e)
        for c in mains:
            c.pop('_inline', None)
        self._yt_threads(mains)
        if not mains:
            raise CommentError('这个视频还没有评论', fatal=True)
        return result

    # ------------------------------------------------------------ 总流程
    def fetch(self, url):
        """抓取 + 导出。中途停止或被限流时，已经抓到的照样导出（Result.stopped 写明原因）。"""
        if platform_of(url) == 'youtube':
            return self._finish(self._fetch_youtube(url))
        return self._finish(self._fetch_bili(url))

    def _fetch_bili(self, url):
        self._buvid()
        meta = self.resolve(url)
        meta['platform'] = 'B站'
        meta['video_id'] = meta.get('bvid') or f'av{meta["aid"]}'
        result = Result(meta=meta, comments=[], options=self.opts, fetched_at=time.time())
        if not self.logged_in:
            result.notes.append('没有登录 B站：B站只给未登录的人看前几页评论，左下角头像菜单里扫码登录后能抓完整')
        meta.setdefault('upper_mid', meta.get('owner_mid'))
        try:
            self._main(meta, result.comments)
            self._subs(meta, result.comments, result)
        except Canceled:
            result.stopped = '已停止'
        except CommentError as e:
            if not result.comments:
                raise
            result.stopped = str(e)
        for c in result.comments:
            c.pop('_preview', None)
        if not result.comments:
            if result.stopped:
                raise CommentError('已停止，还没抓到评论')
            raise CommentError('这个视频还没有评论' if not meta['reply_total'] else '没有拿到评论（可能需要登录 B站）')
        return result

    def _finish(self, result):
        """下载图片、头像（停止了就不下），导出文件"""
        out_dir = make_out_dir(self.opts.out_root, result.meta)
        result.out_dir = out_dir
        was_canceled = self.cancel.is_set()
        if was_canceled:                   # 停止后也要导出：图片就不下了
            self.cancel = threading.Event()
        else:
            self._media(result, out_dir)
        self._report(stage='export')
        export(result, out_dir)
        return result


# ================================================================ 导出

def _safe(name, limit=60):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', name or '').strip(' .')
    return (name[:limit].rstrip(' .') or '未命名视频')


def make_out_dir(root, meta):
    stamp = datetime.now().strftime('%Y-%m-%d %H%M')
    base = os.path.join(root, f'{_safe(meta["title"])} 的评论 {stamp}')
    path, i = base, 2
    while os.path.exists(path):
        path, i = f'{base} ({i})', i + 1
    os.makedirs(path)
    return path


def _fmt_time(ts):
    return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S') if ts else ''


COLUMNS = [('楼层', 7), ('类型', 8), ('用户名', 18), ('UID', 13), ('等级', 6), ('点赞', 8), ('回复数', 8), ('发布时间', 20),
           ('IP属地', 9), ('回复对象', 16), ('内容', 70), ('图片', 30), ('UP主点赞', 9), ('UP主回复', 9), ('置顶', 6),
           ('评论ID', 14), ('所属楼层ID', 14)]


def _columns(result):
    """YouTube 没有等级、IP 属地、图片；「UP主」叫「作者」，时间是估算的"""
    if result.meta.get('platform') != 'YouTube':
        return COLUMNS
    rename = {'UP主点赞': '作者点爱心', 'UP主回复': '作者回复', 'UID': '频道ID',
              '发布时间': '发布时间（估算）' if result.meta.get('time_approx') else '发布时间'}
    return [(rename.get(n, n), w) for n, w in COLUMNS]


def _rows(result):
    """表格的每一行（主评论后面紧跟它的楼中楼）。返回 [(层级, [值…])]，层级 0 = 主评论，1 = 楼中楼"""
    out = []
    for i, c in enumerate(result.comments, 1):
        for j, x in enumerate([c] + c['replies']):
            pics = '\n'.join(p.get('local') or p['url'] for p in x['pics'])
            out.append((0 if j == 0 else 1, [
                f'{i}' if j == 0 else f'{i}-{j}', '主评论' if j == 0 else '楼中楼', x['name'], x['uid'] or '',
                '' if x['level'] is None else x['level'],
                x['like'], x['rcount'] if j == 0 else '',
                (f"{x.get('ago')}（约 {_fmt_time(x['time'])[:10]}）" if x.get('ago') else _fmt_time(x['time'])[:10])
                if result.meta.get('time_approx') else _fmt_time(x['time']), x['ip'],
                x['reply_to'] or ('' if j == 0 else c['name']), x['text'], pics,
                '是' if x['up_like'] else '', '是' if x['up_reply'] else '', '是' if x['pinned'] else '',
                str(x['id']), '' if j == 0 else str(c['id'])]))
    return out


_FORMULA_START = ('=', '+', '-', '@', '\t', '\r')
_DATE_LIKE = re.compile(r'\d{1,4}[-/.]\d{1,4}(?:[-/.]\d{1,4})?')   # 楼层「1-2」、「3/4」：Excel 会当成日期
_LONG_NUMBER = re.compile(r'\d{12,}')                             # 长 ID：Excel 显示成科学计数法，超过 15 位还丢精度


def _csv_text(v):
    """以 = + - @ 开头的文字（B站常见的「-阿伟-」「=_=」「+1」「@某人」）在 Excel 里会被当成公式：
    显示成 #NAME?、吞掉符号，恶意内容还能做成可点的公式。前面加一个制表符，Excel 就按文字显示。
    （OWASP 建议的 Excel 可靠做法：制表符放在带引号的字段里，所以写入时全部加引号）
    楼层号「1-2」会被认成日期、12 位以上的 UID / 评论 ID 会显示成科学计数法甚至丢精度，也一样按文字写"""
    if isinstance(v, int) and not isinstance(v, bool) and abs(v) >= 10 ** 11:
        v = str(v)
    if isinstance(v, str) and (v.startswith(_FORMULA_START) or _DATE_LIKE.fullmatch(v) or _LONG_NUMBER.fullmatch(v)):
        return '\t' + v
    return v


def write_csv(result, path):
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:      # 带 BOM：Excel 直接双击打开不乱码
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        w.writerow([c for c, _ in _columns(result)])
        for _, row in _rows(result):
            w.writerow([_csv_text(v) for v in row])


_XML_BAD = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]')


def _x(s):
    s = _XML_BAD.sub('', str(s))[:32000]
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def _col(i):
    s = ''
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def write_xlsx(result, path):
    """不依赖第三方库的最小 .xlsx：冻结表头、可筛选、楼中楼分组（Excel 左边的 +/- 可以折叠）"""
    rows = _rows(result)
    cols = _columns(result)
    num_cols = {4, 5, 6}
    sheet = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
             '<sheetPr><outlinePr summaryBelow="0"/></sheetPr>',
             '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
             'state="frozen"/></sheetView></sheetViews>',
             '<sheetFormatPr defaultRowHeight="16" outlineLevelRow="1"/>', '<cols>']
    for i, (_, w) in enumerate(cols, 1):
        sheet.append(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>')
    sheet.append('</cols><sheetData>')
    sheet.append('<row r="1">' + ''.join(f'<c r="{_col(i)}1" t="inlineStr" s="1"><is><t>{_x(h)}</t></is></c>'
                                         for i, (h, _) in enumerate(cols)) + '</row>')
    for n, (level, vals) in enumerate(rows, 2):
        cells = []
        for i, v in enumerate(vals):
            ref = f'{_col(i)}{n}'
            style = 3 if level else 2
            if i in num_cols and isinstance(v, int):
                cells.append(f'<c r="{ref}" s="{style}"><v>{v}</v></c>')
            elif v != '':
                cells.append(f'<c r="{ref}" t="inlineStr" s="{style}"><is><t xml:space="preserve">{_x(v)}</t></is></c>')
        attrs = ' outlineLevel="1"' if level else ''
        sheet.append(f'<row r="{n}"{attrs}>' + ''.join(cells) + '</row>')
    sheet.append(f'</sheetData><autoFilter ref="A1:{_col(len(cols) - 1)}{len(rows) + 1}"/></worksheet>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<fonts count="3"><font><sz val="11"/><name val="Microsoft YaHei"/></font>'
              '<font><b/><sz val="11"/><name val="Microsoft YaHei"/></font>'
              '<font><sz val="11"/><color rgb="FF6B6A65"/><name val="Microsoft YaHei"/></font></fonts>'
              '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
              '<fill><patternFill patternType="solid"><fgColor rgb="FFF3F3F0"/></patternFill></fill></fills>'
              '<borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs>'
              '<cellXfs count="4"><xf/>'
              '<xf fontId="1" fillId="2" applyFont="1" applyFill="1"><alignment vertical="center"/></xf>'
              '<xf applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
              '<xf fontId="2" applyFont="1" applyAlignment="1"><alignment vertical="top" wrapText="1" indent="1"/></xf>'
              '</cellXfs></styleSheet>')
    files = {
        '[Content_Types].xml': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '</Types>',
        '_rels/.rels': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>',
        'xl/workbook.xml': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="评论" sheetId="1" r:id="rId1"/></sheets>'
        '<definedNames><definedName name="_xlnm._FilterDatabase" localSheetId="0" hidden="1">'
        f"'评论'!$A$1:${_col(len(cols) - 1)}${len(rows) + 1}</definedName></definedNames></workbook>",
        'xl/_rels/workbook.xml.rels': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/></Relationships>',
        'xl/worksheets/sheet1.xml': ''.join(sheet),
        'xl/styles.xml': styles,
    }
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)


def page_data(result):
    """网页里用的数据（只留需要的字段；图片、头像、表情换成本地相对路径，没下载到的保留网址）"""
    media = result.media
    o = result.options

    def one(x):
        d = {k: x.get(k) for k in ('id', 'uid', 'name', 'level', 'vip', 'up', 'pinned', 'verified', 'text', 'reply_to', 'like',
                                   'time', 'ago', 'ip', 'up_like', 'up_reply', 'rcount', 'parent', 'dialog')}
        # 头像没下载到（比如手机连不上 YouTube 的图片服务器）：留原网址，联网打开网页时还能显示
        d['avatar'] = media.get(x['avatar']) or (x['avatar'] if o.avatars else '')
        d['emotes'] = {k: media[u] for k, u in x['emotes'].items() if u in media}
        d['pics'] = [{'src': p.get('local') or '', 'url': p['url'], 'w': p['w'], 'h': p['h'], 'gif': bool(p.get('gif'))}
                     for p in x['pics']]
        return d

    comments = []
    for c in result.comments:
        d = one(c)
        d['replies'] = [one(r) for r in c['replies']]
        comments.append(d)
    m = result.meta
    return {
        'meta': {'title': m['title'], 'owner': m['owner'], 'url': m['url'], 'bvid': m.get('bvid', ''),
                 'platform': m.get('platform') or 'B站', 'video_id': m.get('video_id') or m.get('bvid', ''),
                 'time_approx': bool(m.get('time_approx')),
                 'pubdate': m.get('pubdate') or 0, 'reply_total': m.get('reply_total') or 0,
                 'cover': media.get(m.get('cover'), '')},
        'fetched_at': int(result.fetched_at), 'stopped': result.stopped, 'notes': result.notes,
        'options': {'sort': o.sort, 'max_main': o.max_main, 'sub_mode': o.sub_mode, 'sub_limit': o.sub_limit,
                    'images': o.images, 'avatars': o.avatars},
        'comments': comments,
    }


def _json_for_script(obj):
    s = json.dumps(obj, ensure_ascii=False, separators=(',', ':'))
    return s.replace('<', '\\u003c').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')


_MIME = {'.webp': 'image/webp', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.gif': 'image/gif',
         '.bmp': 'image/bmp', '.avif': 'image/avif'}


def _embedded(result, out_dir):
    """相对路径 → data: 网址（每张图只嵌一次，网页里按路径查）"""
    import base64
    out = {}
    for rel in set(result.media.values()):
        p = os.path.join(out_dir, rel)
        try:
            with open(p, 'rb') as f:
                b = base64.b64encode(f.read()).decode('ascii')
        except OSError:
            continue
        out[rel] = f'data:{_MIME.get(os.path.splitext(rel)[1].lower(), "image/webp")};base64,{b}'
    return out


def write_html(result, path, logo_svg=''):
    from . import comment_page
    data = _json_for_script(page_data(result))
    media = _json_for_script(_embedded(result, os.path.dirname(path)) if result.options.embed else {})
    html_text = comment_page.render(result.meta['title'], data, logo_svg, media)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html_text)


LOGO_SVG = ''          # 界面层启动时填上软件的标志（星芒落点），网页页眉用


def export(result, out_dir):
    fmts = result.options.formats or ('html',)
    if 'html' in fmts:
        p = os.path.join(out_dir, '评论.html')
        write_html(result, p, LOGO_SVG)
        result.files['html'] = p
    if 'xlsx' in fmts:
        p = os.path.join(out_dir, '评论.xlsx')
        write_xlsx(result, p)
        result.files['xlsx'] = p
    if 'csv' in fmts:
        p = os.path.join(out_dir, '评论.csv')
        write_csv(result, p)
        result.files['csv'] = p


# ================================================================ 上次抓的结果（重新打开「抓取评论」时直接显示）

INDEX_PATH = os.path.join(bilibili.DATA_DIR, 'comment_exports.json')
_index_lock = threading.Lock()


def _load_index():
    try:
        with open(INDEX_PATH, encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def remember(result, *urls):
    """记下这次导出的文件（按视频，同一个视频只记最近一次）"""
    entry = {'time': int(result.fetched_at), 'title': result.meta.get('title') or '', 'out_dir': result.out_dir,
             'files': dict(result.files), 'n_main': result.n_main, 'n_sub': result.n_sub, 'n_pics': result.n_pics,
             'stopped': result.stopped, 'platform': result.meta.get('platform') or ''}
    keys = {video_key(u) for u in urls + (result.meta.get('url') or '',) if u}
    with _index_lock:
        idx = _load_index()
        for k in keys:
            idx[k] = entry
        if len(idx) > 500:                       # 只留最近 500 个视频
            idx = dict(sorted(idx.items(), key=lambda kv: kv[1].get('time', 0))[-500:])
        try:
            os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
            tmp = INDEX_PATH + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(idx, f, ensure_ascii=False)
            os.replace(tmp, INDEX_PATH)
        except OSError:
            pass


def last_export(url):
    """这个视频上次导出的文件还在的话返回记录（files 里只留还在的文件），否则 None"""
    e = _load_index().get(video_key(url))
    if not e:
        return None
    files = {k: p for k, p in (e.get('files') or {}).items() if p and os.path.exists(p)}
    if not files:
        return None
    return dict(e, files=files)
