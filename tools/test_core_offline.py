"""共享 Python 核心（电脑版、手机版都用）的离线回归测试：不联网、不碰真实账号，外部服务用本地服务器或替身代替。

运行：.venv\\Scripts\\python.exe tools\\test_core_offline.py
"""
import csv
import hashlib
import http.cookiejar
import http.server
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request

_TMP = tempfile.mkdtemp(prefix='vd-test-')
os.environ['APPDATA'] = _TMP          # 数据目录放到临时文件夹：不动本机的设置和历史
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import comments, core, fastdl, gdrive, page_sniff, task_store  # noqa: E402


# ---------------------------------------------------------------- 工具

class _Server:
    """本地 HTTP 服务器：handler(请求处理器, 第几次请求) 负责回应"""

    def __init__(self, handler):
        outer = self
        self.count = 0
        self.seen = []

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                with lock:
                    outer.count += 1
                    n = outer.count
                outer.seen.append((self.headers.get('Host'), self.path, self.headers.get('Cookie')))
                try:
                    handler(self, n)
                except (BrokenPipeError, ConnectionResetError):
                    pass                  # 客户端发现内容不对主动断开

        lock = threading.Lock()
        self.srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def _reply(rpid, name='用户', text='评论', pinned=False):
    return {'rpid': rpid, 'mid': rpid * 10, 'member': {'uname': name}, 'content': {'message': text},
            'like': 1, 'ctime': 1700000000 + rpid}


def _fetcher(pages, opts=None, fail_on=None):
    """不联网的评论抓取器：pages 是每次请求主评论接口返回的 data；fail_on = 第几次请求抛限流"""
    o = opts or comments.Options(formats=('csv',), images=False, avatars=False, sub_mode='none')
    o.out_root = tempfile.mkdtemp(dir=_TMP)
    f = comments.Fetcher.__new__(comments.Fetcher)
    f.opts, f.progress, f.cancel, f.logged_in, f._mixin = o, (lambda d: None), threading.Event(), True, 'x'
    f._state = {}
    f._buvid = lambda: None
    f.resolve = lambda url: {'aid': 1, 'reply_total': 100, 'owner_mid': 0, 'title': '测试视频'}
    calls = [0]

    def get(path, params, sign=False):
        calls[0] += 1
        if fail_on and calls[0] == fail_on:
            raise comments.CommentError('B站限制了访问频率，请过一会儿再试')
        return pages[min(calls[0], len(pages)) - 1]
    f._get = get
    return f


def _page(replies, end=False, tops=()):
    return {'cursor': {'is_end': end, 'pagination_reply': {'next_offset': 'next'}, 'all_count': 100},
            'replies': replies, 'top_replies': list(tops)}


# ---------------------------------------------------------------- 评论抓取

class CommentFetchTest(unittest.TestCase):
    def test_stop_between_pages_keeps_fetched(self):
        f = _fetcher([_page([_reply(1), _reply(2)])])

        def stop():                       # 翻页间点了「停止」
            f.cancel.set()
            raise comments.Canceled()
        f._pause = stop
        r = f.fetch('https://www.bilibili.com/video/BV1xx411c7mD')
        self.assertEqual(r.n_main, 2)
        self.assertEqual(r.stopped, '已停止')
        self.assertTrue(os.path.isfile(r.files['csv']))

    def test_rate_limited_on_later_page_keeps_fetched(self):
        f = _fetcher([_page([_reply(1), _reply(2)]), _page([_reply(3)])], fail_on=2)
        f._pause = lambda: None
        r = f.fetch('https://www.bilibili.com/video/BV1xx411c7mD')
        self.assertEqual(r.n_main, 2)
        self.assertIn('限制', r.stopped)

    def test_stop_before_any_comment_says_stopped(self):
        f = _fetcher([_page([])])

        def get(*a, **k):
            f.cancel.set()
            raise comments.Canceled()
        f._get = get
        with self.assertRaises(comments.CommentError) as cm:
            f.fetch('https://www.bilibili.com/video/BV1xx411c7mD')
        self.assertIn('已停止', str(cm.exception))

    def test_pinned_counts_toward_limit(self):
        o = comments.Options(formats=('csv',), images=False, avatars=False, sub_mode='none', max_main=1)
        f = _fetcher([_page([_reply(2)], tops=[_reply(1)])], opts=o)
        f._pause = lambda: None
        r = f.fetch('https://www.bilibili.com/video/BV1xx411c7mD')
        self.assertEqual(r.n_main, 1)
        self.assertTrue(r.comments[0]['pinned'])


class CsvTest(unittest.TestCase):
    def test_formula_like_text_is_neutralized(self):
        names = ['-阿伟-', '=_=', '+1', '@某人', '普通用户']
        res = comments.Result(meta={'title': 't'}, comments=[], options=comments.Options())
        for i, n in enumerate(names, 1):
            c = comments.Fetcher._norm(_reply(i, name=n, text=n), 0)
            res.comments.append(c)
        path = os.path.join(tempfile.mkdtemp(dir=_TMP), 'c.csv')
        comments.write_csv(res, path)
        with open(path, encoding='utf-8-sig', newline='') as f:
            rows = list(csv.reader(f))[1:]
        got = [r[2] for r in rows]
        self.assertEqual(got[:4], ['\t' + n for n in names[:4]])
        self.assertEqual(got[4], '普通用户')
        for r, n in zip(rows, names):
            self.assertEqual(r[10].lstrip('\t'), n)        # 内容列也处理了，去掉前缀后和原文一样


# ---------------------------------------------------------------- 链接提取

class ExtractUrlsTest(unittest.TestCase):
    def test_parentheses(self):
        cases = {
            '看 https://example.com/wiki/movie_(cut) 不错': 'https://example.com/wiki/movie_(cut)',
            '(见 https://a.com/x)。': 'https://a.com/x',
            'https://a.com/x).': 'https://a.com/x',
            'https://a.com/movie_(cut)).': 'https://a.com/movie_(cut)',
            '链接：https://www.bilibili.com/video/BV1xx411c7mD?p=2!': 'https://www.bilibili.com/video/BV1xx411c7mD?p=2',
        }
        for text, want in cases.items():
            self.assertEqual(core.extract_urls(text), [want], text)


# ---------------------------------------------------------------- 谷歌云盘

class SidecarTest(unittest.TestCase):
    def test_only_generated_attachments(self):
        d = tempfile.mkdtemp(dir=_TMP)
        names = ['Ep.1.mp4', 'Ep.1.jpg', 'Ep.1.zh-Hans.srt', 'Ep.1.danmaku.ass', 'Ep.1.danmaku.xml',
                 'Ep.1.5 特别篇.mp4', 'Ep.1.5 特别篇.jpg', 'Ep.1.笔记.txt', 'Ep.1.notes.txt', 'Ep.1.mp4.part']
        for n in names:
            open(os.path.join(d, n), 'wb').close()
        got = [os.path.basename(p) for p in gdrive.sidecars(os.path.join(d, 'Ep.1.mp4'))]
        self.assertEqual(got, ['Ep.1.danmaku.ass', 'Ep.1.danmaku.xml', 'Ep.1.jpg', 'Ep.1.zh-Hans.srt'])


class _FakeDrive:
    def __init__(self, remote):
        self.remote = remote          # [{'id', 'size', 'md5Checksum'}]
        self.uploads = 0

    def ensure_folder(self, parts):
        return 'folder'

    def _list(self, name, parent, folder=False):
        return self.remote

    def find_same(self, *a, **k):
        return gdrive.Drive.find_same(self, *a, **k)

    def start_upload(self, path, parent, name):
        return 'session'

    def upload(self, path, session, progress, cancelled):
        self.uploads += 1
        return 'new-id'


class UploaderDedupTest(unittest.TestCase):
    def _run(self, remote_md5):
        d = tempfile.mkdtemp(dir=_TMP)
        path = os.path.join(d, 'v.mp4')
        with open(path, 'wb') as f:
            f.write(b'new!')
        drive = _FakeDrive([{'id': 'old-id', 'size': '4', 'md5Checksum': remote_md5}])
        up = gdrive.Uploader.__new__(gdrive.Uploader)
        up.drive, up._lock, up.uploaded = drive, threading.RLock(), {}
        up._persist = lambda: None
        up.on_change = lambda j: None
        recycled = []
        orig = gdrive.to_recycle_bin
        gdrive.to_recycle_bin = lambda p: recycled.append(p) or True
        try:
            j = {'path': path, 'folder': ['a'], 'size': 4, 'session': '', 'keep_local': False, 'state': 'running',
                 'done': 0}
            up._run(j)
        finally:
            gdrive.to_recycle_bin = orig
        return drive, j, recycled, up, path

    def test_same_name_size_but_different_content_is_uploaded(self):
        drive, j, recycled, _, _ = self._run(hashlib.md5(b'old!').hexdigest())
        self.assertEqual(drive.uploads, 1)
        self.assertEqual(j['file_id'], 'new-id')
        self.assertEqual(len(recycled), 1)          # 真传上去了，才按设置回收本地文件

    def test_identical_content_skips_upload(self):
        drive, j, recycled, _, _ = self._run(hashlib.md5(b'new!').hexdigest())
        self.assertEqual(drive.uploads, 0)
        self.assertEqual(j['file_id'], 'old-id')

    def test_replaced_file_is_not_considered_uploaded(self):
        _, _, _, up, path = self._run(hashlib.md5(b'new!').hexdigest())
        self.assertIsNotNone(up.uploaded_info(path))
        with open(path, 'wb') as f:
            f.write(b'a newer, longer version')
        self.assertIsNone(up.uploaded_info(path))


# ---------------------------------------------------------------- 网页嗅探的 Cookie

def _cookie(name, value, domain, path='/', secure=False, subdomains=None):
    sub = domain.startswith('.') if subdomains is None else subdomains
    return http.cookiejar.Cookie(0, name, value, None, False, domain, sub, domain.startswith('.'), path, True,
                                 secure, None, True, None, None, {})


class SniffCookieTest(unittest.TestCase):
    def test_same_site_handles_two_part_suffix(self):
        self.assertFalse(page_sniff._same_site('https://a.sina.com.cn/x', 'https://evil.com.cn/y'))
        self.assertTrue(page_sniff._same_site('https://www.weibo.com/x', 'https://video.weibo.com/y'))
        self.assertFalse(page_sniff._same_site('https://www.weibo.com/x', 'https://evilweibo.com/y'))

    def test_cookies_matched_per_url(self):
        jar = [_cookie('SID', 'google', '.google.com'), _cookie('YSC', 'yt', '.youtube.com'),
               _cookie('sec', 's', '.youtube.com', secure=True), _cookie('p', 'root', '.youtube.com'),
               _cookie('p', 'deep', '.youtube.com', path='/watch'), _cookie('host', 'h', 'm.youtube.com')]
        self.assertEqual(page_sniff._cookies_for('https://www.youtube.com/watch?v=1', jar),
                         {'YSC': 'yt', 'sec': 's', 'p': 'deep'})
        self.assertEqual(page_sniff._cookies_for('http://www.youtube.com/', jar), {'YSC': 'yt', 'p': 'root'})
        self.assertEqual(page_sniff._cookies_for('https://m.youtube.com/watchlater', jar),
                         {'YSC': 'yt', 'sec': 's', 'p': 'root', 'host': 'h'})
        self.assertIsNone(page_sniff._cookies_for('https://evil.com/', jar))

    def test_redirect_to_other_host_drops_cookies(self):
        def handle(h, n):
            if h.path == '/start':
                h.send_response(302)
                h.send_header('Location', f'http://localhost:{srv.port}/landed')
                h.end_headers()
            else:
                h.send_response(200)
                h.send_header('Content-Type', 'text/html')
                h.end_headers()
                h.wfile.write(b'<html></html>')
        srv = _Server(handle)
        try:
            import requests
            r = page_sniff._fetch(requests.Session(), f'http://127.0.0.1:{srv.port}/start',
                                  cookies=[_cookie('SESSDATA', 'secret', '127.0.0.1')])
            self.assertEqual(r.status_code, 200)
            self.assertEqual(srv.seen[0][2], 'SESSDATA=secret')     # 本站请求带着
            self.assertTrue(srv.seen[1][0].startswith('localhost'))
            self.assertIsNone(srv.seen[1][2])                       # 跳到别的主机就不带
        finally:
            srv.close()


# ---------------------------------------------------------------- 任务列表保存

class TaskStoreTest(unittest.TestCase):
    def test_older_snapshot_cannot_overwrite_newer(self):
        old_path = task_store.PATH
        task_store.PATH = os.path.join(tempfile.mkdtemp(dir=_TMP), 'tasks.json')
        try:
            old_seq = task_store._next_seq()        # 后台线程先拿到旧快照……
            new_seq = task_store._next_seq()
            task_store._write([{'id': 'new'}], new_seq)     # ……退出时的同步保存先写完
            task_store._write([{'id': 'old'}], old_seq)     # 旧快照这时才拿到文件锁
            with open(task_store.PATH, encoding='utf-8') as f:
                self.assertEqual(json.load(f), [{'id': 'new'}])
        finally:
            task_store.PATH = old_path


# ---------------------------------------------------------------- 多连接下载

def _urlopen(url, headers):
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=10)


class FastDownloadTest(unittest.TestCase):
    def test_estimate_is_not_used_as_exact_size(self):
        body = b'x' * 17

        def handle(h, n):                 # 不支持 Range，但给了准确的 Content-Length
            h.send_response(200)
            h.send_header('Content-Length', str(len(body)))
            h.end_headers()
            h.wfile.write(body)
        srv = _Server(handle)
        try:
            path = os.path.join(tempfile.mkdtemp(dir=_TMP), 'a.bin')
            fastdl.download([f'http://127.0.0.1:{srv.port}/a'], {}, path, _urlopen, total_hint=19)
            with open(path, 'rb') as f:
                self.assertEqual(f.read(), body)
        finally:
            srv.close()

    def test_wrong_range_is_rejected(self):
        data = os.urandom(3 * 1024 * 1024 + 123)
        bad = set()

        def handle(h, n):
            rng = h.headers.get('Range', '')
            a, b = rng.replace('bytes=', '').split('-')
            a, b = int(a), min(int(b), len(data) - 1)
            if a > 0 and a not in bad:    # 每段第一次请求：故意返回错位但等长的内容
                bad.add(a)
                a2, b2 = a - 1, b - 1
            else:
                a2, b2 = a, b
            h.send_response(206)
            h.send_header('Content-Range', f'bytes {a2}-{b2}/{len(data)}')
            h.send_header('Content-Length', str(b2 - a2 + 1))
            h.end_headers()
            h.wfile.write(data[a2:b2 + 1])
        srv = _Server(handle)
        try:
            path = os.path.join(tempfile.mkdtemp(dir=_TMP), 'b.bin')
            fastdl.download([f'http://127.0.0.1:{srv.port}/b'], {}, path, _urlopen, connections=3,
                            chunk_size=1024 * 1024)
            with open(path, 'rb') as f:
                self.assertEqual(hashlib.sha256(f.read()).hexdigest(), hashlib.sha256(data).hexdigest())
            self.assertTrue(bad)
        finally:
            srv.close()


if __name__ == '__main__':
    unittest.main()
