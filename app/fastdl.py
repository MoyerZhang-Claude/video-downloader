"""通用加速下载：多连接分段下载 + 测速看门狗。

- 服务器支持断点续传（Range）时，把文件切成小块，用几个连接同时下载；
  每块都是一次新请求，也顺便绕开「单个连接越下越慢」的限速。
- 某个连接持续太慢（看门狗）：丢掉这条连接，从断点重新请求，并换到下一个候选地址。
- 有多个候选地址（如 B站 的官方 CDN 节点）时，先花一两秒测速，挑最快的用。
- 服务器不支持 Range 时退回普通单连接下载。

网络请求统一走传入的 urlopen(url, headers) —— 安卓/电脑版都传 yt-dlp 的 ydl.urlopen，
这样代理、Cookie、证书等设置和解析时完全一致。
"""
import collections
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait

KB = 1024
MB = 1024 * KB


class Cancelled(Exception):
    pass


class DownloadFailed(Exception):
    pass


def _status(resp):
    return getattr(resp, 'status', None) or getattr(resp, 'code', None) or 200


def _header(resp, name):
    h = getattr(resp, 'headers', None)
    return (h.get(name) if h is not None else None) or ''


def _http_code(err):
    for attr in ('status', 'code'):
        v = getattr(err, attr, None)
        if isinstance(v, int):
            return v
    resp = getattr(err, 'response', None)
    return getattr(resp, 'status', None) if resp is not None else None


# ---------------------------------------------------------------- 候选地址测速

def rank_urls(urls, headers, urlopen, probe_bytes=MB, timeout=4.0, is_cancelled=None):
    """并行请求每个地址的前 probe_bytes 字节，按实测速度从快到慢排序（失败的排最后）。"""
    urls = list(dict.fromkeys(u for u in urls if u))
    if len(urls) <= 1:
        return urls

    def probe(url):
        h = dict(headers or {})
        h['Range'] = f'bytes=0-{probe_bytes - 1}'
        t0 = time.monotonic()
        got = 0
        try:
            resp = urlopen(url, h)
            try:
                while got < probe_bytes and time.monotonic() - t0 < timeout:
                    if is_cancelled and is_cancelled():
                        break
                    block = resp.read(64 * KB)
                    if not block:
                        break
                    got += len(block)
            finally:
                resp.close()
        except Exception:  # noqa: BLE001 - 测速失败的地址直接排到最后
            return -1.0
        return got / max(time.monotonic() - t0, 1e-3)

    # 总时限：连不上的节点会一直卡到网络超时（20 秒），不等它，直接当作最慢
    pool = ThreadPoolExecutor(max_workers=min(6, len(urls)))
    futures = [pool.submit(probe, u) for u in urls]
    wait(futures, timeout=timeout + 1.5)
    pool.shutdown(wait=False, cancel_futures=True)
    speeds = [f.result() if f.done() and not f.cancelled() else -1.0 for f in futures]
    order = sorted(range(len(urls)), key=lambda i: -speeds[i])
    return [urls[i] for i in order]


# ---------------------------------------------------------------- 分段下载

class FastDownloader:
    def __init__(self, urls, headers, path, urlopen, on_progress=None, is_cancelled=None,
                 connections=4, chunk_size=4 * MB, slow_bps=150 * KB, slow_window=8.0, total_hint=0):
        self.urls = [u for u in urls if u]
        self.headers = {k: v for k, v in (headers or {}).items() if k.lower() not in ('range', 'cookie')}
        self.path = path
        self.urlopen = urlopen
        self.on_progress = on_progress
        self.is_cancelled = is_cancelled or (lambda: False)
        self.connections = max(1, connections)
        self.chunk_size = chunk_size
        self.slow_bps = slow_bps
        self.slow_window = slow_window
        self.total = int(total_hint or 0)
        self.done = 0
        self.switches = 0               # 看门狗换线次数（日志用）
        self._lock = threading.Lock()
        self._samples = collections.deque()   # (时间, 累计字节) 用来算速度
        self._last_report = 0.0
        self._error = None

    # ---------------------------------------------------------- 公共
    def run(self):
        if not self.urls:
            raise DownloadFailed('没有可用的下载地址')
        info = self._probe()
        if info and info['ranges'] and info['total'] > 0:
            self.total = info['total']
            self._ranged(info['url'])
        else:
            self._single()
        self._report(force=True)
        return self.path

    # ---------------------------------------------------------- 探测
    def _probe(self):
        """用 Range: bytes=0-0 问服务器：文件多大、能不能断点续传；顺便拿到跳转后的真实地址。"""
        for i, url in enumerate(self.urls):
            h = dict(self.headers, Range='bytes=0-0')
            try:
                resp = self.urlopen(url, h)
            except Exception as e:  # noqa: BLE001
                self._error = e
                continue
            try:
                code = _status(resp)
                cr = _header(resp, 'Content-Range')
                m = re.search(r'/(\d+)', cr)
                final = getattr(resp, 'url', None) or url
                if code == 206 and m:
                    # 能用的地址放最前面；有跳转（如抖音播放接口 → CDN）时直接用跳转后的地址，省掉每块一次跳转
                    self.urls.pop(i)
                    self.urls[:0] = [final] if final == url else [final, url]
                    return {'ranges': True, 'total': int(m.group(1)), 'url': final}
                return {'ranges': False, 'total': int(_header(resp, 'Content-Length') or 0), 'url': final}
            finally:
                resp.close()
        if self._error:
            raise self._error
        return None

    # ---------------------------------------------------------- 进度
    def _add(self, n):
        with self._lock:
            self.done += n
        self._report()

    def _report(self, force=False):
        now = time.monotonic()
        with self._lock:
            self._samples.append((now, self.done))
            while len(self._samples) > 2 and now - self._samples[0][0] > 3.0:
                self._samples.popleft()
            if not force and now - self._last_report < 0.3:
                return
            self._last_report = now
            t0, d0 = self._samples[0]
            speed = (self.done - d0) / (now - t0) if now - t0 > 0.2 else 0
            done, total = self.done, self.total
        if self.on_progress:
            self.on_progress(done, total, int(speed))

    def _check_cancel(self):
        if self.is_cancelled():
            raise Cancelled()

    # ---------------------------------------------------------- 不支持 Range：单连接
    def _single(self):
        last_err = None
        for url in self.urls:
            self.done = 0
            try:
                resp = self.urlopen(url, dict(self.headers))
                try:
                    if not self.total:
                        self.total = int(_header(resp, 'Content-Length') or 0)
                    with open(self.path, 'wb') as f:
                        while True:
                            self._check_cancel()
                            block = resp.read(256 * KB)
                            if not block:
                                break
                            f.write(block)
                            self._add(len(block))
                finally:
                    resp.close()
                if self.total and self.done < self.total:
                    raise DownloadFailed(f'下载不完整（{self.done}/{self.total}）')
                return
            except Cancelled:
                raise
            except Exception as e:  # noqa: BLE001
                last_err = e
        raise last_err or DownloadFailed('下载失败')

    # ---------------------------------------------------------- 支持 Range：多连接分段
    def _ranged(self, first_url):
        total = self.total
        # 小文件不用拆太细：保证至少能分给每个连接两块
        size = max(1 * MB, min(self.chunk_size, total // (self.connections * 2) or total))
        self._pending = collections.deque([s, min(s + size, total) - 1] for s in range(0, total, size))
        self._in_flight = 0          # 正在下载、还没结束的块数
        self._alive = 0              # 还在工作的连接数
        self._fatal = None
        with open(self.path, 'wb') as f:
            f.truncate(total)
        n = min(self.connections, len(self._pending))
        self._alive = n
        threads = [threading.Thread(target=self._worker, args=(i,), daemon=True) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if self._fatal:
            raise self._fatal
        if self.done < total or self._pending:
            raise DownloadFailed(f'下载不完整（{self.done}/{total}）')

    def _take(self):
        """取下一块。队列空了但还有别的连接在下载：等一等（它们失败时会把剩余部分放回来）。"""
        while True:
            with self._lock:
                if self._fatal:
                    return None
                if self._pending:
                    self._in_flight += 1
                    return self._pending.popleft()
                if self._in_flight == 0:
                    return None
            if self.is_cancelled():
                return None
            time.sleep(0.1)

    def _finish(self, start=None, end=None):
        """这一块结束；start..end 是没下完的部分，放回队列最前面。"""
        with self._lock:
            if start is not None and start <= end:
                self._pending.appendleft([start, end])
            self._in_flight -= 1

    def _worker(self, n):
        url_i = n      # 各连接从不同的候选地址开始（只有一个地址时都一样）
        window = self.slow_window
        failures = 0
        f = open(self.path, 'r+b')
        try:
            while True:
                if self.is_cancelled():
                    self._fatal = self._fatal or Cancelled()
                    return
                piece = self._take()
                if piece is None:
                    return
                start, end = piece
                url = self.urls[url_i % len(self.urls)]
                pos, slow, err = start, False, None
                try:
                    resp = self.urlopen(url, dict(self.headers, Range=f'bytes={start}-{end}'))
                    try:
                        if _status(resp) != 206:
                            raise DownloadFailed(f'服务器没有按分段返回（HTTP {_status(resp)}）')
                        f.seek(start)
                        w_t, w_bytes = time.monotonic(), 0
                        bs = 64 * KB
                        while pos <= end and not self._fatal and not self.is_cancelled():
                            t_read = time.monotonic()
                            block = resp.read(min(bs, end - pos + 1))
                            if not block:
                                break
                            # 读取块大小自适应：慢时用小块（看门狗能及时发现），快时用大块（省 CPU）
                            dt = time.monotonic() - t_read
                            if dt > 0.4:
                                bs = 16 * KB
                            elif dt < 0.05 and bs < 512 * KB:
                                bs *= 2
                            f.write(block)
                            pos += len(block)
                            w_bytes += len(block)
                            self._add(len(block))
                            # 看门狗：这条连接连续 window 秒都低于 slow_bps → 断开，从断点换线路重连
                            el = time.monotonic() - w_t
                            if el >= window:
                                if w_bytes / el < self.slow_bps and pos <= end:
                                    slow = True
                                    break
                                w_t, w_bytes = time.monotonic(), 0
                    finally:
                        resp.close()
                except Exception as e:  # noqa: BLE001
                    err = e
                self._finish(pos, end)
                if self._fatal or self.is_cancelled():
                    continue
                if slow:
                    url_i += 1
                    self.switches += 1
                    window = min(window * 2, 60.0)      # 换了还慢：放宽判定，避免在慢网下反复重连
                    continue
                if err is None and pos > end:           # 这块顺利下完
                    failures = 0
                    window = self.slow_window
                    continue
                # 出错或服务器提前断开：换个地址重试；太多次失败就让这个连接退出
                failures += 1
                url_i += 1
                code = _http_code(err) if err else None
                with self._lock:
                    others = self._alive - 1
                    give_up = (code in (403, 429, 503) and failures >= 2) or failures >= 8
                    if give_up:
                        if others > 0:          # 服务器不喜欢太多连接：减少连接数，交给其余连接
                            self._alive -= 1
                            return
                        if failures >= 8:
                            self._fatal = err or DownloadFailed('连接反复中断')
                            return
                time.sleep(min(0.5 * failures, 3))
        finally:
            f.close()


def download(urls, headers, path, urlopen, **kw):
    return FastDownloader(urls, headers, path, urlopen, **kw).run()
