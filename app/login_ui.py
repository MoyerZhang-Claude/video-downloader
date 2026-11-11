"""网站登录的界面：用普通的浏览器窗口打开网站的登录页，用户登录好后关掉窗口（或者点「完成」），
软件读出 Cookie 加密保存。浏览器相关的操作都在后台线程里做，界面不等它。"""
import threading

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QLabel

from . import browser, site_login, ui


class _Signals(QObject):
    opened = Signal(str)            # 浏览器打开了（参数是错误信息，空 = 成功）
    polled = Signal(bool)           # 登录窗口还开着吗
    saved = Signal(int, bool, str)  # (保存了多少条 Cookie, 有没有「已登录」标志, 错误信息)


class LoginDialog(ui.Dialog):
    """exec() 返回 'ok'（已保存登录信息）/ 'none'（没有检测到登录）/ None（取消）。

    浏览器用最普通的方式打开（不带调试端口），网站看到的就是一个正常的 Edge / Chrome，谷歌、X、TikTok 都能登录。
    这样打开的浏览器开着时读不了它的 Cookie，所以：用户登录好后关掉浏览器窗口（或者点「完成」，由软件替他正常关闭），
    软件再在后台无界面地读出 Cookie、加密保存。"""
    scrim_closes = False        # 登录到一半，不小心点到对话框外面不要取消

    def __init__(self, host, site):
        name = browser.find_browser()[1] or '浏览器'
        super().__init__(host, f'登录 {site.name}', width=500)
        self.site = site
        self.set_text(f'已经在 {name} 窗口里打开 {site.name} 的登录页。\n'
                      f'在那个窗口里正常登录。登录好以后，关掉那个 {name} 窗口，或者回到这里点「完成」，'
                      '软件会读取登录信息并加密保存。')
        self.status = QLabel('正在打开浏览器…')
        self.status.setObjectName('dialogSub')
        self.status.setWordWrap(True)
        self.body.addSpacing(6)
        self.body.addWidget(self.status)
        note = QLabel(f'这个 {name} 窗口用的是本软件自己的配置，和你平时用的浏览器互不影响。登录信息加密保存在本机。')
        note.setObjectName('dialogSub')
        note.setWordWrap(True)
        self.body.addWidget(note)
        self.cancel_btn = self.add_button('取消')
        self.done_btn = self.add_button('完成', 'done', 'primary')
        self.done_btn.clicked.disconnect()
        self.done_btn.clicked.connect(self._save)
        self.cancel_btn.clicked.disconnect()
        self.cancel_btn.clicked.connect(self._cancel)
        self.done_btn.setEnabled(False)
        self.sig = _Signals()
        self.sig.opened.connect(self._on_opened)
        self.sig.polled.connect(self._on_polled)
        self.sig.saved.connect(self._on_saved)
        self._busy = False
        self._saving = False
        self._ended = False
        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self._poll)
        self.closed.connect(self._on_closed)
        threading.Thread(target=self._open, daemon=True).start()

    # 后台线程 ----------------------------------------------------------
    def _open(self):
        try:
            browser.open_plain(self.site.url)
            self.sig.opened.emit('')
        except Exception as e:  # noqa: BLE001
            self.sig.opened.emit(str(e) or type(e).__name__)

    def _poll_work(self):
        self.sig.polled.emit(browser.plain_open())

    def _save_work(self):
        try:
            browser.close_plain()                   # 正常关闭：浏览器把 Cookie 写回磁盘
            cookies = browser.read_cookies()        # 后台无界面地启动一下读出来，读完立即退出
            logged = site_login.has_login(self.site, cookies) if self.site.names else True
            n = site_login.save(self.site, cookies) if logged else 0
            self.sig.saved.emit(n, logged, '')
        except Exception as e:  # noqa: BLE001
            self.sig.saved.emit(0, False, str(e) or type(e).__name__)

    # 界面线程 ----------------------------------------------------------
    def _on_opened(self, error):
        if self._ended:
            return
        if error:
            self.status.setText(f'浏览器没有打开：{error}')
            return
        self.status.setText('等待登录…（登录好以后关掉浏览器窗口，或者点「完成」）')
        self.done_btn.setEnabled(True)
        QTimer.singleShot(4000, self._timer.start)  # 浏览器刚启动时进程还在交接，等一下再开始看它在不在

    def _poll(self):
        if self._busy or self._saving or self._ended:
            return
        self._busy = True
        threading.Thread(target=self._poll_work, daemon=True).start()

    def _on_polled(self, alive):
        self._busy = False
        if self._saving or self._ended:
            return
        if not alive:                               # 用户把浏览器关了：读一下登录上了没有
            self._save()

    def _save(self):
        if self._saving or self._ended:
            return
        self._saving = True
        self._timer.stop()
        self.done_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.status.setText('正在关闭浏览器、读取登录信息…')
        threading.Thread(target=self._save_work, daemon=True).start()

    def _on_saved(self, n, logged, error):
        if self._ended:
            return
        if error:
            self._saving = False
            self.cancel_btn.setEnabled(True)
            self.status.setText(f'读取登录信息失败：{error}')
            return
        self.finish('ok' if n and logged else 'none')

    def _cancel(self):
        self.finish(None)

    def _on_closed(self):
        """对话框关了：取消 / Esc 时浏览器窗口还开着的话关掉（完成时已经关了）。"""
        self._ended = True
        self._timer.stop()
        if not self._saving:
            threading.Thread(target=browser.close_plain, daemon=True).start()


def login(win, site):
    """打开浏览器窗口登录 site。返回是否保存了登录信息。"""
    if not browser.available():
        ui.alert(win, '没有找到 Edge 或 Chrome',
                 '网站登录要用到电脑上的 Edge 或 Chrome 浏览器，这台电脑上没有找到。\n'
                 '可以改用 cookies.txt：在平时用的浏览器里装扩展「Get cookies.txt LOCALLY」，登录网站后导出，'
                 '放进 设置 → 网络与登录 → 高级 里的 cookies 文件夹。')
        return False
    res = LoginDialog(win.central, site).exec()
    if res == 'ok':
        win.notify(f'已登录 {site.name}，登录信息已加密保存', 'ok')
        return True
    if res == 'none':
        ui.alert(win, f'没有检测到 {site.name} 的登录',
                 '浏览器里没有这个网站的登录信息。请重新点「登录」，在打开的浏览器窗口里完成登录，'
                 '然后关掉那个窗口（或者点「完成」）。')
    return False


def logout(win, site, done=None):
    if not ui.confirm(win, f'退出 {site.name}', f'删除本机保存的 {site.name} 登录信息？软件用的浏览器配置里也会退出这个网站。',
                      ok='退出登录', danger=True):
        return

    def work():
        try:
            site_login.logout(site)
        finally:
            if done:
                win.bridge.ui_call.emit(done)
    threading.Thread(target=work, daemon=True).start()
