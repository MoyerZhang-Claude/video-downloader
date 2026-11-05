"""网盘部分：和后台的网盘下载引擎（pan-engine.exe，Go 编写，源码在 pan/）通信。

引擎负责百度网盘、夸克网盘、123云盘、Mega 的分享解析、扫码登录和多连接下载（原 PanDL 网盘下载器的核心）；
这里只把界面的操作转成请求，把引擎推来的任务变化、进度转成 Qt 信号。

数据目录：%APPDATA%\\VideoDownloader\\pan（任务列表、加密保存的网盘账号、网盘下载设置）。
第一次启动时，如果用过 PanDL，会把它保存的网盘账号复制过来，不用重新扫码。
"""
import itertools
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal

from .bilibili import DATA_DIR as USER_DIR
from .config import ROOT

log = logging.getLogger(__name__)

DATA_DIR = os.path.join(USER_DIR, 'pan')
OLD_PANDL_DIR = os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'), 'PanDL')

# 平台 ID → 名称（顺序就是界面里的顺序）
PROVIDERS = {'baidu': '百度网盘', 'quark': '夸克网盘', 'pan123': '123云盘', 'mega': 'Mega'}
LOGIN_PROVIDERS = ('baidu', 'quark', 'pan123')       # Mega 不需要登录
PROVIDER_BY_NAME = {v: k for k, v in PROVIDERS.items()}

# 网盘分享链接的网址（和 pan/internal/share/detect.go 一致）：这些链接交给网盘引擎，不交给视频解析
_PAN_URL_RE = re.compile(
    r'^https?://(?:(?:pan|yun)\.baidu\.com/(?:s/|share/|wap/)|pan\.quark\.cn/s/'
    r'|(?:www\.)?(?:123pan\.com|123pan\.cn|123684\.com|123865\.com|123912\.com|123592\.com)/s/'
    r'|\d+\.share\.123pan\.cn/|mega\.(?:nz|co\.nz|io)/)', re.I)
_PAN_TEXT_RE = re.compile(
    r'(?:pan|yun)\.baidu\.com/|pan\.quark\.cn/s/|123pan\.c(?:om|n)|123684\.com|123865\.com|123912\.com|123592\.com'
    r'|mega\.(?:nz|co\.nz|io)/', re.I)

# 任务状态（和引擎一致）
QUEUED, RUNNING, PAUSED, COMPLETED, FAILED = 'queued', 'running', 'paused', 'completed', 'failed'


def is_pan_url(url):
    return bool(_PAN_URL_RE.match(url or ''))


def has_pan_link(text):
    return bool(_PAN_TEXT_RE.search(text or ''))


def engine_path():
    """打包版在 _internal 里；源码运行时在 pan/bin（用 编译网盘引擎.bat 生成）。"""
    if getattr(sys, 'frozen', False):
        return os.path.join(getattr(sys, '_MEIPASS', os.path.dirname(sys.executable)), 'pan-engine.exe')
    return os.path.join(ROOT, 'pan', 'bin', 'pan-engine.exe')


def migrate_pandl_accounts():
    """用过 PanDL 网盘下载器时，把它保存的网盘账号复制过来（同一个 Windows 用户才能解密，换电脑无效）。"""
    src = os.path.join(OLD_PANDL_DIR, 'accounts.dat')
    dst = os.path.join(DATA_DIR, 'accounts.dat')
    if os.path.isfile(src) and not os.path.exists(dst):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            shutil.copy2(src, dst)
            log.info('已从 PanDL 复制网盘账号：%s', src)
        except OSError as e:
            log.warning('复制 PanDL 的网盘账号失败: %s', e)


def fmt_bytes(n):
    if n is None or n < 0:
        return ''
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return f'{n:.0f} {unit}' if unit == 'B' else f'{n:.1f} {unit}' if n < 100 else f'{n:.0f} {unit}'
        n /= 1024
    return ''


class Engine(QObject):
    """网盘下载引擎（后台进程）。所有回调都在界面线程里执行。"""
    ready = Signal(dict)             # 启动完成：{'version':…, 'settings':…}
    tasks_changed = Signal(list, list)   # 有变化的任务、被删除的任务 ID
    progress = Signal(dict)          # 每 0.5 秒：运行中任务的进度
    add_progress = Signal(dict)      # 添加大文件夹时读取目录的进度
    captcha = Signal()               # 有验证码等待输入
    failed = Signal(str)             # 引擎没能启动或意外退出
    _message = Signal(object)        # 读线程 → 界面线程

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc = None
        self.alive = False
        self.error = ''
        self.version = ''
        self.settings = {}
        self.tasks = {}              # 任务 ID → 任务信息（引擎的 TaskView + 实时进度）
        self.tasks_loaded = False    # 已经拿到完整的任务列表（之前收到的只是零星的变化）
        self.total_speed = 0
        self._ids = itertools.count(1)
        self._callbacks = {}
        self._waiters = {}
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._stopping = False
        self._restarts = 0
        self._download_dir = ''
        self._message.connect(self._dispatch)

    # ---------------------------------------------------------------- 启动 / 停止
    def start(self, download_dir):
        self._download_dir = download_dir
        exe = engine_path()
        if not os.path.isfile(exe):
            self._fail(f'找不到网盘解析组件：{exe}')
            return
        migrate_pandl_accounts()
        os.makedirs(DATA_DIR, exist_ok=True)
        try:
            self.proc = subprocess.Popen(
                [exe, '-data', DATA_DIR, '-download', download_dir],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except OSError as e:
            self._fail(f'网盘解析组件启动失败：{e}')
            return
        self._stopping = False
        threading.Thread(target=self._read_stdout, args=(self.proc,), daemon=True, name='pan-out').start()
        threading.Thread(target=self._read_stderr, args=(self.proc,), daemon=True, name='pan-log').start()

    def stop(self, timeout=8):
        """退出软件时调用：让引擎保存进度后退出。"""
        self._stopping = True
        p = self.proc
        if p is None or p.poll() is not None:
            return
        try:
            self.call_sync('shutdown', timeout=timeout)
        except Exception:  # noqa: BLE001
            pass
        try:
            p.stdin.close()
            p.wait(timeout=3)
        except Exception:  # noqa: BLE001
            p.kill()
        self.alive = False

    def _fail(self, msg):
        self.alive = False
        self.error = msg
        log.warning(msg)
        self.failed.emit(msg)

    # ---------------------------------------------------------------- 请求
    def call(self, method, params=None, callback=None):
        """发请求，返回请求编号（可用 cancel 取消）。callback(result, error) 在界面线程里调用。"""
        rid = next(self._ids)
        if callback is not None:
            self._callbacks[rid] = callback
        if not self._send({'id': rid, 'method': method, 'params': params or {}}):
            self._callbacks.pop(rid, None)
            if callback is not None:
                err = self.error or '网盘解析组件没有运行'
                QTimer.singleShot(0, lambda: callback(None, err))
        return rid

    def call_sync(self, method, params=None, timeout=5):
        """很快就有结果的请求（如识别链接）可以直接等结果。失败时抛出 RuntimeError。"""
        rid = next(self._ids)
        ev = threading.Event()
        box = {}
        with self._lock:
            self._waiters[rid] = (ev, box)
        try:
            if not self._send({'id': rid, 'method': method, 'params': params or {}}):
                raise RuntimeError(self.error or '网盘解析组件没有运行')
            if not ev.wait(timeout):
                raise RuntimeError('网盘解析组件没有响应')
        finally:
            with self._lock:
                self._waiters.pop(rid, None)
        if box.get('error'):
            raise RuntimeError(box['error'])
        return box.get('result')

    def cancel(self, rid):
        self._callbacks.pop(rid, None)
        self._send({'id': 0, 'method': 'cancel', 'params': {'request': rid}})

    def _send(self, msg):
        p = self.proc
        if p is None or p.poll() is not None:
            return False
        data = (json.dumps(msg, ensure_ascii=False) + '\n').encode('utf-8')
        try:
            with self._write_lock:
                p.stdin.write(data)
                p.stdin.flush()
            return True
        except (OSError, ValueError) as e:
            log.warning('发送给网盘解析组件失败: %s', e)
            return False

    # ---------------------------------------------------------------- 读取（后台线程）
    def _read_stdout(self, proc):
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            rid = msg.get('id')
            if rid:
                with self._lock:
                    waiter = self._waiters.get(rid)
                if waiter:                         # 同步请求：直接唤醒等待的线程
                    waiter[1].update(msg)
                    waiter[0].set()
                    continue
            self._message.emit(msg)
        self._message.emit({'event': '_exit', 'data': proc.poll()})

    def _read_stderr(self, proc):
        for line in proc.stderr:
            text = line.decode('utf-8', 'replace').rstrip()
            if text:
                log.info('[网盘] %s', text)

    # ---------------------------------------------------------------- 分发（界面线程）
    def _dispatch(self, msg):
        rid = msg.get('id')
        if rid:
            cb = self._callbacks.pop(rid, None)
            if cb is not None:
                try:
                    cb(msg.get('result'), msg.get('error') or '')
                except Exception:  # noqa: BLE001
                    log.exception('网盘请求的回调出错')
            return
        ev, data = msg.get('event'), msg.get('data')
        if ev == 'ready':
            self.alive, self.error = True, ''
            self.tasks_loaded = False
            self.version = data.get('version', '')
            self.settings = data.get('settings') or {}
            self._restarts = 0
            self.call('listTasks', callback=self._on_task_list)
            self.ready.emit(data)
        elif ev == 'tasks':
            updated = data.get('updated') or []
            removed = data.get('removed') or []
            for t in updated:
                old = self.tasks.get(t['id'])
                if old and t.get('status') == RUNNING and not t.get('speed') and old.get('speed'):
                    t['speed'] = old['speed']      # 事件里的速度可能还没算出来，先沿用上一次的
                self.tasks[t['id']] = t
            for tid in removed:
                self.tasks.pop(tid, None)
            self.tasks_changed.emit(updated, removed)
        elif ev == 'progress':
            self.total_speed = data.get('totalSpeed') or 0
            running = set()
            for it in data.get('items') or []:
                t = self.tasks.get(it['id'])
                if t is not None:
                    running.add(it['id'])
                    for k in ('downloaded', 'size', 'speed', 'eta', 'conns', 'phase'):
                        if k in it:
                            t[k] = it[k]
            for t in self.tasks.values():          # 不在进度里的任务速度清零
                if t['id'] not in running:
                    t['speed'] = 0
            self.progress.emit(data)
        elif ev == 'addProgress':
            self.add_progress.emit(data)
        elif ev == 'captcha':
            self.captcha.emit()
        elif ev == 'fatal':
            self._fail(str(data))
        elif ev == '_exit':
            self._on_exit(data)

    def _on_task_list(self, result, error):
        if error:
            return
        self.tasks = {t['id']: t for t in result or []}
        self.tasks_loaded = True
        self.tasks_changed.emit(list(self.tasks.values()), [])

    def _on_exit(self, code):
        was_alive = self.alive
        self.alive = False
        self.tasks_loaded = False
        for rid, cb in list(self._callbacks.items()):
            self._callbacks.pop(rid, None)
            try:
                cb(None, '网盘解析组件已退出')
            except Exception:  # noqa: BLE001
                pass
        if self._stopping:
            return
        if not self.error:
            self.error = f'网盘解析组件意外退出（{code}）'
        log.warning(self.error)
        # 意外退出：自动重启（连续失败 3 次就不再重启）
        if was_alive and self._restarts < 3:
            self._restarts += 1
            QTimer.singleShot(1500, lambda: self.start(self._download_dir))
        else:
            self.failed.emit(self.error)

    # ---------------------------------------------------------------- 便捷方法
    def detect(self, text):
        """识别文字里的网盘分享链接（含提取码）。引擎没运行时返回空列表。"""
        if not self.alive:
            return []
        try:
            res = self.call_sync('detect', {'text': text}, timeout=3) or {}
        except RuntimeError as e:
            log.warning('识别网盘链接失败: %s', e)
            return []
        return res.get('shares') or []


# ---------------------------------------------------------------- 一次添加的一批网盘文件

class Batch:
    """从一个分享里添加的一批文件（下载页里显示成一张卡片，全部下完后记入下载历史）。"""

    def __init__(self, row):
        self.id = row['id']
        self.provider = row['provider']
        self.share_id = row['share_id']
        self.title = row['title']
        self.url = row['url']
        self.save_dir = row['save_dir']
        self.task_ids = list(row['task_ids'])
        self.ts = row['ts']
        self.state = row['state']
        self.record_id = row['record_id']
        self.errors = []

    @property
    def provider_name(self):
        return PROVIDERS.get(self.provider, self.provider)

    def tasks(self, engine):
        return [engine.tasks[i] for i in self.task_ids if i in engine.tasks]

    def stats(self, engine):
        ts = self.tasks(engine)
        st = {'total': len(ts), 'done': 0, 'failed': 0, 'running': 0, 'queued': 0, 'paused': 0,
              'size': 0, 'downloaded': 0, 'speed': 0, 'unknown_size': False, 'verified': 0}
        for t in ts:
            s = t.get('status')
            st[{COMPLETED: 'done', FAILED: 'failed', RUNNING: 'running', QUEUED: 'queued', PAUSED: 'paused'}
               .get(s, 'queued')] += 1
            size = t.get('size') or 0
            if size < 0:
                st['unknown_size'] = True
            else:
                st['size'] += size
            st['downloaded'] += size if s == COMPLETED and size > 0 else max(0, t.get('downloaded') or 0)
            if s == RUNNING:
                st['speed'] += t.get('speed') or 0
            if t.get('verified'):
                st['verified'] += 1
        st['active'] = st['running'] + st['queued']
        st['progress'] = st['downloaded'] / st['size'] if st['size'] > 0 else (1.0 if ts and st['done'] == len(ts) else 0)
        left = st['size'] - st['downloaded']
        st['eta'] = int(left / st['speed']) if st['speed'] > 0 and left > 0 else 0
        return st

    def done_paths(self, engine):
        return [t['filePath'] for t in self.tasks(engine) if t.get('status') == COMPLETED and t.get('filePath')]

    def location(self, engine):
        """这批文件在电脑上的位置：只有一个文件时是文件本身，否则是它们共同所在的文件夹。"""
        paths = self.done_paths(engine)
        if len(paths) == 1:
            return paths[0]
        if paths:
            try:
                common = os.path.commonpath(paths)
                if os.path.isdir(common):
                    return common
            except ValueError:
                pass
        return self.save_dir


def human_eta(sec):
    if not sec or sec <= 0:
        return ''
    sec = int(sec)
    if sec >= 3600:
        return f'{sec // 3600} 小时 {sec % 3600 // 60} 分'
    if sec >= 60:
        return f'{sec // 60} 分 {sec % 60} 秒'
    return f'{sec} 秒'


def now():
    return time.time()
