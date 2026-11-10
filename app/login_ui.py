"""网站登录的界面：在内置浏览器里打开网站的登录页，软件这边等着检测到登录（或者用户点「完成」），
然后把 Cookie 加密保存、关掉浏览器。浏览器相关的操作都在后台线程里做，界面不等它。"""
import threading

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QLabel

from . import browser, site_login, ui


class _Signals(QObject):
    opened = Signal(str)            # 浏览器打开了（参数是错误信息，空 = 成功）
    polled = Signal(bool, bool)     # (检测到登录, 浏览器还开着)
    saved = Signal(int, str)        # (保存了多少条 Cookie, 错误信息)


class LoginDialog(ui.Dialog):
    """exec() 返回 'ok'（已保存登录信息）/ 'none'（浏览器里没有这个网站的 Cookie）/ None（取消）。"""
    scrim_closes = False        # 登录到一半，不小心点到对话框外面不要取消

    def __init__(self, host, site):
        super().__init__(host, f'登录 {site.name}', width=480)
        self.site = site
        self.set_text(f'已经在内置浏览器（{browser.find_browser()[1]}）里打开 {site.name}。\n'
                      '在那个窗口里登录，登录好以后软件会自动检测到；检测不到的话，回到这里点「完成」。')
        self.status = QLabel('正在打开内置浏览器…')
        self.status.setObjectName('dialogSub')
        self.status.setWordWrap(True)
        self.body.addSpacing(6)
        self.body.addWidget(self.status)
        note = QLabel('内置浏览器用的是本软件自己的配置，和你平时用的浏览器互不影响。登录信息加密保存在本机。')
        note.setObjectName('dialogSub')
        note.setWordWrap(True)
        self.body.addWidget(note)
        self.cancel_btn = self.add_button('取消')
        self.done_btn = self.add_button('完成', 'done', 'primary')
        self.done_btn.clicked.disconnect()
        self.done_btn.clicked.connect(lambda: self._save(manual=True))
        self.cancel_btn.clicked.disconnect()
        self.cancel_btn.clicked.connect(self._cancel)
        self.done_btn.setEnabled(False)
        self.sig = _Signals()
        self.sig.opened.connect(self._on_opened)
        self.sig.polled.connect(self._on_polled)
        self.sig.saved.connect(self._on_saved)
        self._busy = False          # 正在问浏览器（上一次还没回来就不再问）
        self._saving = False
        self._ended = False
        self._timer = QTimer(self)
        self._timer.setInterval(1500)
        self._timer.timeout.connect(self._poll)
        self.closed.connect(self._on_closed)
        threading.Thread(target=self._open, daemon=True).start()

    # 后台线程 ----------------------------------------------------------
    def _open(self):
        try:
            browser.window(self.site.url)
            self.sig.opened.emit('')
        except Exception as e:  # noqa: BLE001
            self.sig.opened.emit(str(e) or type(e).__name__)

    def _poll_work(self):
        try:
            b = browser.current_window()
            if b is None:
                self.sig.polled.emit(False, False)
            else:
                self.sig.polled.emit(site_login.has_login(self.site, b.cookies()), True)
        except Exception:  # noqa: BLE001 - 刚好在关：下一轮再看
            self.sig.polled.emit(False, browser.current_window() is not None)

    def _save_work(self):
        try:
            cookies = browser.read_cookies()        # 窗口开着就问它；用户自己关掉了就后台无界面读一次
            n = site_login.save(self.site, cookies)
            browser.close_window()                  # 用完立即关闭浏览器
            self.sig.saved.emit(n, '')
        except Exception as e:  # noqa: BLE001
            browser.close_window()
            self.sig.saved.emit(0, str(e) or type(e).__name__)

    # 界面线程 ----------------------------------------------------------
    def _on_opened(self, error):
        if self._ended:
            return
        if error:
            self.status.setText(f'内置浏览器没有打开：{error}')
            return
        self.status.setText('等待登录…（登录好以后会自动检测到）')
        self.done_btn.setEnabled(True)
        self._timer.start()

    def _poll(self):
        if self._busy or self._saving or self._ended:
            return
        self._busy = True
        threading.Thread(target=self._poll_work, daemon=True).start()

    def _on_polled(self, logged, alive):
        self._busy = False
        if self._saving or self._ended:
            return
        if logged:
            self.status.setText('已检测到登录，正在保存登录信息…')
            self._timer.stop()
            self.done_btn.setEnabled(False)
            QTimer.singleShot(2000, self._save)     # 等登录后的跳转走完（YouTube 登录完还要跳回 youtube.com）
        elif not alive:
            self._timer.stop()
            self._save()                            # 用户把浏览器关了：看看刚才登录上了没有

    def _save(self, manual=False):
        if self._saving or self._ended:
            return
        self._saving = True
        self._timer.stop()
        self.done_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.status.setText('正在保存登录信息…')
        threading.Thread(target=self._save_work, daemon=True).start()

    def _on_saved(self, n, error):
        if self._ended:
            return
        if error:
            self._saving = False
            self.cancel_btn.setEnabled(True)
            self.status.setText(f'保存登录信息失败：{error}')
            return
        self.finish('ok' if n else 'none')

    def _cancel(self):
        self.finish(None)

    def _on_closed(self):
        """对话框关了（完成 / 取消 / Esc）：浏览器还开着的话关掉。"""
        self._ended = True
        self._timer.stop()
        if browser.current_window() is not None:
            threading.Thread(target=browser.close_window, daemon=True).start()


def login(win, site):
    """打开内置浏览器登录 site。返回是否保存了登录信息。"""
    if not browser.available():
        ui.alert(win, '没有找到 Edge 或 Chrome',
                 '内置浏览器登录要用到电脑上的 Edge 或 Chrome 浏览器，这台电脑上没有找到。\n'
                 '可以改用 cookies.txt：在平时用的浏览器里装扩展「Get cookies.txt LOCALLY」，登录网站后导出，'
                 '放进 设置 → 网络与登录 → 高级 里的 cookies 文件夹。')
        return False
    res = LoginDialog(win.central, site).exec()
    if res == 'ok':
        win.notify(f'已登录 {site.name}，登录信息已加密保存', 'ok')
        return True
    if res == 'none':
        ui.alert(win, f'没有检测到 {site.name} 的登录',
                 '内置浏览器里没有这个网站的登录信息。请重新点「登录」，在打开的浏览器窗口里完成登录后再点「完成」。')
    return False


def logout(win, site, done=None):
    if not ui.confirm(win, f'退出 {site.name}', f'删除本机保存的 {site.name} 登录信息？内置浏览器里也会退出这个网站。',
                      ok='退出登录', danger=True):
        return

    def work():
        try:
            site_login.logout(site)
        finally:
            if done:
                win.bridge.ui_call.emit(done)
    threading.Thread(target=work, daemon=True).start()
