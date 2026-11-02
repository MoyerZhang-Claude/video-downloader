"""线条图标（24×24，圆头圆角细线，风格接近 Claude 应用）。全部为本项目手绘的简单几何图形。

icons.pixmap('download', '#141413', 18) → 已按屏幕缩放渲染好的 QPixmap（带缓存）。
"""
import math

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


def _gear():
    """齿轮：8 个圆润的齿 + 中心圆。"""
    pts = []
    teeth, r_out, r_in = 8, 9.2, 7.0
    for i in range(teeth * 4):
        ang = math.pi * 2 * i / (teeth * 4) - math.pi / 2
        r = r_out if i % 4 in (1, 2) else r_in
        pts.append((12 + r * math.cos(ang), 12 + r * math.sin(ang)))
    d = 'M' + 'L'.join(f'{x:.2f},{y:.2f}' for x, y in pts) + 'Z'
    return f'<path d="{d}"/><circle cx="12" cy="12" r="3"/>'


DOT = '<circle cx="{x}" cy="{y}" r="{r}" fill="{c}" stroke="none"/>'


def _dots(*xy, r=1.25):
    return ''.join(DOT.format(x=x, y=y, r=r, c='{c}') for x, y in xy)


BODIES = {
    'plus': '<path d="M12 5v14M5 12h14"/>',
    'menu': '<path d="M4 7h16M4 12h16M4 17h10"/>',
    'arrow_left': '<path d="M19 12H5.5M11.5 5.8 5.3 12l6.2 6.2"/>',
    'arrow_up': '<path d="M12 19V5.5M5.8 11.5 12 5.3l6.2 6.2"/>',
    'arrow_down': '<path d="M12 5v13.5M5.8 12.5 12 18.7l6.2-6.2"/>',
    'folder': '<path d="M3.5 7A1.5 1.5 0 0 1 5 5.5h4l2 2.2h8A1.5 1.5 0 0 1 20.5 9.2v8.8a1.5 1.5 0 0 1-1.5 1.5H5'
              'A1.5 1.5 0 0 1 3.5 18z"/>',
    'clock': '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    'download': '<path d="M12 4.5v10.5M7.2 10.5 12 15.3l4.8-4.8M5 19.5h14"/>',
    'settings': _gear(),
    'search': '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4 4"/>',
    'chevron_down': '<path d="m6.5 9.5 5.5 5.5 5.5-5.5"/>',
    'chevron_up': '<path d="m6.5 14.5 5.5-5.5 5.5 5.5"/>',
    'chevron_right': '<path d="m9.5 6.5 5.5 5.5-5.5 5.5"/>',
    'chevron_left': '<path d="m14.5 6.5-5.5 5.5 5.5 5.5"/>',
    'check': '<path d="M5 12.5 9.8 17.3 19 7.5"/>',
    'x': '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    'pause': '<path d="M9 6.5v11M15 6.5v11"/>',
    'play': '<path d="M8.5 6v12l9.5-6z"/>',
    'trash': '<path d="M4.5 7h15M10 11v5.5M14 11v5.5M6.5 7l.8 11.3a1.8 1.8 0 0 0 1.8 1.7h5.8a1.8 1.8 0 0 0 1.8-1.7'
             'L17.5 7M9 7V5.5A1.5 1.5 0 0 1 10.5 4h3A1.5 1.5 0 0 1 15 5.5V7"/>',
    'more': _dots((6, 12), (12, 12), (18, 12), r=1.4),
    'sun': '<circle cx="12" cy="12" r="3.8"/><path d="M12 3v1.8M12 19.2V21M3 12h1.8M19.2 12H21M5.6 5.6l1.3 1.3'
           'M17.1 17.1l1.3 1.3M5.6 18.4l1.3-1.3M17.1 6.9l1.3-1.3"/>',
    'moon': '<path d="M19.5 14.6A7.8 7.8 0 1 1 9.4 4.5a6.2 6.2 0 0 0 10.1 10.1z"/>',
    'monitor': '<rect x="3.5" y="4.5" width="17" height="11.5" rx="2"/><path d="M8.5 20h7M12 16v4"/>',
    'sidebar': '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M9.5 4.5v15"/>',
    'external': '<path d="M13.5 4.5h6v6M19.5 4.5 11 13M17.5 14v4a1.5 1.5 0 0 1-1.5 1.5H6A1.5 1.5 0 0 1 4.5 18V8'
                'A1.5 1.5 0 0 1 6 6.5h4"/>',
    'scissors': '<circle cx="6.5" cy="7" r="2.5"/><circle cx="6.5" cy="17" r="2.5"/>'
                '<path d="M8.6 8.4 19.5 17.5M8.6 15.6 19.5 6.5"/>',
    'image': '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><circle cx="9" cy="10" r="1.7"/>'
             '<path d="m20.5 15.5-4.8-4.8L5.5 19.5"/>',
    'danmaku': '<path d="M4 6.5h8.5M15.5 6.5h4.5M7.5 12h12.5M4 17.5h6M13 17.5h4"/>',
    'subtitles': '<rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M7 15h3.5M13 15h4M7 11.5h6.5M16 11.5h1"/>',
    'file_text': '<path d="M13.5 3.5H7.5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V8.5z"/>'
                 '<path d="M13.5 3.5v5h5M9 13h6M9 16.5h4"/>',
    'log_out': '<path d="M9.5 20H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h3.5M15.5 16.5 20 12l-4.5-4.5M20 12H9.5"/>',
    'help': '<circle cx="12" cy="12" r="8.5"/><path d="M9.6 9.6a2.5 2.5 0 0 1 4.8.9c0 1.6-2.4 2.1-2.4 3.6"/>'
            + _dots((12, 17.2), r=0.9),
    'info': '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5"/>' + _dots((12, 7.8), r=0.9),
    'alert': '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V13"/>' + _dots((12, 16.4), r=0.9),
    'check_circle': '<circle cx="12" cy="12" r="8.5"/><path d="m8.4 12.3 2.5 2.5 4.8-5"/>',
    'play_circle': '<circle cx="12" cy="12" r="8.5"/><path d="M10.2 8.8v6.4l5-3.2z"/>',
    'refresh': '<path d="M19.5 12a7.5 7.5 0 1 1-2.3-5.4M19.5 4.5v4.2h-4.2"/>',
    'list': '<path d="M9 6.5h11M9 12h11M9 17.5h11"/>' + _dots((4.8, 6.5), (4.8, 12), (4.8, 17.5), r=1.1),
    'clipboard': '<rect x="5.5" y="5" width="13" height="15.5" rx="2"/>'
                 '<path d="M9 5V4.3a.8.8 0 0 1 .8-.8h4.4a.8.8 0 0 1 .8.8V5M9 11h6M9 14.5h4"/>',
    'copy': '<rect x="8.5" y="8.5" width="11" height="11" rx="2"/>'
            '<path d="M15.5 8.5V6.5a2 2 0 0 0-2-2h-7a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h2"/>',
    'globe': '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.4 2.5 3.4 5.3 3.4 8.5s-1 6-3.4 8.5'
             'c-2.4-2.5-3.4-5.3-3.4-8.5s1-6 3.4-8.5z"/>',
    'bell': '<path d="M6.5 16.5V11a5.5 5.5 0 0 1 11 0v5.5l1.5 1.5H5zM10.2 20.5a2 2 0 0 0 3.6 0"/>',
    'cube': '<path d="M12 3.5 19.5 7.5v9L12 20.5 4.5 16.5v-9z"/><path d="M4.5 7.5 12 11.5l7.5-4M12 11.5v9"/>',
    'user': '<circle cx="12" cy="8.5" r="3.5"/><path d="M5.5 19.5a6.5 6.5 0 0 1 13 0"/>',
    'link': '<path d="M10 14a4 4 0 0 0 5.7 0l2.8-2.8a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-2.8 2.8'
            'a4 4 0 0 0 5.7 5.7l1-1"/>',
    'film': '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M7.5 4.5v15M16.5 4.5v15M3.5 9.5h4'
            'M3.5 14.5h4M16.5 9.5h4M16.5 14.5h4"/>',
    'tv': '<rect x="3.5" y="7" width="17" height="12.5" rx="3"/><path d="M8.5 3.5 11 7M15.5 3.5 13 7M9 12v2M15 12v2"/>',
    'keyboard': '<rect x="3" y="6" width="18" height="12" rx="2.5"/><path d="M7 10h.5M10.5 10h.5M14 10h.5'
                'M17 10h.5M8 14h8"/>',
    'type': '<path d="M5 7V5.5h14V7M12 5.5v13M9.5 18.5h5"/>',
    'heart': '<path d="M12 19.5s-7.5-4.4-7.5-9.6A4.2 4.2 0 0 1 12 7.6a4.2 4.2 0 0 1 7.5 2.3c0 5.2-7.5 9.6-7.5 9.6z"/>',
    'github': '<path d="M9 19.5c-4 1.2-4-2-5.5-2.5M14.5 21v-3.4a3 3 0 0 0-.8-2.3c2.7-.3 5.5-1.3 5.5-6a4.7 4.7 0 0 0-1.3-3.2'
              ' 4.4 4.4 0 0 0-.1-3.2s-1-.3-3.4 1.3a11.6 11.6 0 0 0-6 0C6 2.6 5 2.9 5 2.9a4.4 4.4 0 0 0-.1 3.2'
              'A4.7 4.7 0 0 0 3.6 9.3c0 4.7 2.8 5.7 5.5 6a3 3 0 0 0-.8 2.3V21"/>',
}

_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{c}" '
        'stroke-width="{w}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
_cache = {}


def _renderer(name, color, width):
    key = (name, color, width)
    r = _cache.get(key)
    if r is None:
        body = BODIES.get(name, BODIES['x']).replace('{c}', color)
        r = QSvgRenderer(QByteArray(_SVG.format(c=color, w=width, body=body).encode()))
        _cache[key] = r
    return r


def _split(color):
    """SVG 不认 #AARRGGBB：拆成 #RRGGBB + 不透明度。"""
    col = QColor(color)
    return col.name(), col.alphaF()


def paint(painter, name, rect, color, width=1.75):
    """直接画到 painter 上（自绘控件用）。"""
    cname, alpha = _split(color)
    if alpha < 1:
        painter.save()
        painter.setOpacity(painter.opacity() * alpha)
    _renderer(name, cname, width).render(painter, QRectF(rect))
    if alpha < 1:
        painter.restore()


_pm_cache = {}


def pixmap(name, color, size=18, dpr=None, width=1.75):
    dpr = dpr or (QGuiApplication.primaryScreen().devicePixelRatio() if QGuiApplication.primaryScreen() else 1.0)
    cname, alpha = _split(color)
    key = (name, cname, alpha, size, dpr, width)
    pm = _pm_cache.get(key)
    if pm is None:
        pm = QPixmap(round(size * dpr), round(size * dpr))
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(alpha)
        _renderer(name, cname, width).render(p, QRectF(0, 0, pm.width(), pm.height()))
        p.end()
        pm.setDevicePixelRatio(dpr)
        _pm_cache[key] = pm
    return pm


def icon(name, color, size=18):
    return QIcon(pixmap(name, color, size))
