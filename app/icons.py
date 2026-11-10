"""线条图标（24×24，圆头圆角细线，风格接近 Claude 应用）。全部为本项目手绘的简单几何图形。

icons.pixmap('download', '#141413', 18) → 已按屏幕缩放渲染好的 QPixmap（带缓存）。
"""
import math

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .brand_logos import LOGOS


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
    'cloud': '<path d="M7.2 18.5h9.9a4.1 4.1 0 0 0 .7-8.15 5.9 5.9 0 0 0-11.3 1.4A3.4 3.4 0 0 0 7.2 18.5z"/>',
    'cloud_up': '<path d="M7.2 18.5h9.9a4.1 4.1 0 0 0 .7-8.15 5.9 5.9 0 0 0-11.3 1.4A3.4 3.4 0 0 0 7.2 18.5z"/>'
                '<path d="M12 16v-5.2M9.8 12.8 12 10.6l2.2 2.2"/>',
    'settings': _gear(),
    'search': '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4 4"/>',
    'zoom_in': '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4 4M8.4 11h5.2M11 8.4v5.2"/>',
    'zoom_out': '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4 4M8.4 11h5.2"/>',
    'rotate': '<rect x="3.5" y="10" width="10.5" height="10.5" rx="2"/>'
              '<path d="M10 4.5h4a5.5 5.5 0 0 1 5.5 5.5v3.5M16.8 11.3l2.7 2.7 2.7-2.7"/>',
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
    'lock': '<rect x="5.5" y="10.5" width="13" height="9.5" rx="2.2"/><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5"/>',
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
    'edit': '<path d="M14.5 5.5l4 4M4 20l1-4.5L15.8 4.7a2 2 0 0 1 2.9 0l.6.6a2 2 0 0 1 0 2.9L8.5 19z"/>',
    'folder_move': '<path d="M3.5 7A1.5 1.5 0 0 1 5 5.5h4l2 2.2h8A1.5 1.5 0 0 1 20.5 9.2v8.8a1.5 1.5 0 0 1-1.5 1.5H5'
                   'A1.5 1.5 0 0 1 3.5 18z"/><path d="M9 13.5h6.5M13 11l2.5 2.5L13 16"/>',
    'folder_plus': '<path d="M3.5 7A1.5 1.5 0 0 1 5 5.5h4l2 2.2h8A1.5 1.5 0 0 1 20.5 9.2v8.8a1.5 1.5 0 0 1-1.5 1.5H5'
                   'A1.5 1.5 0 0 1 3.5 18z"/><path d="M12 10.5v6M9 13.5h6"/>',
    # ---- 自建分区可选的图标库 ----
    'star': '<path d="M12 3.6l2.5 5.2 5.7.8-4.1 4 1 5.7L12 16.6l-5.1 2.7 1-5.7-4.1-4 5.7-.8z"/>',
    'music': '<path d="M9 17.5V6.2l10-2.2v11.5"/><circle cx="6.5" cy="17.5" r="2.5"/><circle cx="16.5" cy="15.5" r="2.5"/>',
    'game': '<path d="M7.2 8h9.6a4.5 4.5 0 0 1 4.4 5.4l-.7 3.5a2.3 2.3 0 0 1-4.1 1l-1.9-2.4H9.5l-1.9 2.4a2.3 2.3 0 0 1-4.1-1'
            'l-.7-3.5A4.5 4.5 0 0 1 7.2 8z"/><path d="M7.5 10.5v3.5M5.75 12.25h3.5"/>' + _dots((15.5, 11), (17.5, 13.2), r=1.05),
    'book': '<path d="M4.5 5.5A1.5 1.5 0 0 1 6 4h12.5v14H6a1.5 1.5 0 0 0-1.5 1.5z"/><path d="M4.5 19.5A1.5 1.5 0 0 0 6 21h12.5'
            'M8.5 8h6"/>',
    'camera': '<path d="M4.5 8.5A1.5 1.5 0 0 1 6 7h2l1.5-2.2h5L16 7h2a1.5 1.5 0 0 1 1.5 1.5v9A1.5 1.5 0 0 1 18 19H6'
              'a1.5 1.5 0 0 1-1.5-1.5z"/><circle cx="12" cy="12.8" r="3.3"/>',
    'cap': '<path d="M2.5 9.5 12 5l9.5 4.5L12 14z"/><path d="M6.5 11.6V16c1.5 1.4 3.4 2 5.5 2s4-.6 5.5-2v-4.4M21.5 9.5v5"/>',
    'bookmark': '<path d="M6.5 4.5A1.5 1.5 0 0 1 8 3h8a1.5 1.5 0 0 1 1.5 1.5V21L12 17l-5.5 4z"/>',
    'coffee': '<path d="M5 9h12v6a5 5 0 0 1-5 5h-2a5 5 0 0 1-5-5z"/><path d="M17 11h1.5a2.5 2.5 0 0 1 0 5H17'
              'M8.5 3.5c-.6.8-.6 1.7 0 2.5M12 3.5c-.6.8-.6 1.7 0 2.5"/>',
    'plane': '<path d="M21 3.5 10.5 14M21 3.5l-6.5 17-4-6.5-6.5-4z"/>',
    'dumbbell': '<path d="M6.5 6.5v11M17.5 6.5v11M3.5 9v6M20.5 9v6M6.5 12h11"/>',
    'food': '<path d="M7 3v8M4.5 3v5a2.5 2.5 0 0 0 5 0V3M7 11v10M17 3c-1.8 1-3 3.2-3 6v4h3v8"/>',
    'paw': '<circle cx="8.3" cy="7.5" r="1.8"/><circle cx="15.7" cy="7.5" r="1.8"/><circle cx="4.9" cy="12" r="1.6"/>'
           '<circle cx="19.1" cy="12" r="1.6"/><path d="M12 11.5c-2.5 0-5 3.6-5 6a2.5 2.5 0 0 0 3.2 2.4c1.2-.4 2.4-.4 3.6 0'
           'A2.5 2.5 0 0 0 17 17.5c0-2.4-2.5-6-5-6z"/>',
    'palette': '<path d="M12 3.5a8.5 8.5 0 1 0 0 17c1.3 0 2-.8 2-1.8 0-1.3-1.2-1.7-1.2-2.9 0-1 .8-1.8 1.8-1.8h2.1'
               'a3.8 3.8 0 0 0 3.8-3.8c0-3.8-3.8-6.7-8.5-6.7z"/>' + _dots((7.6, 11.6), (9.4, 7.8), (14.2, 7.6), r=1.15),
    'code': '<path d="m8 7-5 5 5 5M16 7l5 5-5 5M13.5 4.5l-3 15"/>',
    'mic': '<rect x="9" y="3.5" width="6" height="11" rx="3"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v2.5"/>',
    'leaf': '<path d="M6 18.5C5 11 9.5 5 19.5 4.5c.5 9.5-5 14.5-13.5 14z"/><path d="M4.5 20.5c3.5-4.5 7-7.5 10.5-9.5"/>',
    'flame': '<path d="M12 21c-3.9 0-6.5-2.6-6.5-6.2 0-3.6 2.7-5.6 3.6-8.8 2.2 1.3 3.2 3.4 3.1 5.4 1-.6 1.8-1.7 2-3'
             ' 1.9 1.6 3.3 4 3.3 6.4 0 3.6-2.6 6.2-5.5 6.2z"/>',
    'gift': '<rect x="3.5" y="8" width="17" height="4" rx="1"/><path d="M5 12v7.5a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V12'
            'M12 8v12.5M12 8C10.5 4.5 6.5 4 6.5 6.5S10 8 12 8zm0 0c1.5-3.5 5.5-4 5.5-1.5S14 8 12 8z"/>',
    'home': '<path d="M4 10.5 12 4l8 6.5V19a1.5 1.5 0 0 1-1.5 1.5H15V15H9v5.5H5.5A1.5 1.5 0 0 1 4 19z"/>',
    'car': '<path d="M4.8 13.5 6.4 8.4A2 2 0 0 1 8.3 7h7.4a2 2 0 0 1 1.9 1.4l1.6 5.1"/><rect x="3.5" y="13.5" width="17"'
           ' height="4.5" rx="1.5"/><path d="M6 18v2M18 18v2"/>' + _dots((7.5, 15.75), (16.5, 15.75), r=1),
    'smile': '<circle cx="12" cy="12" r="8.5"/><path d="M8.5 14.2a4.2 4.2 0 0 0 7 0"/>' + _dots((9, 10), (15, 10), r=1),
    'sparkle': '<path d="M11 3.5c.6 4.4 2.1 5.9 6.5 6.5-4.4.6-5.9 2.1-6.5 6.5-.6-4.4-2.1-5.9-6.5-6.5 4.4-.6 5.9-2.1 6.5-6.5z"/>'
               '<path d="M18.5 15.5c.3 1.8.9 2.4 2.7 2.7-1.8.3-2.4.9-2.7 2.7-.3-1.8-.9-2.4-2.7-2.7 1.8-.3 2.4-.9 2.7-2.7z"/>',
    'trophy': '<path d="M8 4h8v5.5a4 4 0 0 1-8 0z"/><path d="M8 6H5.5a2.5 2.5 0 0 0 2.6 3.5M16 6h2.5a2.5 2.5 0 0 1-2.6 3.5'
              'M12 13.5V17M8.5 20.5h7M9.5 20.5c0-2 1-3.5 2.5-3.5s2.5 1.5 2.5 3.5"/>',
    'news': '<path d="M5 5.5h11.5V19a1.5 1.5 0 0 0 1.5 1.5H6.5A1.5 1.5 0 0 1 5 19z"/><path d="M16.5 9h2.5V19a1.5 1.5 0 0 1-1.5 1.5'
            'M8 9h5.5M8 12.5h5.5M8 16h3.5"/>',
    'moon_star': '<path d="M19.5 14.6A7.8 7.8 0 1 1 9.4 4.5a6.2 6.2 0 0 0 10.1 10.1z"/><path d="M17 3.5v3M15.5 5h3"/>',
}

# 新建分区时可以选的图标（顺序就是图标库里的排列）
SECTION_ICONS = ['folder', 'star', 'heart', 'bookmark', 'music', 'film', 'tv', 'game', 'camera', 'mic', 'book', 'cap',
                 'news', 'code', 'palette', 'sparkle', 'trophy', 'gift', 'smile', 'paw', 'leaf', 'flame', 'coffee', 'food',
                 'dumbbell', 'plane', 'car', 'home', 'globe', 'moon_star']

_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{c}" '
        'stroke-width="{w}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
_cache = {}


def _brand_svg(info, color):
    """平台 logo：单色实心轮廓，居中放进正方形（颜色由主题决定）。"""
    x, y, w, h = info['box']
    side = max(w, h) * 1.06
    vx, vy = x + w / 2 - side / 2, y + h / 2 - side / 2
    rule = ' fill-rule="evenodd"' if info['evenodd'] else ''
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vx:.3f} {vy:.3f} {side:.3f} {side:.3f}">'
            f'<path d="{" ".join(info["paths"])}" fill="{color}"{rule}/></svg>')


def is_known(name):
    return name in BODIES or name in LOGOS


def _renderer(name, color, width):
    key = (name, color, width)
    r = _cache.get(key)
    if r is None:
        if name in LOGOS:
            svg = _brand_svg(LOGOS[name], color)
        else:
            body = BODIES.get(name, BODIES['folder']).replace('{c}', color)
            svg = _SVG.format(c=color, w=width, body=body)
        r = QSvgRenderer(QByteArray(svg.encode()))
        _cache[key] = r
    return r


def _split(color):
    """SVG 不认 #AARRGGBB：拆成 #RRGGBB + 不透明度。"""
    col = QColor(color)
    return col.name(), col.alphaF()


_paint_cache = {}       # (图标, 颜色, 线宽, 像素宽, 像素高) -> QPixmap


def paint(painter, name, rect, color, width=1.75):
    """直接画到 painter 上（自绘控件用）。
    矢量图标每次重新渲染很慢（滚动时所有可见的条目每一帧都要重画），这里按屏幕缩放比例渲染成位图缓存起来，
    之后只是贴图。"""
    rect = QRectF(rect)
    dev = painter.device()
    dpr = dev.devicePixelRatioF() if dev is not None else 1.0
    w, h = max(1, round(rect.width() * dpr)), max(1, round(rect.height() * dpr))
    cname, alpha = _split(color)
    key = (name, cname, width, w, h)
    pm = _paint_cache.get(key)
    if pm is None:
        if len(_paint_cache) > 1500:        # 换了很多次主题 / 缩放：清掉重来
            _paint_cache.clear()
        pm = QPixmap(w, h)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        _renderer(name, cname, width).render(p, QRectF(0, 0, w, h))
        p.end()
        _paint_cache[key] = pm
    if alpha < 1:
        painter.save()
        painter.setOpacity(painter.opacity() * alpha)
    painter.drawPixmap(rect, pm, QRectF(0, 0, w, h))
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
