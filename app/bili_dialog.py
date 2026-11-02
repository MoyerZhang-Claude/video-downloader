import threading
import time

import segno
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from . import bilibili
from .theme import P


def qr_pixmap(text, target=264, border=4):
    matrix = segno.make(text, error='m').matrix
    n = len(matrix)
    module = max(2, target // (n + 2 * border))
    size = (n + 2 * border) * module
    img = QImage(size, size, QImage.Format_RGB32)
    img.fill(QColor('white'))
    p = QPainter(img)
    black = QColor('black')
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if dark:
                p.fillRect((x + border) * module, (y + border) * module, module, module, black)
    p.end()
    return QPixmap.fromImage(img)


class _Signals(QObject):
    qr = Signal(str)
    status = Signal(str, str)
    done = Signal(object)


class BiliLoginDialog(QDialog):
    """用哔哩哔哩手机 App 扫码登录。登录成功后 self.account 为账号信息。"""

    def __init__(self, parent=None, long_term=False):
        super().__init__(parent)
        self.long_term = long_term
        self.setWindowTitle('B站长效登录（电视端扫码）' if long_term else 'B站扫码登录')
        self.setFixedWidth(340)
        self.account = None
        self._stop = threading.Event()
        self.sig = _Signals()
        self.sig.qr.connect(self._show_qr)
        self.sig.status.connect(self._show_status)
        self.sig.done.connect(self._finish)

        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(10)
        title = QLabel('B站长效登录' if long_term else 'B站扫码登录')
        title.setObjectName('sectionTitle')
        v.addWidget(title)
        tip = QLabel('打开 <b>哔哩哔哩</b> 手机 App，点右上角扫一扫，扫描下方二维码并在手机上确认。'
                     + (f'<br><span style="color:{P["accent"]}">长效登录：有效期约 180 天。手机上会显示「登录云视听小电视」，'
                        '这是正常的；下载效果和网页登录完全一样。</span>' if long_term else
                        f'<br><span style="color:{P["text3"]}">网页扫码有效期约 7 天；想更久请用「长效登录」。</span>'))
        tip.setWordWrap(True)
        v.addWidget(tip)
        self.qr_label = QLabel('正在获取二维码…')
        self.qr_label.setAlignment(Qt.AlignCenter)
        self.qr_label.setFixedSize(276, 276)
        self.qr_label.setStyleSheet(f'background: white; color: #73726c; border: 1px solid {P["border_strong"]};'
                                    ' border-radius: 12px;')
        v.addWidget(self.qr_label, alignment=Qt.AlignCenter)
        self.status_label = QLabel('')
        self.status_label.setAlignment(Qt.AlignCenter)
        v.addWidget(self.status_label)
        note = QLabel(r'登录信息只保存在本机（%APPDATA%\VideoDownloader），仅用于解析 B站 视频。')
        note.setWordWrap(True)
        note.setObjectName('hint')
        v.addWidget(note)
        h = QHBoxLayout()
        self.refresh_btn = QPushButton('刷新二维码')
        self.refresh_btn.clicked.connect(self._start)
        self.refresh_btn.setVisible(False)
        h.addWidget(self.refresh_btn)
        h.addStretch(1)
        close = QPushButton('关闭')
        close.clicked.connect(self.reject)
        h.addWidget(close)
        v.addLayout(h)
        self._start()

    def _start(self):
        self._stop.set()
        self._stop = threading.Event()
        self.refresh_btn.setVisible(False)
        self.qr_label.setPixmap(QPixmap())
        self.qr_label.setText('正在获取二维码…')
        self._show_status('', '')
        threading.Thread(target=self._run, args=(self._stop,), daemon=True).start()

    def _run(self, stop):
        login = bilibili.TvQrLogin() if self.long_term else bilibili.QrLogin()
        try:
            self.sig.qr.emit(login.generate())
        except Exception as e:  # noqa: BLE001
            self.sig.status.emit(bilibili.ERROR, f'获取二维码失败：{e}')
            return
        deadline = time.monotonic() + 180
        last = None
        while not stop.is_set():
            try:
                status, msg = login.poll()
            except Exception as e:  # noqa: BLE001 - 网络抖动时继续轮询
                status, msg = bilibili.ERROR, str(e)
            if stop.is_set():
                return
            if status != last:
                self.sig.status.emit(status, msg)
                last = status
            if status == bilibili.SUCCESS:
                try:
                    info = bilibili.account_info()
                except Exception:  # noqa: BLE001
                    info = None
                self.sig.done.emit(info or {'name': '', 'vip': False, 'vip_label': ''})
                return
            if status == bilibili.EXPIRED or time.monotonic() > deadline:
                self.sig.status.emit(bilibili.EXPIRED, '')
                return
            stop.wait(1.5)

    def _show_qr(self, url):
        dpr = self.devicePixelRatioF()
        pm = qr_pixmap(url, target=int(264 * dpr))
        pm.setDevicePixelRatio(dpr)
        self.qr_label.setText('')
        self.qr_label.setPixmap(pm)

    def _show_status(self, status, msg):
        text, role = {
            bilibili.WAITING: ('等待扫码…', 'text2'),
            bilibili.SCANNED: ('已扫码，请在手机上点「确认登录」', 'info'),
            bilibili.EXPIRED: ('二维码已过期，请点「刷新二维码」', 'warn'),
            bilibili.SUCCESS: ('登录成功', 'ok'),
            bilibili.ERROR: (msg or '网络错误，正在重试…', 'err'),
        }.get(status, ('', 'text2'))
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f'color: {P[role]}; font-weight: 600;')
        if status == bilibili.EXPIRED or (status == bilibili.ERROR and '二维码' in (msg or '')):
            self.refresh_btn.setVisible(True)

    def _finish(self, info):
        self.account = info
        name = info.get('name') or ''
        vip = info.get('vip_label') or ('大会员' if info.get('vip') else '非大会员')
        self.status_label.setText(f'登录成功：{name}（{vip}）')
        self.accept()

    def done(self, r):
        self._stop.set()
        super().done(r)
