"""自定义控件：单行省略文本、封面缩略图（含后台加载）、封面预览窗口。"""
import os
import re
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from .theme import c

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36')


# ---------------------------------------------------------------- 文本

class ElidedLabel(QLabel):
    """单行文本，放不下时末尾显示省略号，完整内容放在提示里。"""

    def __init__(self, text='', object_name=None, parent=None):
        super().__init__(parent)
        if object_name:
            self.setObjectName(object_name)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(10)
        self._full = ''
        self.set_full_text(text)

    def set_full_text(self, text, tooltip=None):
        self._full = text or ''
        self.setText(self._full)
        self.setToolTip(tooltip if tooltip is not None else self._full)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setPen(self.palette().color(self.foregroundRole()))
        p.setFont(self.font())
        text = self.fontMetrics().elidedText(self._full, Qt.ElideRight, self.width())
        p.drawText(self.rect(), int(Qt.AlignLeft | Qt.AlignVCenter), text)


# ---------------------------------------------------------------- 封面

class CoverView(QWidget):
    """圆角封面缩略图；没有封面时显示浅色占位。点击发出 clicked。"""
    clicked = Signal()

    def __init__(self, w, h, radius=8, parent=None):
        super().__init__(parent)
        self.setFixedSize(w, h)
        self.radius = radius
        self._pm = None
        self._text = ''
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)

    def set_pixmap(self, pm):
        self._pm = pm
        self.setCursor(Qt.PointingHandCursor if pm else Qt.ArrowCursor)
        self.update()

    def set_placeholder(self, text):
        self._text = text
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(r, self.radius, self.radius)
        p.setClipPath(path)
        if self._pm is not None:
            p.drawPixmap(self.rect(), self._pm)
        else:
            p.fillRect(self.rect(), c('subtle'))
            if self._text:
                p.setPen(c('text3'))
                f = self.font()
                f.setPixelSize(13 if self.height() > 50 else 10)
                p.setFont(f)
                p.drawText(self.rect(), int(Qt.AlignCenter), self._text)
        p.setClipping(False)
        p.setPen(QPen(c('border'), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), self.radius, self.radius)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self._pm is not None:
            self.clicked.emit()
        super().mousePressEvent(e)


THUMB_W, THUMB_H = 256, 144     # 2 倍分辨率，界面里按 128×72 显示，高分屏也清晰


def compose_thumb(img, w=THUMB_W, h=THUMB_H):
    """生成 16:9 缩略图：横图裁切铺满；竖图/方图居中显示，两侧用模糊放大的自身做背景。"""
    out = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    if img.width() / max(1, img.height()) >= 1.5:
        s = img.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        p.drawImage(0, 0, s, (s.width() - w) // 2, (s.height() - h) // 2, w, h)
    else:
        bg = img.scaled(24, 14, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        p.drawImage(out.rect(), bg.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
        p.fillRect(out.rect(), QColor(0, 0, 0, 70))
        f = img.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        p.drawImage((w - f.width()) // 2, (h - f.height()) // 2, f)
    p.end()
    return out


class ThumbLoader(QObject):
    """后台下载并缩放封面，主线程只做 QImage→QPixmap 的转换，界面不卡。"""
    loaded = Signal(str)          # url

    def __init__(self, get_proxy, parent=None):
        super().__init__(parent)
        self.get_proxy = get_proxy
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='thumb')
        self.raw = {}            # url -> 原图字节（保存封面用）
        self.pixmaps = {}        # url -> 缩略图
        self.failed = set()
        self._pending = set()
        self._images = {}
        self._signal = _ThumbSignal()
        self._signal.done.connect(self._on_done)

    def get(self, url):
        return self.pixmaps.get(url)

    def request(self, url, referer=''):
        if not url or url in self.pixmaps or url in self._pending or url in self.failed:
            return
        self._pending.add(url)
        self.pool.submit(self._fetch, url, referer)

    def _fetch(self, url, referer):
        try:
            import requests
            proxy = (self.get_proxy() or '').strip()
            headers = {'User-Agent': UA}
            if referer:
                headers['Referer'] = referer
            r = requests.get(url, headers=headers, timeout=15,
                             proxies={'http': proxy, 'https': proxy} if proxy else None)
            r.raise_for_status()
            data = r.content
            img = QImage.fromData(data)
            if img.isNull():
                raise ValueError('无法识别的图片格式')
            self._images[url] = compose_thumb(img)
            self.raw[url] = data
            self._signal.done.emit(url, True)
        except Exception:  # noqa: BLE001 - 封面失败不影响下载
            self._signal.done.emit(url, False)

    def _on_done(self, url, ok):
        self._pending.discard(url)
        img = self._images.pop(url, None)
        if ok and img is not None:
            pm = QPixmap.fromImage(img)
            pm.setDevicePixelRatio(2.0)
            self.pixmaps[url] = pm
        else:
            self.failed.add(url)
        self.loaded.emit(url)

    def fetch_raw_sync(self, url, referer=''):
        """缩略图还没加载完时，保存封面直接同步下载一次。"""
        import requests
        proxy = (self.get_proxy() or '').strip()
        headers = {'User-Agent': UA, **({'Referer': referer} if referer else {})}
        r = requests.get(url, headers=headers, timeout=20,
                         proxies={'http': proxy, 'https': proxy} if proxy else None)
        r.raise_for_status()
        self.raw[url] = r.content
        return r.content


class _ThumbSignal(QObject):
    done = Signal(str, bool)


def cover_url(info):
    url = info.get('thumbnail') or ''
    if not url:
        thumbs = [t for t in info.get('thumbnails') or [] if t.get('url')]
        if thumbs:
            url = thumbs[-1]['url']
    if url.startswith('//'):
        url = 'https:' + url
    elif url.startswith('http://') and 'hdslb.com' in url:
        url = 'https://' + url[7:]
    return url


def safe_filename(name, limit=80):
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', ' ', name or '').strip().strip('.')
    return (name[:limit].rstrip() or 'cover')


def save_cover_bytes(data, out_dir, title):
    """把封面原图保存为 JPG（本来就是 JPG 时原样写入，不二次压缩），返回保存路径。"""
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, f'{safe_filename(title)} [封面]')
    path, n = base + '.jpg', 1
    while os.path.exists(path):
        n += 1
        path = f'{base} ({n}).jpg'
    if data[:3] == b'\xff\xd8\xff':
        with open(path, 'wb') as f:
            f.write(data)
    else:
        img = QImage.fromData(data)
        if img.isNull() or not img.convertToFormat(QImage.Format_RGB32).save(path, 'JPG', 95):
            raise OSError('封面图片转换失败')
    return path


class CoverDialog(QDialog):
    """封面大图预览，可保存或复制。"""

    def __init__(self, data, title, out_dir, parent=None):
        super().__init__(parent)
        self.setWindowTitle('封面预览')
        self.data, self.title, self.out_dir = data, title, out_dir
        self.saved_path = None
        img = QImage.fromData(data)
        self.img = img
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 12)
        screen = QGuiApplication.primaryScreen().availableGeometry()
        max_w, max_h = int(screen.width() * 0.7), int(screen.height() * 0.65)
        pm = QPixmap.fromImage(img)
        dpr = self.devicePixelRatioF()
        if pm.width() / dpr > max_w or pm.height() / dpr > max_h:
            pm = pm.scaled(int(max_w * dpr), int(max_h * dpr), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        pm.setDevicePixelRatio(dpr)
        pic = QLabel()
        pic.setPixmap(pm)
        pic.setAlignment(Qt.AlignCenter)
        v.addWidget(pic)
        fmt = 'JPG' if data[:3] == b'\xff\xd8\xff' else ('PNG' if data[:4] == b'\x89PNG' else
                                                          ('WEBP' if data[8:12] == b'WEBP' else '图片'))
        info = QLabel(f'{img.width()} × {img.height()} · {fmt} · {len(data) / 1024:.0f} KB')
        info.setObjectName('hint')
        h = QHBoxLayout()
        h.addWidget(info)
        h.addStretch(1)
        copy_btn = QPushButton('复制图片')
        copy_btn.clicked.connect(self._copy)
        save_btn = QPushButton('保存封面')
        save_btn.setObjectName('primary')
        save_btn.clicked.connect(self._save)
        close_btn = QPushButton('关闭')
        close_btn.clicked.connect(self.reject)
        for b in (copy_btn, save_btn, close_btn):
            b.setCursor(Qt.PointingHandCursor)
            h.addWidget(b)
        v.addLayout(h)

    def _copy(self):
        QGuiApplication.clipboard().setImage(self.img)
        self.sender().setText('✓ 已复制')

    def _save(self):
        self.saved_path = save_cover_bytes(self.data, self.out_dir, self.title)
        self.accept()
