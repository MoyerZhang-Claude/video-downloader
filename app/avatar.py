"""用户头像：从任意图片里框选一个圆形区域当头像（拖动选位置、滚轮或滑块缩放、旋转）。

文件放在软件目录的 avatar 文件夹（和 config.json 在一起，打包.bat 会备份）：
- avatar.png       512×512 的圆形头像（圆外透明），侧边栏左下角显示的就是它
- source.jpg/.png  原图（长边缩到 2048 以内），「调整头像」时从它重新框选
- crop.json        上次框选的位置，重新打开时还原
删掉整个 avatar 文件夹 = 不用头像。
"""
import json
import logging
import math
import os
import shutil

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter, QPainterPath, QPen, QTransform
from PySide6.QtWidgets import QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from .config import ROOT
from .theme import c
from .ui import Avatar, IconButton

log = logging.getLogger(__name__)

DIR = os.path.join(ROOT, 'avatar')
AVATAR = os.path.join(DIR, 'avatar.png')
CROP = os.path.join(DIR, 'crop.json')
OUT_SIZE = 512
SRC_MAX = 2048
MAX_ZOOM = 8.0
FILTER = '图片 (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif *.tiff);;所有文件 (*)'

_cache = {'mtime': None, 'img': None}


# ---------------------------------------------------------------- 读写

def exists():
    return os.path.isfile(AVATAR)


def image():
    """当前头像（QImage，圆外透明）；没有就返回 None。按修改时间缓存。"""
    try:
        m = os.path.getmtime(AVATAR)
    except OSError:
        return None
    if _cache['mtime'] != m:
        img = QImage(AVATAR)
        _cache.update(mtime=m, img=None if img.isNull() else img)
    return _cache['img']


def _source_path():
    for name in ('source.png', 'source.jpg'):
        p = os.path.join(DIR, name)
        if os.path.isfile(p):
            return p
    return None


def has_source():
    return _source_path() is not None


def load_image(path):
    """读图片（手机照片按 EXIF 方向转正，太大的先缩小）。返回 (QImage 或 None, 错误说明)。"""
    r = QImageReader(path)
    r.setAutoTransform(True)
    size = r.size()
    if size.isValid() and max(size.width(), size.height()) > SRC_MAX:
        r.setScaledSize(size.scaled(SRC_MAX, SRC_MAX, Qt.KeepAspectRatio))
    img = r.read()
    if img.isNull():
        return None, r.errorString()
    if max(img.width(), img.height()) > SRC_MAX:
        img = img.scaled(SRC_MAX, SRC_MAX, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    if min(img.width(), img.height()) < 8:
        return None, '图片太小'
    fmt = QImage.Format_ARGB32_Premultiplied if img.hasAlphaChannel() else QImage.Format_RGB32
    return img.convertToFormat(fmt), ''


def load_saved():
    """「调整头像」用：上次的原图和框选位置。"""
    path = _source_path()
    if not path:
        return None, None
    img, _ = load_image(path)
    try:
        with open(CROP, encoding='utf-8') as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = None
    return img, state


def save(result, state, source=None):
    """保存头像；source 不为 None 时（换了新图片）连原图一起换掉。"""
    os.makedirs(DIR, exist_ok=True)
    if source is not None:
        for name in ('source.png', 'source.jpg'):
            try:
                os.remove(os.path.join(DIR, name))
            except OSError:
                pass
        if source.hasAlphaChannel():
            source.save(os.path.join(DIR, 'source.png'))
        else:
            source.save(os.path.join(DIR, 'source.jpg'), 'JPG', 92)
    tmp = AVATAR + '.tmp.png'
    if not result.save(tmp, 'PNG'):
        raise OSError('写入头像失败')
    os.replace(tmp, AVATAR)
    with open(CROP, 'w', encoding='utf-8') as f:
        json.dump(state, f)
    _cache['mtime'] = None


def remove():
    shutil.rmtree(DIR, ignore_errors=True)
    _cache.update(mtime=None, img=None)


# ---------------------------------------------------------------- 裁剪区域

class CropView(QWidget):
    """显示图片 + 圆形取景框。拖动移动、滚轮缩放（以鼠标位置为中心）、双击复位、方向键微调。"""
    changed = Signal()

    MARGIN = 26

    def __init__(self, img, state=None, size=400, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setCursor(Qt.OpenHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setToolTip('拖动选择位置 · 滚轮缩放 · 双击复位')
        self._drag = None
        self._scaled = None
        self.set_image(img, state)

    # 圆的半径（窗口像素）、图片缩放（窗口像素 / 图片像素）
    @property
    def radius(self):
        return (self.width() - 2 * self.MARGIN) / 2

    def _base(self):
        return 2 * self.radius / min(self.img.width(), self.img.height())

    def scale(self):
        return self._base() * self.zoom

    def set_image(self, img, state=None):
        st = state or {}
        self.img, self.rot, self._scaled = img, 0, None
        for _ in range(int(st.get('rot', 0)) % 360 // 90):
            self._rotate_image()
        self.zoom = 1.0
        self.cx, self.cy = img.width() / 2, img.height() / 2
        try:
            self.zoom = min(MAX_ZOOM, max(1.0, float(st.get('zoom', 1.0))))
            self.cx = float(st.get('cx', 0.5)) * self.img.width()
            self.cy = float(st.get('cy', 0.5)) * self.img.height()
        except (TypeError, ValueError):
            pass
        self._clamp()
        self._changed()

    def state(self):
        return {'cx': round(self.cx / self.img.width(), 5), 'cy': round(self.cy / self.img.height(), 5),
                'zoom': round(self.zoom, 4), 'rot': self.rot}

    def _changed(self):
        self.update()
        self.changed.emit()

    def _clamp(self):
        r = self.radius / self.scale()       # 圆的半径（图片像素）：圆始终完整落在图片里
        w, h = self.img.width(), self.img.height()
        self.cx = min(max(self.cx, r), w - r)
        self.cy = min(max(self.cy, r), h - r)

    # ---- 操作
    def zoom_to(self, z, anchor=None):
        z = min(MAX_ZOOM, max(1.0, z))
        o = QPointF(self.width() / 2, self.height() / 2)
        a = anchor if anchor is not None else o
        s0 = self.scale()
        px, py = self.cx + (a.x() - o.x()) / s0, self.cy + (a.y() - o.y()) / s0   # 鼠标下面那一点保持不动
        self.zoom = z
        s1 = self.scale()
        self.cx, self.cy = px - (a.x() - o.x()) / s1, py - (a.y() - o.y()) / s1
        self._clamp()
        self._changed()

    def _rotate_image(self):
        h = self.img.height()
        self.img = self.img.transformed(QTransform().rotate(90), Qt.SmoothTransformation)
        self._scaled = None
        if hasattr(self, 'cx'):
            self.cx, self.cy = h - self.cy, self.cx
        self.rot = (self.rot + 90) % 360

    def rotate(self):
        self._rotate_image()
        self._clamp()
        self._changed()

    def reset(self):
        self.zoom = 1.0
        self.cx, self.cy = self.img.width() / 2, self.img.height() / 2
        self._changed()

    def move_by(self, dx, dy):
        s = self.scale()
        self.cx -= dx / s
        self.cy -= dy / s
        self._clamp()
        self._changed()

    # ---- 鼠标键盘
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.position()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            d = e.position() - self._drag
            self._drag = e.position()
            self.move_by(d.x(), d.y())

    def mouseReleaseEvent(self, e):
        self._drag = None
        self.setCursor(Qt.OpenHandCursor)

    def mouseDoubleClickEvent(self, e):
        self.reset()

    def wheelEvent(self, e):
        steps = e.angleDelta().y() / 120 or e.pixelDelta().y() / 60
        if steps:
            self.zoom_to(self.zoom * 1.12 ** steps, e.position())

    def keyPressEvent(self, e):
        k = e.key()
        moves = {Qt.Key_Left: (6, 0), Qt.Key_Right: (-6, 0), Qt.Key_Up: (0, 6), Qt.Key_Down: (0, -6)}
        if k in moves:
            self.move_by(*moves[k])
        elif k in (Qt.Key_Plus, Qt.Key_Equal):
            self.zoom_to(self.zoom * 1.12)
        elif k == Qt.Key_Minus:
            self.zoom_to(self.zoom / 1.12)
        else:
            super().keyPressEvent(e)

    # ---- 绘制
    def _display_image(self):
        """缩小显示时先平滑缩好（直接画大图会有锯齿和闪烁）。"""
        s = self.scale() * self.devicePixelRatioF()
        if s >= 1:
            return self.img, 1.0
        if self._scaled is None or self._scaled[0] != self.scale():
            w = max(1, round(self.img.width() * s))
            h = max(1, round(self.img.height() * s))
            self._scaled = (self.scale(), self.img.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
        small = self._scaled[1]
        return small, self.img.width() / small.width()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        frame = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(frame, 14, 14)
        p.setClipPath(clip)
        p.fillRect(frame, QColor('#1c1c1b'))
        s = self.scale()
        o = frame.center()
        img, k = self._display_image()
        target = QRectF(o.x() - self.cx * s, o.y() - self.cy * s, self.img.width() * s, self.img.height() * s)
        p.drawImage(target, img, QRectF(0, 0, self.img.width() / k, self.img.height() / k))
        r = self.radius
        circle = QRectF(o.x() - r, o.y() - r, 2 * r, 2 * r)
        shade = QPainterPath()
        shade.setFillRule(Qt.OddEvenFill)
        shade.addRect(frame)
        shade.addEllipse(circle)
        p.fillPath(shade, QColor(20, 20, 19, 165))
        p.setPen(QPen(QColor(255, 255, 255, 230), 2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(circle)
        if self._drag is not None:          # 拖动时显示三等分辅助线
            p.setClipPath(_ellipse_path(circle))
            p.setPen(QPen(QColor(255, 255, 255, 90), 1))
            for i in (1, 2):
                x = circle.left() + circle.width() * i / 3
                y = circle.top() + circle.height() * i / 3
                p.drawLine(QPointF(x, circle.top()), QPointF(x, circle.bottom()))
                p.drawLine(QPointF(circle.left(), y), QPointF(circle.right(), y))

    # ---- 结果
    def result(self, n=OUT_SIZE):
        """把圆里的部分裁成 n×n 的图片，圆外透明。"""
        side = 2 * self.radius / self.scale()           # 圆的直径（图片像素）
        x = min(max(self.cx - side / 2, 0), self.img.width() - side)
        y = min(max(self.cy - side / 2, 0), self.img.height() - side)
        rect = QRect(round(x), round(y), max(1, round(side)), max(1, round(side)))
        part = self.img.copy(rect).scaled(n, n, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        out = QImage(n, n, QImage.Format_ARGB32_Premultiplied)
        out.fill(Qt.transparent)
        p = QPainter(out)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        p.drawImage(0, 0, part)
        mask = QImage(n, n, QImage.Format_ARGB32_Premultiplied)
        mask.fill(Qt.transparent)
        mp = QPainter(mask)
        mp.setRenderHint(QPainter.Antialiasing)
        mp.setPen(Qt.NoPen)
        mp.setBrush(QColor(0, 0, 0))
        mp.drawEllipse(QRectF(0, 0, n, n))
        mp.end()
        p.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        p.drawImage(0, 0, mask)
        p.end()
        return out


def _ellipse_path(rect):
    path = QPainterPath()
    path.addEllipse(rect)
    return path


class ZoomSlider(QWidget):
    """缩放滑块（对数刻度：拖到中间 ≈ 放大 2.8 倍）。value 0~1。"""
    moved = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(28)
        self.setMinimumWidth(160)
        self.setCursor(Qt.PointingHandCursor)
        self.value = 0.0
        self._down = False

    def set_value(self, v):
        self.value = min(1.0, max(0.0, v))
        self.update()

    def _track(self):
        return QRectF(9, self.height() / 2 - 2, self.width() - 18, 4)

    def _pick(self, x):
        t = self._track()
        self.set_value((x - t.left()) / t.width())
        self.moved.emit(self.value)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._down = True
            self._pick(e.position().x())

    def mouseMoveEvent(self, e):
        if self._down:
            self._pick(e.position().x())

    def mouseReleaseEvent(self, e):
        self._down = False

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = self._track()
        p.setPen(Qt.NoPen)
        p.setBrush(c('track'))
        p.drawRoundedRect(t, 2, 2)
        x = t.left() + t.width() * self.value
        p.setBrush(c('text3'))
        p.drawRoundedRect(QRectF(t.left(), t.top(), x - t.left(), t.height()), 2, 2)
        knob = QRectF(x - 8, self.height() / 2 - 8, 16, 16)
        p.setBrush(c('surface'))
        p.setPen(QPen(c('border_focus'), 1))
        p.drawEllipse(knob)


# ---------------------------------------------------------------- 编辑窗口

class AvatarEditor(QDialog):
    """编辑头像：左边框选，右边实时预览。exec() 返回真表示已保存。"""

    def __init__(self, parent, img, state=None, source_is_new=True):
        super().__init__(parent)
        self.setWindowTitle('编辑头像')
        self.new_source = img if source_is_new else None
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 18)
        v.setSpacing(6)
        title = QLabel('编辑头像')
        title.setObjectName('sectionTitle')
        v.addWidget(title)
        hint = QLabel('拖动图片，选出圆圈里要显示的部分；滚轮或下面的滑块缩放')
        hint.setObjectName('rowDesc')
        v.addWidget(hint)
        v.addSpacing(8)

        body = QHBoxLayout()
        body.setSpacing(22)
        left = QVBoxLayout()
        left.setSpacing(10)
        self.view = CropView(img, state)
        left.addWidget(self.view)
        tools = QHBoxLayout()
        tools.setSpacing(4)
        zo = IconButton('zoom_out', '缩小', size=30, icon_size=18)
        zo.clicked.connect(lambda: self.view.zoom_to(self.view.zoom / 1.25))
        self.slider = ZoomSlider()
        self.slider.moved.connect(lambda t: self.view.zoom_to(MAX_ZOOM ** t))
        zi = IconButton('zoom_in', '放大', size=30, icon_size=18)
        zi.clicked.connect(lambda: self.view.zoom_to(self.view.zoom * 1.25))
        rot = IconButton('rotate', '向右旋转 90°', size=30, icon_size=18)
        rot.clicked.connect(self.view.rotate)
        tools.addWidget(zo)
        tools.addWidget(self.slider, 1)
        tools.addWidget(zi)
        tools.addSpacing(8)
        tools.addWidget(rot)
        left.addLayout(tools)
        body.addLayout(left)

        right = QVBoxLayout()
        right.setSpacing(12)
        cap = QLabel('预览')
        cap.setObjectName('rowDesc')
        right.addWidget(cap)
        self.previews = [Avatar(96), Avatar(52), Avatar(32)]
        row = QHBoxLayout()
        row.setSpacing(12)
        for a in self.previews:
            row.addWidget(a, 0, Qt.AlignBottom)
        row.addStretch(1)
        right.addLayout(row)
        side = QLabel('最小的那个就是侧边栏左下角的大小')
        side.setObjectName('rowDesc')
        side.setWordWrap(True)
        right.addWidget(side)
        right.addStretch(1)
        body.addLayout(right)
        v.addLayout(body)
        v.addSpacing(14)

        btns = QHBoxLayout()
        other = QPushButton('换一张图片…')
        other.setObjectName('ghost')
        other.setCursor(Qt.PointingHandCursor)
        other.clicked.connect(self._other)
        btns.addWidget(other)
        btns.addStretch(1)
        cancel = QPushButton('取消')
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        ok = QPushButton('保存')
        ok.setObjectName('primary')
        ok.setCursor(Qt.PointingHandCursor)
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        v.addLayout(btns)

        self._timer = QTimer(self, singleShot=True, interval=16)
        self._timer.timeout.connect(self._refresh_preview)
        self.view.changed.connect(self._on_change)
        self._on_change()
        self.view.setFocus()

    def _on_change(self):
        self.slider.set_value(math.log(self.view.zoom) / math.log(MAX_ZOOM))
        self._timer.start()

    def _refresh_preview(self):
        img = self.view.result(192)
        for a in self.previews:
            a.set_image(img)

    def _other(self):
        img = pick_image(self)
        if img is not None:
            self.new_source = img
            self.view.set_image(img)

    def accept(self):
        try:
            save(self.view.result(), self.view.state(), self.new_source)
        except OSError as e:
            log.warning('保存头像失败: %s', e)
            QMessageBox.warning(self, '保存头像失败', f'{e}\n\n保存位置：{DIR}')
            return
        super().accept()


def pick_image(parent):
    path, _ = QFileDialog.getOpenFileName(parent, '选择一张图片作为头像', os.path.expanduser('~/Pictures'), FILTER)
    if not path:
        return None
    img, err = load_image(path)
    if img is None:
        QMessageBox.warning(parent, '打不开这张图片',
                            f'{os.path.basename(path)}\n{err or "格式不支持"}\n\n支持 JPG、PNG、WebP、BMP、GIF、TIFF。')
    return img


def change(parent):
    """选新图片 → 框选 → 保存。保存了返回 True。"""
    img = pick_image(parent)
    return img is not None and AvatarEditor(parent, img).exec() == QDialog.Accepted


def adjust(parent):
    """用上次的原图重新框选；没有原图就当作换新图片。"""
    img, state = load_saved()
    if img is None:
        return change(parent)
    return AvatarEditor(parent, img, state, source_is_new=False).exec() == QDialog.Accepted
