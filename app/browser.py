"""内置浏览器：独立配置的 Edge（没有就用 Chrome）+ Chrome DevTools 协议（CDP）。

不内嵌浏览器内核（QtWebEngine 会让安装包大 100 MB 以上），而是用电脑上已有的 Edge / Chrome，
给它一个只属于本软件的配置目录（%APPDATA%\\VideoDownloader\\browser），通过 CDP 和它说话：
- 网站登录：在这个浏览器窗口里登录网站，再让浏览器把它自己解密好的 Cookie 交出来
  （新版 Chrome / Edge 的 Cookie 加了密，从外面读不出来；这样就绕开了）。
- 网页资源嗅探：页面真正播放视频时监听网络请求（见 sniffer.py）。

安全约束（调试端口能完全控制浏览器、读出所有 Cookie）：
1. 只用上面那个独立的配置目录，绝不在用户自己的浏览器配置上打开调试端口。
2. 端口用 0（系统随机分配），只监听 127.0.0.1；端口号从配置目录里的 DevToolsActivePort 文件读。
3. 连接时不带 Origin、不经过任何代理。
4. 用完立即关闭浏览器；浏览器进程在软件的 Job 里（proc_job.py），软件怎么退出它都会被结束。
"""
import json
import os
import subprocess
import threading
import time

from .config import DATA

PROFILE_DIR = os.path.join(DATA, 'browser')
_PORT_FILE = os.path.join(PROFILE_DIR, 'DevToolsActivePort')


class BrowserError(RuntimeError):
    pass


# ---------------------------------------------------------------- 找浏览器
def _app_path(exe):
    """注册表 App Paths 里登记的安装位置"""
    try:
        import winreg
    except ImportError:
        return None
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, rf'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}') as k:
                path = winreg.QueryValue(k, None)
            if path and os.path.isfile(path.strip('"')):
                return path.strip('"')
        except OSError:
            continue
    return None


def find_browser():
    """电脑上的 Edge（优先）或 Chrome：返回 (路径, 名字)；都没有返回 (None, '')。"""
    env = os.environ.get
    roots = [env('ProgramFiles(x86)'), env('ProgramFiles'), env('LOCALAPPDATA')]
    cands = [('msedge.exe', 'Edge', r'Microsoft\Edge\Application\msedge.exe'),
             ('chrome.exe', 'Chrome', r'Google\Chrome\Application\chrome.exe')]
    for exe, name, rel in cands:
        path = _app_path(exe)
        if path:
            return path, name
        for root in roots:
            if root and os.path.isfile(os.path.join(root, rel)):
                return os.path.join(root, rel), name
    return None, ''


def available():
    return os.name == 'nt' and bool(find_browser()[0])


# ---------------------------------------------------------------- CDP 连接
class Cdp:
    """一条 CDP 连接（WebSocket 上的 JSON）：call() 发命令等结果，on() 订阅事件。事件回调在读线程里调用。"""

    def __init__(self, ws_url):
        from websockets.sync.client import connect
        kw = {'max_size': None, 'open_timeout': 10, 'ping_interval': None, 'compression': None}
        try:
            self.ws = connect(ws_url, proxy=None, **kw)        # 本机连接：不走系统代理
        except TypeError:                                       # 旧版 websockets 没有 proxy 参数
            self.ws = connect(ws_url, **kw)
        self._next = 0
        self._lock = threading.Lock()
        self._waiters = {}          # 命令编号 -> [事件, 结果]
        self._handlers = {}         # 事件名 -> [回调(params, session_id)]
        self.closed = False
        threading.Thread(target=self._read, daemon=True, name='cdp-read').start()

    def _read(self):
        try:
            for raw in self.ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if 'id' in msg:
                    w = self._waiters.get(msg['id'])
                    if w:
                        w[1] = msg
                        w[0].set()
                else:
                    for fn in list(self._handlers.get(msg.get('method'), ())):
                        try:
                            fn(msg.get('params') or {}, msg.get('sessionId'))
                        except Exception:  # noqa: BLE001 - 回调出错不能弄断读线程
                            pass
        except Exception:  # noqa: BLE001 - 浏览器关了
            pass
        self.closed = True
        for w in list(self._waiters.values()):
            w[0].set()

    def call(self, method, params=None, session=None, timeout=15):
        if self.closed:
            raise BrowserError('浏览器已经关闭')
        with self._lock:
            self._next += 1
            cid = self._next
            waiter = [threading.Event(), None]
            self._waiters[cid] = waiter
            msg = {'id': cid, 'method': method, 'params': params or {}}
            if session:
                msg['sessionId'] = session
            try:
                self.ws.send(json.dumps(msg))
            except Exception as e:  # noqa: BLE001
                self._waiters.pop(cid, None)
                raise BrowserError(f'浏览器已经关闭（{e}）') from None
        ok = waiter[0].wait(timeout)
        self._waiters.pop(cid, None)
        res = waiter[1]
        if not ok or res is None:
            raise BrowserError('浏览器已经关闭' if self.closed else f'浏览器没有响应（{method}）')
        if 'error' in res:
            raise BrowserError(f'{method}: {res["error"].get("message")}')
        return res.get('result') or {}

    def on(self, method, fn):
        self._handlers.setdefault(method, []).append(fn)

    def off(self, method, fn):
        try:
            self._handlers.get(method, []).remove(fn)
        except ValueError:
            pass

    def close(self):
        self.closed = True
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- 浏览器进程
class Browser:
    """一个用独立配置目录启动的浏览器进程 + 它的 CDP 连接。"""

    def __init__(self, headless=False, url='about:blank'):
        exe, self.name = find_browser()
        if not exe:
            raise BrowserError('没有找到 Edge 或 Chrome 浏览器')
        os.makedirs(PROFILE_DIR, exist_ok=True)
        try:
            os.remove(_PORT_FILE)           # 上次留下的端口号已经没用了
        except OSError:
            pass
        self.headless = headless
        args = [exe, f'--user-data-dir={PROFILE_DIR}',
                '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1',     # 随机端口，只听本机
                '--no-first-run', '--no-default-browser-check', '--disable-background-mode']
        if headless:
            args += ['--headless=new', '--disable-gpu', '--mute-audio']
        args.append(url)
        # 这个进程在软件的 Job 里：软件退出（包括崩溃）时它跟着结束，不会留下开着调试端口的浏览器
        self.proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            self.cdp = Cdp(self._wait_ws())
        except Exception:
            self.kill()
            raise

    def _wait_ws(self, timeout=20):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            try:
                with open(_PORT_FILE, encoding='utf-8') as f:
                    lines = f.read().split('\n')
                if len(lines) >= 2 and lines[0].strip().isdigit() and lines[1].strip():
                    return f'ws://127.0.0.1:{int(lines[0])}{lines[1].strip()}'
            except OSError:
                pass
            if self.proc.poll() is not None and time.monotonic() - t0 > 3:
                # 进程马上退出了：多半是这个配置目录已经有一个浏览器开着（它把请求转交过去就退出）
                raise BrowserError('内置浏览器没有启动成功（可能上一个还没完全关闭，请稍后再试）')
            time.sleep(0.1)
        raise BrowserError('内置浏览器启动超时')

    @property
    def alive(self):
        return not self.cdp.closed

    def cookies(self):
        """浏览器里的全部 Cookie（已经由浏览器自己解密）：[{name, value, domain, path, expires, secure, httpOnly…}]"""
        return self.cdp.call('Storage.getCookies').get('cookies') or []

    def open_tab(self, url):
        """打开一个新标签页并切到前面，返回 targetId。"""
        tid = self.cdp.call('Target.createTarget', {'url': url}).get('targetId')
        try:
            self.cdp.call('Target.activateTarget', {'targetId': tid})
        except BrowserError:
            pass
        return tid

    def pages(self):
        infos = self.cdp.call('Target.getTargets').get('targetInfos') or []
        return [t for t in infos if t.get('type') == 'page']

    def close(self, wait=4):
        """正常关闭（让浏览器把 Cookie 写回磁盘）；关不掉就强制结束。"""
        try:
            if not self.cdp.closed:
                self.cdp.call('Browser.close', timeout=3)
        except BrowserError:
            pass
        try:
            self.proc.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            pass
        self.kill()

    def kill(self):
        try:
            self.cdp.close()
        except AttributeError:
            pass
        if self.proc.poll() is None:
            # 浏览器是多进程的：连它启动的子进程一起结束
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(self.proc.pid)], capture_output=True,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            os.remove(_PORT_FILE)
        except OSError:
            pass


# ---------------------------------------------------------------- 全局只有一个（同一个配置目录不能同时开两个浏览器）
_lock = threading.RLock()
_window = None          # 用户看得见的那个浏览器窗口（登录 / 嗅探时开着）


def window(url='about:blank'):
    """打开（或者复用已经开着的）浏览器窗口，在新标签页里打开 url。返回 (Browser, targetId)。"""
    global _window
    with _lock:
        if _window is not None and _window.alive:
            return _window, _window.open_tab(url)
        if _window is not None:
            _window.kill()
        _window = Browser(headless=False, url=url)
        pages = _window.pages()
        return _window, (pages[0]['targetId'] if pages else None)


def current_window():
    with _lock:
        return _window if _window is not None and _window.alive else None


def close_window():
    """用完关闭浏览器窗口。"""
    global _window
    with _lock:
        b, _window = _window, None
    if b is not None:
        b.close()


def read_cookies():
    """读出内置浏览器里的全部 Cookie：窗口正开着就直接问它；没开就在后台无界面地启动一下，读完立即退出。"""
    with _lock:
        b = current_window()
        if b is not None:
            return b.cookies()
        if not os.path.isdir(os.path.join(PROFILE_DIR, 'Default')):
            return []           # 从来没用内置浏览器登录过
        for attempt in (1, 2):
            try:
                b = Browser(headless=True)
                break
            except BrowserError:
                if attempt == 2:
                    raise
                time.sleep(1.5)         # 窗口刚关、配置目录的锁还没放开：等一下再试
        try:
            return b.cookies()
        finally:
            b.close(wait=3)


def shutdown():
    """软件退出时调用。"""
    global _window
    with _lock:
        b, _window = _window, None
    if b is not None:
        try:
            b.close(wait=2)
        except Exception:  # noqa: BLE001
            b.kill()
