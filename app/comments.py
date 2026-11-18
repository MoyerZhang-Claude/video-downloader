"""B站评论区抓取 + 导出（网页 / Excel / CSV）。

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
    pass


class Canceled(Exception):
    pass


def supported(url):
    """能抓评论的链接：B站视频（BV / av）、番剧和影视的某一集（ep）、b23.tv 短链"""
    return bool(re.search(r'(bilibili\.com/(video|bangumi/play|list/)|b23\.tv/|bili2233\.cn/|\bBV[0-9A-Za-z]{10}\b'
                          r'|\bav\d+\b|\bep\d+\b)', url or '', re.I))


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

    def __init__(self, opts, proxy='', progress=None, cancel=None):
        import requests
        self.opts = opts
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
        self._state = {'main': 0, 'main_target': 0, 'main_total': 0, 'sub': 0, 'sub_target': 0, 'media': 0, 'media_total': 0,
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
            if c['rcount'] <= len(preview):        # 回复少（≤3 条）：主评论里已经带着了，不用再请求
                subs = [self._norm(r, meta['upper_mid']) for r in preview[:want]]
            else:
                subs, seen, pn = [], set(), 1
                while len(subs) < want:
                    try:
                        d = self._get('/x/v2/reply/reply', {'oid': meta['aid'], 'type': 1, 'root': c['id'], 'ps': PAGE,
                                                            'pn': pn})
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
            subs.sort(key=lambda x: (x['time'], x['id']))
            c['replies'] = subs
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

        meta = result.meta
        if meta.get('cover'):
            add(meta['cover'], '图片', meta['cover'] + '@640w.webp', '.webp')
        for c in result.all():
            for k, u in c['emotes'].items():
                if not u.lower().split('?')[0].endswith('.gif'):      # 动态表情不要
                    add(u, '表情', u + '@96w.webp', '.webp')
            if o.avatars and c['avatar']:
                add(c['avatar'], '头像', c['avatar'] + '@80w_80h.webp', '.webp')
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

    # ------------------------------------------------------------ 总流程
    def fetch(self, url):
        """抓取 + 导出。中途停止或被限流时，已经抓到的照样导出（Result.stopped 写明原因）。"""
        self._buvid()
        meta = self.resolve(url)
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
        out_dir = make_out_dir(self.opts.out_root, meta)
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


def _rows(result):
    """表格的每一行（主评论后面紧跟它的楼中楼）。返回 [(层级, [值…])]，层级 0 = 主评论，1 = 楼中楼"""
    out = []
    for i, c in enumerate(result.comments, 1):
        for j, x in enumerate([c] + c['replies']):
            pics = '\n'.join(p.get('local') or p['url'] for p in x['pics'])
            out.append((0 if j == 0 else 1, [
                f'{i}' if j == 0 else f'{i}-{j}', '主评论' if j == 0 else '楼中楼', x['name'], x['uid'] or '', x['level'],
                x['like'], x['rcount'] if j == 0 else '', _fmt_time(x['time']), x['ip'],
                x['reply_to'] or ('' if j == 0 else c['name']), x['text'], pics,
                '是' if x['up_like'] else '', '是' if x['up_reply'] else '', '是' if x['pinned'] else '',
                str(x['id']), '' if j == 0 else str(c['id'])]))
    return out


_FORMULA_START = ('=', '+', '-', '@', '\t', '\r')


def _csv_text(v):
    """以 = + - @ 开头的文字（B站常见的「-阿伟-」「=_=」「+1」「@某人」）在 Excel 里会被当成公式：
    显示成 #NAME?、吞掉符号，恶意内容还能做成可点的公式。前面加一个制表符，Excel 就按文字显示。
    （OWASP 建议的 Excel 可靠做法：制表符放在带引号的字段里，所以写入时全部加引号）"""
    if isinstance(v, str) and v.startswith(_FORMULA_START):
        return '\t' + v
    return v


def write_csv(result, path):
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:      # 带 BOM：Excel 直接双击打开不乱码
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        w.writerow([c for c, _ in COLUMNS])
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
    num_cols = {4, 5, 6}
    sheet = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
             '<sheetPr><outlinePr summaryBelow="0"/></sheetPr>',
             '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
             'state="frozen"/></sheetView></sheetViews>',
             '<sheetFormatPr defaultRowHeight="16" outlineLevelRow="1"/>', '<cols>']
    for i, (_, w) in enumerate(COLUMNS, 1):
        sheet.append(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>')
    sheet.append('</cols><sheetData>')
    sheet.append('<row r="1">' + ''.join(f'<c r="{_col(i)}1" t="inlineStr" s="1"><is><t>{_x(h)}</t></is></c>'
                                         for i, (h, _) in enumerate(COLUMNS)) + '</row>')
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
    sheet.append(f'</sheetData><autoFilter ref="A1:{_col(len(COLUMNS) - 1)}{len(rows) + 1}"/></worksheet>')
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
        f"'评论'!$A$1:${_col(len(COLUMNS) - 1)}${len(rows) + 1}</definedName></definedNames></workbook>",
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
        d = {k: x[k] for k in ('id', 'uid', 'name', 'level', 'vip', 'up', 'pinned', 'text', 'reply_to', 'like', 'time', 'ip',
                               'up_like', 'up_reply', 'rcount', 'parent', 'dialog')}
        d['avatar'] = media.get(x['avatar'], '')
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
