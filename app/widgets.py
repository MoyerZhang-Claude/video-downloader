"""自定义控件：渐变背景、磨砂玻璃卡片、封面缩略图、提示气泡等。"""
import os
import re
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import (
    Property, QEasingCurve, QObject, QPoint, QPropertyAnimation, QRectF, QSize, Qt, QTimer, Signal,
)
from PySide6.QtGui import (
    QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient,
)
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from .theme import PLATFORM_COLORS

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36')


# ---------------------------------------------------------------- 背景与卡片

class GradientBackground(QWidget):
    """窗口底色：柔和的多色渐变光斑。先在小图上渲染一次，再平滑放大，缩放窗口时也很轻。"""

    BLOBS = ((0.05, 0.10, 0.55, '#a5b4fc'), (0.92, 0.05, 0.45, '#f0abfc'), (0.80, 0.95, 0.60, '#93c5fd'),
             (0.15, 0.95, 0.45, '#fbcfe8'), (0.50, 0.45, 0.40, '#ddd6fe'))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cards = []
        self._small = self._render_small()
        self._cache = None

    @classmethod
    def _render_small(cls, w=480, h=300):
        img = QImage(w, h, QImage.Format_RGB32)
        p = QPainter(img)
        base = QLinearGradient(0, 0, w, h)
        base.setColorAt(0, QColor('#e0e7ff'))
        base.setColorAt(0.5, QColor('#f3e8ff'))
        base.setColorAt(1, QColor('#fce7f3'))
        p.fillRect(0, 0, w, h, base)
        for fx, fy, fr, color in cls.BLOBS:
            rg = QRadialGradient(fx * w, fy * h, fr * max(w, h))
            c0, c1 = QColor(color), QColor(color)
            c0.setAlpha(170)
            c1.setAlpha(0)
            rg.setColorAt(0, c0)
            rg.setColorAt(1, c1)
            p.fillRect(0, 0, w, h, rg)
        p.end()
        return img

    def add_card(self, card):
        self.cards.append(card)
        card.installEventFilter(self)

    def eventFilter(self, obj, event):
        # 卡片移动/改变大小时阴影位置跟着变
        if event.type() in (event.Type.Move, event.Type.Resize, event.Type.Show, event.Type.Hide):
            self.update()
        return False

    def resizeEvent(self, e):
        self._cache = None
        super().resizeEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        if self._cache is None:
            dpr = self.devicePixelRatioF()
            size = QSize(max(1, int(self.width() * dpr)), max(1, int(self.height() * dpr)))
            self._cache = QPixmap.fromImage(self._small.scaled(size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
            self._cache.setDevicePixelRatio(dpr)
        p.drawPixmap(0, 0, self._cache)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        clip = QRectF(e.rect())
        for card in self.cards:
            if not card.isVisible():
                continue
            pos = card.mapTo(self, QPoint(0, 0))
            r = QRectF(pos.x(), pos.y(), card.width(), card.height())
            if not r.adjusted(-10, -10, 10, 14).intersects(clip):
                continue
            inner = QPainterPath()
            inner.addRoundedRect(r, card.radius, card.radius)
            outer = QPainterPath()
            outer.addRect(r.adjusted(-14, -14, 14, 18))
            p.save()
            p.setClipPath(outer.subtracted(inner))
            for i in range(1, 7):
                p.setBrush(QColor(79, 70, 229, 8))
                p.drawRoundedRect(r.adjusted(-i, -i + 3, i, i + 4), card.radius + i, card.radius + i)
            p.restore()


class GlassCard(QFrame):
    """半透明磨砂玻璃卡片：白色渐变填充 + 高光描边。"""
    radius = 16

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, QColor(255, 255, 255, 180))
        g.setColorAt(1, QColor(255, 255, 255, 118))
        p.setBrush(g)
        p.setPen(QPen(QColor(255, 255, 255, 235), 1))
        p.drawRoundedRect(r, self.radius, self.radius)


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


def platform_pill(name):
    lab = QLabel(name or '未知')
    color = PLATFORM_COLORS.get(name, '#6366f1')
    lab.setStyleSheet(f'background: {color}; color: white; border-radius: 8px; padding: 1px 8px;'
                      f' font-size: 11px; font-weight: 700;')
    lab.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    return lab


class Toast(QWidget):
    """窗口底部的轻提示：自绘圆角半透明底，淡入淡出 150ms，不挡鼠标操作。"""
    COLORS = {'info': QColor(30, 27, 75, 228), 'error': QColor(185, 28, 28, 232), 'ok': QColor(4, 120, 87, 232)}

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hide()
        self._text, self._kind, self._opacity = '', 'info', 0.0
        f = self.font()
        f.setPixelSize(13)
        f.setBold(True)
        self.setFont(f)
        self._anim = QPropertyAnimation(self, b'opacity', self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(self._anim_done)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self._fade(0.0))

    def _get_opacity(self):
        return self._opacity

    def _set_opacity(self, v):
        self._opacity = v
        self.update()

    opacity = Property(float, _get_opacity, _set_opacity)

    def show_message(self, text, kind='info', ms=2400):
        self._text, self._kind = text, kind
        fm = self.fontMetrics()
        max_w = max(200, (self.parentWidget().width() if self.parentWidget() else 800) - 80)
        self._shown = fm.elidedText(text, Qt.ElideMiddle, max_w - 44)
        self.resize(fm.horizontalAdvance(self._shown) + 44, fm.height() + 18)
        self.place()
        self.raise_()
        self.show()
        self._fade(1.0)
        self._timer.start(ms)

    def place(self):
        parent = self.parentWidget()
        if parent:
            self.move((parent.width() - self.width()) // 2, parent.height() - self.height() - 64)

    def _fade(self, target):
        self._anim.stop()
        self._anim.setStartValue(self._opacity)
        self._anim.setEndValue(target)
        self._anim.start()

    def _anim_done(self):
        if self._opacity <= 0.01:
            self.hide()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(self._opacity)
        p.setPen(Qt.NoPen)
        p.setBrush(self.COLORS.get(self._kind, self.COLORS['info']))
        r = QRectF(self.rect())
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setPen(QColor('white'))
        p.drawText(self.rect(), int(Qt.AlignCenter), getattr(self, '_shown', self._text))


SPINNER = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'


# ---------------------------------------------------------------- 封面

class CoverView(QWidget):
    """圆角封面缩略图；没有封面时显示渐变占位。点击发出 clicked。"""
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
            g = QLinearGradient(0, 0, self.width(), self.height())
            g.setColorAt(0, QColor(199, 210, 254, 200))
            g.setColorAt(1, QColor(233, 213, 255, 200))
            p.fillRect(self.rect(), g)
            if self._text:
                p.setPen(QColor(255, 255, 255, 235))
                f = self.font()
                f.setPointSizeF(max(7.0, f.pointSizeF() * (1.1 if self.height() > 50 else 0.8)))
                f.setBold(True)
                p.setFont(f)
                p.drawText(self.rect(), int(Qt.AlignCenter), self._text)
        p.setClipping(False)
        p.setPen(QPen(QColor(255, 255, 255, 210), 1))
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
        save_btn.setObjectName('accent')
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
