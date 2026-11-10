"""Claude 风格的界面组件（全部自绘，颜色取自 theme.P，切换深浅色后自动跟着变）。

- Spark：应用标志（星芒 + 下载箭头），解析时会「呼吸」
- IconButton / Chip / NavItem / SelectButton：图标按钮、胶囊按钮、侧边栏项目、带下拉箭头的文字按钮
- Composer：圆角大输入框（Enter 发送、Shift+Enter 换行）
- Card / ProgressLine / Toggle / Segmented / Avatar / PlatformTag / Overlay
- 窗口外观：Win11 标题栏配色、菜单圆角
"""
import ctypes
import math
import sys
import time
import weakref

import shiboken6
from PySide6.QtCore import (
    Property, QEasingCurve, QEvent, QObject, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QTimer, Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap, QTransform
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QButtonGroup, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QMenu, QPlainTextEdit, QPushButton, QSizePolicy, QStyledItemDelegate, QToolTip, QVBoxLayout, QWidget,
)

from . import icons, theme
from .theme import PLATFORM_COLORS, P, c


def px_font(size, weight=None, family=None):
    f = QFont(family) if family else QFont(QApplication.font())
    f.setPixelSize(size)
    if weight:
        f.setWeight(QFont.Weight(weight))
    return f


# ---------------------------------------------------------------- 动画时钟（所有动画共用一个定时器）

class _Clock(QObject):
    def __init__(self):
        super().__init__()
        self.items = weakref.WeakSet()
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self._tick)

    def add(self, w):
        self.items.add(w)
        if not self.timer.isActive():
            self.timer.start()

    def remove(self, w):
        self.items.discard(w)
        if not self.items:
            self.timer.stop()

    def _tick(self):
        for w in list(self.items):
            if not shiboken6.isValid(w):
                self.items.discard(w)
            elif w.isVisible():
                w.update()
        if not self.items:
            self.timer.stop()


_clock = None


def clock():
    global _clock
    if _clock is None:
        _clock = _Clock()
    return _clock


# ---------------------------------------------------------------- 星芒标志

_LENGTHS = [0.300, 0.214, 0.270, 0.206, 0.188, None, 0.180, 0.274, 0.202, 0.288]


def _ray(length, w_base, w_tip):
    r = w_tip / 2
    tip_x = max(length - r, r)
    p = QPainterPath()
    p.moveTo(0, -w_base / 2)
    p.lineTo(tip_x, -r)
    p.arcTo(QRectF(tip_x - r, -r, 2 * r, 2 * r), 90, -180)
    p.lineTo(0, w_base / 2)
    p.closeSubpath()
    return p


def _rounded_tri(pts, radius):
    p = QPainterPath()
    n = len(pts)
    for i in range(n):
        prev, cur, nxt = pts[i - 1], pts[i], pts[(i + 1) % n]
        v1 = (prev[0] - cur[0], prev[1] - cur[1])
        l1 = math.hypot(*v1)
        v1 = (v1[0] / l1, v1[1] / l1)
        v2 = (nxt[0] - cur[0], nxt[1] - cur[1])
        l2 = math.hypot(*v2)
        v2 = (v2[0] / l2, v2[1] / l2)
        ang = math.acos(max(-1.0, min(1.0, v1[0] * v2[0] + v1[1] * v2[1])))
        d = radius / math.tan(ang / 2)
        a = (cur[0] + v1[0] * d, cur[1] + v1[1] * d)
        b = (cur[0] + v2[0] * d, cur[1] + v2[1] * d)
        p.moveTo(*a) if i == 0 else p.lineTo(*a)
        p.quadTo(cur[0], cur[1], *b)
    p.closeSubpath()
    return p


def spark_paths(T, t=None):
    """图标里的星芒 + 下载箭头（与 app 图标同一图形），T 为图块边长。t 不为 None 时按时间做呼吸动画。"""
    cx, cy = 0.5 * T, 0.415 * T
    out = []
    for i, L in enumerate(_LENGTHS):
        if not L:
            continue
        k = 1.0 if t is None else 1 + 0.17 * math.sin(t * 2 * math.pi / 1.3 - i * 0.85)
        out.append(QTransform().translate(cx, cy).rotate(-90 + 36 * i).map(_ray(L * T * k, 0.092 * T, 0.068 * T)))
    hub = QPainterPath()
    hub.addEllipse(QPointF(cx, cy), 0.094 * T, 0.094 * T)
    out.append(hub)
    dy = 0 if t is None else 0.03 * T * (0.5 - 0.5 * math.cos(t * 2 * math.pi / 1.3))
    stem = QPainterPath()
    sw = 0.080 * T
    stem.addRoundedRect(QRectF(cx - sw / 2, cy, sw, 0.242 * T + dy), sw / 2, sw / 2)
    out.append(stem)
    hy, hx = cy + 0.222 * T + dy, 0.112 * T
    out.append(_rounded_tri([(cx - hx, hy), (cx + hx, hy), (cx, cy + 0.342 * T + dy)], 0.026 * T))
    return out


class Spark(QWidget):
    """应用标志。set_busy(True) 时星芒按顺序伸缩、箭头上下轻点，像在「思考」。"""

    def __init__(self, size=28, parent=None, color_role='brand'):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.role = color_role
        self._busy = False
        self._t0 = time.monotonic()
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def set_busy(self, on):
        if on == self._busy:
            return
        self._busy = on
        if on:
            self._t0 = time.monotonic()
            clock().add(self)
        else:
            clock().remove(self)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        S = min(self.width(), self.height())
        T = S / 0.80
        p.translate(self.width() / 2 - 0.5 * T, self.height() / 2 - 0.44 * T)
        p.setPen(Qt.NoPen)
        p.setBrush(c(self.role))
        t = (time.monotonic() - self._t0) if self._busy else None
        for path in spark_paths(T, t):
            p.drawPath(path)


# ---------------------------------------------------------------- 按钮

class _HoverButton(QAbstractButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_Hover)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)

    def _hov(self):
        return self.underMouse() and self.isEnabled()


class IconButton(_HoverButton):
    """方形图标按钮。variant: ghost（默认，悬停才有底色）/ outline / accent（陶土橙）/ primary（深色）"""

    def __init__(self, icon, tip='', size=32, icon_size=18, variant='ghost', radius=8, parent=None):
        super().__init__(parent)
        self._icon, self.variant, self.icon_px, self.radius = icon, variant, icon_size, radius
        self.setFixedSize(size, size)
        self.color_role = None
        if tip:
            self.setToolTip(tip)

    def set_icon(self, name, tip=None):
        self._icon = name
        if tip is not None:
            self.setToolTip(tip)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        en, down, hov, v = self.isEnabled(), self.isDown(), self._hov(), self.variant
        border = None
        if v == 'accent':
            bg = c('accent_off') if not en else c('accent_press' if down else 'accent_hover' if hov else 'accent')
            fg = c('on_accent')
        elif v == 'primary':
            bg = c('primary_off') if not en else c('primary_hover' if hov or down else 'primary')
            fg = c('on_primary')
        else:
            bg = c('press') if down else c('hover') if hov else None
            if self.isChecked():
                bg = c('selected')
            if v == 'outline':
                border = c('border_focus' if hov else 'border_strong')
            fg = c(self.color_role) if self.color_role else c('text' if hov or self.isChecked() else 'text3')
            if not en:
                fg = c('text4')
        if bg is not None:
            p.setPen(Qt.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(r, self.radius, self.radius)
        if border is not None:
            p.setPen(QPen(border, 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r, self.radius, self.radius)
        s = self.icon_px
        icons.paint(p, self._icon, QRectF((self.width() - s) / 2, (self.height() - s) / 2, s, s), fg,
                    2.0 if v in ('accent', 'primary') else 1.75)


class Chip(_HoverButton):
    """胶囊按钮（可带图标）。checkable 时勾选后变成淡橙底、橙色字，像 Claude 输入框里打开的工具。"""

    def __init__(self, text, icon=None, checkable=False, tip='', height=32, font_px=13, parent=None):
        super().__init__(parent)
        self._icon = icon
        self.setText(text)
        self.setCheckable(checkable)
        self.setFont(px_font(font_px))
        self.setFixedHeight(height)
        if tip:
            self.setToolTip(tip)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def sizeHint(self):
        fm = QFontMetrics(self.font())
        w = 24 + fm.horizontalAdvance(self.text()) + (22 if self._icon else 0)
        return QSize(w, self.height())

    def setText(self, text):
        super().setText(text)
        self.updateGeometry()
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        rad = r.height() / 2
        hov, on = self._hov(), self.isChecked()
        if on:
            p.setPen(QPen(c('accent', 0.35), 1))
            p.setBrush(c('tint_accent'))
            fg = icon_fg = c('accent')
        else:
            p.setPen(QPen(c('border_focus' if hov else 'border_strong'), 1))
            p.setBrush(c('press') if self.isDown() else c('hover') if hov else Qt.NoBrush)
            fg, icon_fg = c('text' if hov else 'text2'), c('text2' if hov else 'text3')
        if not self.isEnabled():
            fg = icon_fg = c('text4')
        p.drawRoundedRect(r, rad, rad)
        x = 12
        if self._icon:
            icons.paint(p, self._icon, QRectF(x, (self.height() - 16) / 2, 16, 16), icon_fg)
            x += 22
        p.setPen(fg)
        p.setFont(self.font())
        p.drawText(QRectF(x, 0, self.width() - x - 10, self.height()), int(Qt.AlignVCenter | Qt.AlignLeft),
                   self.text())


class SelectButton(_HoverButton):
    """文字 + 下拉箭头（像 Claude 输入框下方的模型选择）。点击时调用 on_click 弹出菜单。"""

    def __init__(self, text='', icon=None, tip='', parent=None, font_px=13):
        super().__init__(parent)
        self._icon = icon
        self.setFont(px_font(font_px))
        self.setFixedHeight(30)
        self.setText(text)
        if tip:
            self.setToolTip(tip)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def setText(self, text):
        super().setText(text)
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        fm = QFontMetrics(self.font())
        return QSize(16 + (22 if self._icon else 0) + min(fm.horizontalAdvance(self.text()), 260) + 22, 30)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        hov = self._hov()
        if hov or self.isDown():
            p.setPen(Qt.NoPen)
            p.setBrush(c('press' if self.isDown() else 'hover'))
            p.drawRoundedRect(QRectF(self.rect()), 8, 8)
        fg = c('text' if hov else 'text2')
        x = 8
        if self._icon:
            icons.paint(p, self._icon, QRectF(x, 7, 16, 16), c('text2' if hov else 'text3'))
            x += 22
        p.setFont(self.font())
        p.setPen(fg)
        fm = QFontMetrics(self.font())
        avail = self.width() - x - 24
        p.drawText(QRectF(x, 0, avail, self.height()), int(Qt.AlignVCenter | Qt.AlignLeft),
                   fm.elidedText(self.text(), Qt.ElideMiddle, avail))
        icons.paint(p, 'chevron_down', QRectF(self.width() - 21, 8, 14, 14), c('text3'))


class NavItem(_HoverButton):
    """侧边栏项目：图标 + 文字（+ 右侧小角标）。special=True 时图标是橙色圆底加号（新建）。"""

    def __init__(self, text, icon=None, special=False, height=36, font_px=14, parent=None):
        super().__init__(parent)
        self.setText(text)
        self._icon, self.special = icon, special
        self.badge = ''
        self.trailing = ''          # 右侧的灰色小字（如分区里的视频数）
        self.muted = False          # 文字用浅灰色（如「查看全部」）
        self.collapsed = False
        self._elided = False        # 文字太长被省略成「…」时，鼠标停留显示完整文字
        self.setCheckable(not special)
        self.setFixedHeight(height)
        self.setFont(px_font(font_px))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_badge(self, text):
        if text != self.badge:
            self.badge = text
            self.update()

    def set_trailing(self, text):
        if text != self.trailing:
            self.trailing = text
            self.update()

    def set_collapsed(self, on):
        self.collapsed = on
        self.setToolTip(self.text() if on else '')
        self.update()

    def event(self, e):
        if e.type() == QEvent.ToolTip and not self.toolTip():
            if self._elided:
                QToolTip.showText(e.globalPos(), self.text(), self)
            else:
                QToolTip.hideText()
            return True
        return super().event(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        hov, on = self._hov(), self.isChecked()
        if on or hov or self.isDown():
            p.setPen(Qt.NoPen)
            p.setBrush(c('selected') if on else c('press') if self.isDown() else c('hover'))
            p.drawRoundedRect(r, 8, 8)
        x = (self.width() - 20) / 2 if self.collapsed else 9
        iy = (self.height() - 20) / 2
        if self.special:
            p.setPen(Qt.NoPen)
            p.setBrush(c('accent_hover' if hov else 'accent'))
            p.drawEllipse(QRectF(x, iy, 20, 20))
            icons.paint(p, 'plus', QRectF(x + 4, iy + 4, 12, 12), c('on_accent'), 2.4)
        elif self._icon:
            icons.paint(p, self._icon, QRectF(x + 1, iy + 1, 18, 18), c('text' if on or hov else 'text2'))
        if self.badge:
            f = px_font(11, 600)
            fm = QFontMetrics(f)
            bw = max(18, fm.horizontalAdvance(self.badge) + 10)
            if self.collapsed:
                br = QRectF(self.width() - bw / 2 - 12, 2, bw, 16)
            else:
                br = QRectF(self.width() - bw - 8, (self.height() - 18) / 2, bw, 18)
            p.setPen(Qt.NoPen)
            p.setBrush(c('brand'))
            p.drawRoundedRect(br, br.height() / 2, br.height() / 2)
            p.setPen(QColor('#ffffff'))
            p.setFont(f)
            p.drawText(br, int(Qt.AlignCenter), self.badge)
        if self.collapsed:
            return
        tx = x + (30 if (self._icon or self.special) else 1)
        tail = 0
        if self.trailing and not self.badge:
            f2 = px_font(12)
            tail = QFontMetrics(f2).horizontalAdvance(self.trailing) + 14
            p.setFont(f2)
            p.setPen(c('text4'))
            p.drawText(QRectF(self.width() - tail, 0, tail - 8, self.height()), int(Qt.AlignVCenter | Qt.AlignRight),
                       self.trailing)
        p.setFont(self.font())
        p.setPen(c('text3' if self.muted and not hov else 'text' if on or self.special else 'text2'))
        fm = QFontMetrics(self.font())
        avail = self.width() - tx - (40 if self.badge else max(10, tail))
        shown = fm.elidedText(self.text(), Qt.ElideRight, int(avail))
        self._elided = shown != self.text()
        p.drawText(QRectF(tx, 0, avail, self.height()), int(Qt.AlignVCenter | Qt.AlignLeft), shown)


class SideHeader(_HoverButton):
    """侧边栏的小标题（「分区 ▾」「最近下载 ▾」），点一下折叠 / 展开；可以带一个右侧的「+」按钮。"""
    toggled_open = Signal(bool)

    def __init__(self, text, open_=True, plus_tip='', parent=None):
        super().__init__(parent)
        self.setText(text)
        self.open = open_
        self.setFixedHeight(30)
        self.setFont(px_font(12))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.clicked.connect(self._flip)
        self.plus = None
        if plus_tip:
            self.plus = IconButton('plus', plus_tip, size=26, icon_size=16, radius=7, parent=self)

    def _flip(self):
        self.open = not self.open
        self.update()
        self.toggled_open.emit(self.open)

    def resizeEvent(self, e):
        if self.plus:
            self.plus.move(self.width() - self.plus.width() - 4, (self.height() - self.plus.height()) // 2)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setFont(self.font())
        hov = self._hov()
        p.setPen(c('text2' if hov else 'text3'))
        fm = QFontMetrics(self.font())
        w = fm.horizontalAdvance(self.text())
        p.drawText(QRectF(10, 0, w + 4, self.height()), int(Qt.AlignVCenter | Qt.AlignLeft), self.text())
        icons.paint(p, 'chevron_down' if self.open else 'chevron_right',
                    QRectF(10 + w + 4, (self.height() - 12) / 2, 12, 12), c('text3'), 2.0)


class TreeBox(QWidget):
    """展开的分区：左边一条细竖线，里面是这个分区的视频（像 Claude 侧边栏里展开的项目）。"""

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(QRectF(20, 2, 1, self.height() - 4), c('border_strong'))


# ---------------------------------------------------------------- 容器与小部件

class Card(QFrame):
    """圆角卡片：白底（深色模式为深灰）+ 细边框。"""

    def __init__(self, radius=14, fill='surface', border='border', parent=None):
        super().__init__(parent)
        self.radius, self.fill, self.border = radius, fill, border

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(c(self.border), 1) if self.border else Qt.NoPen)
        p.setBrush(c(self.fill) if self.fill else Qt.NoBrush)
        p.drawRoundedRect(r, self.radius, self.radius)


class HLine(QWidget):
    def __init__(self, parent=None, margin=0):
        super().__init__(parent)
        self.setFixedHeight(1)
        self.margin = margin

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.margin, 0, self.width() - 2 * self.margin, 1, c('border'))


class ProgressLine(QWidget):
    """细进度条（6px，圆头）。role 决定颜色；indeterminate 时一段亮条来回流动（合并 / 生成弹幕版）。"""

    def __init__(self, parent=None, height=6):
        super().__init__(parent)
        self.setFixedHeight(height)
        self.value = 0.0
        self.role = 'brand'
        self.indeterminate = False
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_state(self, value, role, indeterminate=False):
        changed = (abs(value - self.value) > 1e-4 or role != self.role or indeterminate != self.indeterminate)
        self.value, self.role = max(0.0, min(1.0, value)), role
        if indeterminate != self.indeterminate:
            self.indeterminate = indeterminate
            (clock().add if indeterminate else clock().remove)(self)
        if changed:
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        rad = r.height() / 2
        p.setPen(Qt.NoPen)
        p.setBrush(c('track'))
        p.drawRoundedRect(r, rad, rad)
        col = c(self.role)
        if self.value > 0:
            w = max(r.height(), r.width() * self.value)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(0, 0, w, r.height()), rad, rad)
        if self.indeterminate:
            t = (time.monotonic() % 1.6) / 1.6
            seg = r.width() * 0.28
            x = -seg + (r.width() + seg) * t
            hl = QColor(col)
            hl.setAlphaF(0.55 if self.value > 0 else 0.9)
            if self.value > 0:
                hl = QColor('#ffffff')
                hl.setAlphaF(0.45)
            p.setBrush(hl)
            p.setClipRect(r)
            p.drawRoundedRect(QRectF(x, 0, seg, r.height()), rad, rad)


class Toggle(_HoverButton):
    """开关（Claude 设置页那种）。"""

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(38, 22)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b'knob', self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def _get(self):
        return self._pos

    def _set(self, v):
        self._pos = v
        self.update()

    knob = Property(float, _get, _set)

    def _animate(self, on):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        off, on = c('primary_off'), c('accent')
        t = self._pos
        col = QColor.fromRgbF(off.redF() + (on.redF() - off.redF()) * t, off.greenF() + (on.greenF() - off.greenF()) * t,
                              off.blueF() + (on.blueF() - off.blueF()) * t)
        if not self.isEnabled():
            col.setAlphaF(0.45)
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 4
        x = r.left() + 2 + (r.width() - d - 4) * t
        p.setBrush(QColor(0, 0, 0, 30))
        p.drawEllipse(QRectF(x, r.top() + 2.6, d, d))
        p.setBrush(QColor('#ffffff'))
        p.drawEllipse(QRectF(x, r.top() + 2, d, d))


class Segmented(QWidget):
    """一组互斥的小图标按钮（主题：系统 / 浅色 / 深色）。"""
    changed = Signal(str)

    def __init__(self, options, current, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(3, 3, 3, 3)
        h.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = {}
        for key, icon, tip in options:
            b = IconButton(icon, tip, size=30, icon_size=17, radius=7)
            b.setCheckable(True)
            b.setChecked(key == current)
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            self.group.addButton(b)
            self.buttons[key] = b
            h.addWidget(b)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(c('border_strong'), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)


class Avatar(QWidget):
    """圆形头像：有用户头像图片就画图片，否则画首字母（或小人图标）。"""

    def __init__(self, size=30, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.text = ''
        self.role = 'avatar'
        self.image = None           # QImage / QPixmap（已经是圆形、圆外透明）
        self._scaled = None

    def set_text(self, text, role='avatar'):
        self.text, self.role = (text or '')[:1].upper(), role
        self.update()

    def set_image(self, image):
        if image is not None and image.isNull():
            image = None
        if image is not self.image:
            self.image, self._scaled = image, None
            self.update()

    def _image_pixmap(self):
        """按屏幕缩放预先缩小好的头像（小尺寸直接缩放 512 的大图会有锯齿）。"""
        dpr = self.devicePixelRatioF()
        side = max(1, round(self.width() * dpr))
        if self._scaled is None or self._scaled[0] != side:
            img = self.image.toImage() if hasattr(self.image, 'toImage') else self.image
            pix = QPixmap.fromImage(img.scaled(side, side, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
            pix.setDevicePixelRatio(dpr)
            self._scaled = (side, pix)
        return self._scaled[1]

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.image is not None:
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            path = QPainterPath()
            path.addEllipse(QRectF(self.rect()))
            p.setClipPath(path)
            p.drawPixmap(0, 0, self._image_pixmap())
            p.setClipping(False)
            p.setPen(QPen(c('border'), 1))      # 浅色图片贴着浅色背景时也看得出边界
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5))
            return
        p.setPen(Qt.NoPen)
        p.setBrush(c(self.role))
        p.drawEllipse(QRectF(self.rect()))
        if self.text:
            p.setPen(QColor('#ffffff'))
            p.setFont(px_font(int(self.height() * 0.45), 600))
            p.drawText(self.rect(), int(Qt.AlignCenter), self.text)
        else:
            s = self.height() * 0.56
            icons.paint(p, 'user', QRectF((self.width() - s) / 2, (self.height() - s) / 2, s, s), QColor('#ffffff'))


class PlatformTag(QWidget):
    """平台标签：彩色小圆点 + 平台名（细边框胶囊）。"""

    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.name = name or '未知'
        self.setFont(px_font(12))
        fm = QFontMetrics(self.font())
        self.setFixedSize(fm.horizontalAdvance(self.name) + 26, 22)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(c('border_strong'), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(PLATFORM_COLORS.get(self.name, P['text3'])))
        p.drawEllipse(QRectF(9, self.height() / 2 - 3, 6, 6))
        p.setPen(c('text2'))
        p.setFont(self.font())
        p.drawText(QRectF(19, 0, self.width() - 22, self.height()), int(Qt.AlignVCenter | Qt.AlignLeft), self.name)


class Combo(QComboBox):
    """下拉框：下拉列表用圆角高亮的样式（需要 QStyledItemDelegate 才会套用样式表）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setItemDelegate(QStyledItemDelegate(self))
        self.setCursor(Qt.PointingHandCursor)
        self.setMaxVisibleItems(12)      # 选项多（如很多音轨）时最多显示 12 行，其余滚动


# ---------------------------------------------------------------- 输入框

class _Edit(QPlainTextEdit):
    submit = Signal()
    focus_changed = Signal()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not (e.modifiers() & Qt.ShiftModifier):
            self.submit.emit()
            return
        super().keyPressEvent(e)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.focus_changed.emit()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.focus_changed.emit()


class Composer(QWidget):
    """Claude 式的大输入框：圆角、柔和投影，左下角「+」和选项胶囊，右下角陶土橙发送按钮。"""
    submitted = Signal()
    SHADOW = 10

    def __init__(self, placeholder='', parent=None):
        super().__init__(parent)
        self.min_lines = 2
        s = self.SHADOW
        v = QVBoxLayout(self)
        v.setContentsMargins(s + 16, s + 14, s + 12, s + 12)
        v.setSpacing(10)
        self.edit = _Edit()
        self.edit.setObjectName('composer')
        self.edit.setPlaceholderText(placeholder)
        self.edit.setFrameShape(QFrame.NoFrame)
        self.edit.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.edit.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.edit.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.edit.document().setDocumentMargin(2)
        self.edit.submit.connect(self._submit)
        self.edit.textChanged.connect(self._changed)
        self.edit.focus_changed.connect(self.update)
        v.addWidget(self.edit)
        self.bar = QHBoxLayout()
        self.bar.setSpacing(6)
        self.plus = IconButton('plus', '更多', size=32, icon_size=18, variant='outline', radius=9)
        self.bar.addWidget(self.plus)
        self.left = QHBoxLayout()
        self.left.setSpacing(6)
        self.bar.addLayout(self.left)
        self.bar.addStretch(1)
        self.right = QHBoxLayout()
        self.right.setSpacing(6)
        self.bar.addLayout(self.right)
        self.send = IconButton('arrow_up', '解析（Enter）', size=32, icon_size=18, variant='accent', radius=9)
        self.send.clicked.connect(self._submit)
        self.bar.addWidget(self.send)
        v.addLayout(self.bar)
        self._changed()

    def text(self):
        return self.edit.toPlainText()

    def clear(self):
        self.edit.clear()

    def set_min_lines(self, n):
        self.min_lines = n
        self._fit()

    def _submit(self):
        if self.text().strip():
            self.submitted.emit()

    def _changed(self):
        self.send.setEnabled(bool(self.text().strip()))
        self._fit()

    def _fit(self):
        fm = self.edit.fontMetrics()
        lines = max(1, int(self.edit.document().documentLayout().documentSize().height()))
        lines = min(max(lines, self.min_lines), 8)
        self.edit.setFixedHeight(int(lines * fm.lineSpacing() + 2 * self.edit.document().documentMargin() + 4))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, self._fit)

    def mousePressEvent(self, e):
        self.edit.setFocus()          # 点输入框里的空白处也能开始输入
        super().mousePressEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self.SHADOW
        r = QRectF(self.rect()).adjusted(s, s, -s, -s)
        rad = 20
        sh = c('shadow')
        base = sh.alphaF()
        p.setPen(Qt.NoPen)
        for i in range(1, s + 1):     # 柔和投影：一圈圈越来越淡，偏向下方
            col = QColor(sh)
            col.setAlphaF(base * (1 - i / (s + 1)) ** 2 * 0.32)
            p.setBrush(col)
            p.drawRoundedRect(r.adjusted(-i * 0.8, -i * 0.4 + 2, i * 0.8, i + 2), rad + i * 0.6, rad + i * 0.6)
        p.setBrush(c('surface'))
        focus = self.edit.hasFocus()
        p.setPen(QPen(c('border_strong') if focus else c('border'), 1))
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), rad, rad)


# ---------------------------------------------------------------- 轻提示

class Toast(QWidget):
    """窗口底部的深色胶囊提示，淡入淡出，不挡鼠标。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hide()
        self._text, self._kind, self._opacity = '', 'info', 0.0
        self.bottom = 120
        self.setFont(px_font(13, 500))
        self._anim = QPropertyAnimation(self, b'opacity', self)
        self._anim.setDuration(160)
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

    def show_message(self, text, kind='info', ms=2600):
        if text.startswith('✓'):
            text = text[1:].strip()
        self._text, self._kind = text, kind
        fm = QFontMetrics(self.font())
        max_w = max(240, (self.parentWidget().width() if self.parentWidget() else 800) - 120)
        self._shown = fm.elidedText(text, Qt.ElideMiddle, max_w - 64)
        self.resize(fm.horizontalAdvance(self._shown) + 64, 40)
        self.place()
        self.raise_()
        self.show()
        self._fade(1.0)
        self._timer.start(ms + min(2400, len(text) * 40))

    def place(self):
        parent = self.parentWidget()
        if parent:
            self.move((parent.width() - self.width()) // 2, parent.height() - self.height() - self.bottom)

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
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(Qt.NoPen)
        p.setBrush(c('tooltip'))
        p.drawRoundedRect(r, 12, 12)
        icon, role = {'ok': ('check_circle', 'ok'), 'error': ('alert', 'err')}.get(self._kind, ('info', None))
        col = c('on_tooltip', 0.75)
        if role:     # 提示底色和主题相反：浅色主题是深底，用亮一点的绿/红；深色主题是浅底，用深一点的
            col = QColor(theme.LIGHT[role] if theme.is_dark() else ('#a7c785' if role == 'ok' else '#f08c78'))
        icons.paint(p, icon, QRectF(16, (self.height() - 18) / 2, 18, 18), col)
        p.setPen(c('on_tooltip'))
        p.setFont(self.font())
        p.drawText(QRectF(42, 0, self.width() - 56, self.height()), int(Qt.AlignVCenter | Qt.AlignLeft),
                   getattr(self, '_shown', self._text))


class NoticeBar(Card):
    """窗口底部的常驻提示条：一句话 + 一个操作按钮 + 关闭。不像 Toast 会自己消失（如「内核已更新，重启后生效」）。"""

    def __init__(self, parent):
        super().__init__(radius=14, fill='menu', border='border_strong', parent=parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 8, 8, 8)
        h.setSpacing(10)
        h.addWidget(Spark(18, self))
        self.label = QLabel()
        self.label.setObjectName('rowLabel')
        self.label.setStyleSheet('font-size: 13px;')
        h.addWidget(self.label)
        h.addSpacing(6)
        self.btn = QPushButton()
        self.btn.setObjectName('accent')
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.clicked.connect(self._clicked)
        h.addWidget(self.btn)
        x = IconButton('x', '关闭', size=28, icon_size=15)
        x.clicked.connect(self.hide)
        h.addWidget(x)
        self.bottom = 96
        self._on_click = None
        self.hide()

    def show_notice(self, text, button='', on_click=None):
        self.label.setText(text)
        self.btn.setText(button)
        self.btn.setVisible(bool(button))
        self._on_click = on_click
        self.show()
        self.raise_()
        self.place()

    def _clicked(self):
        if self._on_click:
            self._on_click()

    def place(self):
        parent = self.parentWidget()
        if parent and self.isVisible():
            self.adjustSize()
            self.move((parent.width() - self.width()) // 2, parent.height() - self.height() - self.bottom)


# ---------------------------------------------------------------- 窗口内的模态面板（设置）

class Overlay(QWidget):
    """盖在窗口上的半透明遮罩 + 居中面板。点遮罩空白处或按 Esc 关闭。"""
    closed = Signal()

    def __init__(self, host, width=900, height=640):
        super().__init__(host)
        self.host = host
        self.pref = (width, height)
        self.panel = Card(radius=16, fill='panel', border='border_strong', parent=self)
        self.setFocusPolicy(Qt.StrongFocus)
        host.installEventFilter(self)
        self.hide()

    def eventFilter(self, obj, ev):
        if obj is self.host and ev.type() == QEvent.Resize and self.isVisible():
            self._layout()
        return False

    def _layout(self):
        self.setGeometry(self.host.rect())
        w = min(self.pref[0], self.width() - 48)
        h = min(self.pref[1], self.height() - 48)
        self.panel.setGeometry((self.width() - w) // 2, (self.height() - h) // 2, w, h)

    def open(self):
        self._layout()
        self.show()
        self.raise_()
        self.setFocus()

    def close_panel(self):
        if self.isVisible():
            self.hide()
            self.closed.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), c('scrim'))

    def mousePressEvent(self, e):
        if not self.panel.geometry().contains(e.position().toPoint()):
            self.close_panel()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close_panel()
        else:
            super().keyPressEvent(e)


# ---------------------------------------------------------------- Windows 窗口外观

def _colorref(role):
    col = c(role)
    if col.alpha() < 255:              # 半透明颜色先叠到背景色上
        bg = c('bg')
        a = col.alphaF()
        col = QColor.fromRgbF(bg.redF() * (1 - a) + col.redF() * a, bg.greenF() * (1 - a) + col.greenF() * a,
                              bg.blueF() * (1 - a) + col.blueF() * a)
    return col.red() | (col.green() << 8) | (col.blue() << 16)


def _dwm(hwnd, attr, value):
    v = ctypes.c_int(value)
    ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))


def style_window(w, caption_role='bg'):
    """Win11：标题栏和窗口同色（深色模式下标题栏也变深）。"""
    if sys.platform != 'win32':
        return
    try:
        hwnd = int(w.winId())
        _dwm(hwnd, 20, 1 if theme.is_dark() else 0)     # 深色标题栏按钮
        _dwm(hwnd, 35, _colorref(caption_role))        # 标题栏背景
        _dwm(hwnd, 36, _colorref('text'))               # 标题文字
        _dwm(hwnd, 34, _colorref('border_strong'))      # 窗口边框
    except Exception:  # noqa: BLE001 - 旧系统不支持
        pass


def round_popup(w):
    if sys.platform != 'win32':
        return
    try:
        hwnd = int(w.winId())
        _dwm(hwnd, 33, 3)                               # 小圆角
        _dwm(hwnd, 34, _colorref('border_strong'))
    except Exception:  # noqa: BLE001
        pass


class ChromeFilter(QObject):
    """全局：新弹出的菜单、下拉列表、提示气泡加圆角；对话框标题栏配色。"""

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow():
            if isinstance(obj, QMenu) or obj.inherits('QComboBoxPrivateContainer') or obj.inherits('QTipLabel'):
                round_popup(obj)
            elif isinstance(obj, (QDialog, QMainWindow)):
                style_window(obj, 'bg')
        return False


def popup_menu(parent=None):
    m = QMenu(parent)
    m.setFont(px_font(13))
    return m
