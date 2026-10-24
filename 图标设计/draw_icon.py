"""「棱镜落点」图标：电脑版 .ico、安卓自适应图标、预览图。

运行：.venv\\Scripts\\python.exe 图标设计\\draw_icon.py
所有尺寸都由同一套几何参数按目标像素直接绘制（不是缩放大图），小尺寸自动简化。
"""
import math
import os
import shutil
import struct
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QGuiApplication, QImage, QLinearGradient, QPainter,
                           QPainterPath, QPen, QRadialGradient)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, '图标设计')
RES = os.path.join(ROOT, 'android', 'app', 'src', 'main', 'res')
FONTS = sys.argv[1] if len(sys.argv) > 1 else ''

C_TOP = QColor('#5A5BF0')       # 左上：靛蓝
C_BOT = QColor('#AD4BF3')       # 右下：紫
C_SHADOW = QColor(40, 16, 120)

app = QGuiApplication([])


# ---------------------------------------------------------------- 几何

def squircle(rect, n=5.2, steps=900):
    cx, cy, a, b = rect.center().x(), rect.center().y(), rect.width() / 2, rect.height() / 2
    p = QPainterPath()
    for i in range(steps):
        t = 2 * math.pi * i / steps
        c, s = math.cos(t), math.sin(t)
        pt = QPointF(cx + a * math.copysign(abs(c) ** (2 / n), c), cy + b * math.copysign(abs(s) ** (2 / n), s))
        p.moveTo(pt) if i == 0 else p.lineTo(pt)
    p.closeSubpath()
    return p


def rounded_poly_segments(pts, radii):
    """圆角多边形 → [('M',x,y), ('L',x,y), ('C',x1,y1,x2,y2,x,y)...]（圆角用标准三次贝塞尔逼近圆弧）"""
    segs, n = [], len(pts)
    for i in range(n):
        prev, cur, nxt, r = pts[i - 1], pts[i], pts[(i + 1) % n], radii[i]
        v1 = (prev[0] - cur[0], prev[1] - cur[1]); l1 = math.hypot(*v1); v1 = (v1[0] / l1, v1[1] / l1)
        v2 = (nxt[0] - cur[0], nxt[1] - cur[1]); l2 = math.hypot(*v2); v2 = (v2[0] / l2, v2[1] / l2)
        ang = math.acos(max(-1.0, min(1.0, v1[0] * v2[0] + v1[1] * v2[1])))
        d = r / math.tan(ang / 2)
        a = (cur[0] + v1[0] * d, cur[1] + v1[1] * d)
        b = (cur[0] + v2[0] * d, cur[1] + v2[1] * d)
        k = 4 / 3 * math.tan((math.pi - ang) / 4) * r
        segs.append(('M' if i == 0 else 'L', *a))
        segs.append(('C', a[0] - v1[0] * k, a[1] - v1[1] * k, b[0] - v2[0] * k, b[1] - v2[1] * k, *b))
    return segs


def to_path(segs):
    p = QPainterPath()
    for s in segs:
        if s[0] == 'M':
            p.moveTo(s[1], s[2])
        elif s[0] == 'L':
            p.lineTo(s[1], s[2])
        else:
            p.cubicTo(*s[1:])
    p.closeSubpath()
    return p


def rrect_segs(x, y, w, h, r):
    return rounded_poly_segments([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], [r] * 4)


class Glyph:
    """三角（向下的播放键）+ 落点横线。坐标以「图块」边长 T 为单位，原点在图块左上角。"""

    def __init__(self, small=False):
        self.small = small
        if small:          # ≤32px：更粗壮、无细缝
            self.W, self.H, self.gap, self.bar_w, self.bar_h = 0.60, 0.44, 0.085, 0.60, 0.115
            self.r_top, self.r_tip = 0.035, 0.04
        else:
            self.W, self.H, self.gap, self.bar_w, self.bar_h = 0.54, 0.435, 0.05, 0.54, 0.068
            self.r_top, self.r_tip = 0.036, 0.046
        total = self.H + self.gap + self.bar_h
        self.top = 0.5 - total / 2 - 0.004       # 视觉上略微上提
        self.cuts = [] if small else [(0.36, 0.021), (0.67, 0.021)]   # (位置占三角高度比例, 缝宽)
        self.alphas = [1.0] if small else [0.80, 0.91, 1.0]

    def tri_segs(self, T, ox, oy):
        x0 = ox + (0.5 - self.W / 2) * T
        x1 = ox + (0.5 + self.W / 2) * T
        y0 = oy + self.top * T
        tip = (ox + 0.5 * T, oy + (self.top + self.H) * T)
        return rounded_poly_segments([(x0, y0), (x1, y0), tip], [self.r_top * T, self.r_top * T, self.r_tip * T])

    def bar_segs(self, T, ox, oy):
        y = oy + (self.top + self.H + self.gap) * T
        h = self.bar_h * T
        return rrect_segs(ox + (0.5 - self.bar_w / 2) * T, y, self.bar_w * T, h, h / 2)

    def bands(self, T, ox, oy):
        """三角切成几层：[(QPainterPath, alpha)]"""
        tri = to_path(self.tri_segs(T, ox, oy))
        y0 = oy + self.top * T
        H = self.H * T
        edges = [y0 - T]
        for pos, g in self.cuts:
            edges += [y0 + pos * H - g * T / 2, y0 + pos * H + g * T / 2]
        edges.append(y0 + H + T)
        out = []
        for i, a in enumerate(self.alphas):
            top, bot = edges[2 * i], edges[2 * i + 1]
            clip = QPainterPath()
            clip.addRect(QRectF(ox - T, top, 3 * T, bot - top))
            out.append((tri.intersected(clip), a))
        return out

    def paint(self, p, T, ox, oy, color=QColor('white'), alpha_scale=1.0, flat=False):
        p.setPen(Qt.NoPen)
        for path, a in self.bands(T, ox, oy):
            c = QColor(color)
            c.setAlphaF((1.0 if flat else a) * alpha_scale)
            p.setBrush(c)
            p.drawPath(path)
        c = QColor(color)
        c.setAlphaF(alpha_scale)
        p.setBrush(c)
        p.drawPath(to_path(self.bar_segs(T, ox, oy)))


# ---------------------------------------------------------------- 绘制

def new_image(w, h=None):
    img = QImage(w, h or w, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    return img


def painter(img):
    p = QPainter(img)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    return p


def paint_background(p, tile, fill_path=None):
    """渐变 + 左上光晕 + 右下压暗。tile = 视觉图块的矩形（光影都按它定位）。"""
    T = tile.width()
    area = fill_path if fill_path is not None else QPainterPath()
    if fill_path is None:
        area.addRect(QRectF(0, 0, p.device().width(), p.device().height()))
    p.save()
    p.setClipPath(area)
    p.setPen(Qt.NoPen)
    g = QLinearGradient(tile.topLeft(), tile.bottomRight())
    g.setColorAt(0, C_TOP)
    g.setColorAt(1, C_BOT)
    p.fillPath(area, g)
    glow = QRadialGradient(QPointF(tile.left() + 0.24 * T, tile.top() + 0.12 * T), 0.78 * T)
    glow.setColorAt(0, QColor(255, 255, 255, 78))
    glow.setColorAt(0.55, QColor(255, 255, 255, 18))
    glow.setColorAt(1, QColor(255, 255, 255, 0))
    p.fillPath(area, glow)
    dark = QRadialGradient(QPointF(tile.right(), tile.bottom()), 0.75 * T)
    dark.setColorAt(0, QColor(48, 10, 110, 70))
    dark.setColorAt(1, QColor(48, 10, 110, 0))
    p.fillPath(area, dark)
    p.restore()


def paint_rim(p, path, T):
    """玻璃边缘：上沿一道细高光，往下渐隐。"""
    r = path.boundingRect()
    g = QLinearGradient(r.topLeft(), r.bottomLeft())
    g.setColorAt(0, QColor(255, 255, 255, 120))
    g.setColorAt(0.35, QColor(255, 255, 255, 20))
    g.setColorAt(1, QColor(255, 255, 255, 0))
    pen = QPen(g, max(1.0, 0.009 * T))
    p.save()
    p.setClipPath(path)
    p.setBrush(Qt.NoBrush)
    p.setPen(pen)
    p.drawPath(path)
    p.restore()


def paint_shadow(p, glyph, T, ox, oy):
    """柔和投影：先画到 1/6 尺寸再放大，等效于模糊。"""
    S = p.device().width()
    k = 6
    small = new_image(max(1, S // k))
    q = painter(small)
    q.scale(1 / k, 1 / k)
    glyph.paint(q, T, ox, oy + 0.022 * T, C_SHADOW, 0.42, flat=True)
    q.end()
    blur = small.scaled(S, S, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    blur = blur.scaled(S // 2, S // 2, Qt.IgnoreAspectRatio, Qt.SmoothTransformation) \
               .scaled(S, S, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    p.drawImage(0, 0, blur)


def render_tile(size, inset_ratio):
    """电脑版 / 旧版安卓：圆角方块图标。"""
    img = new_image(size)
    p = painter(img)
    small = size <= 32
    inset = 0 if size <= 24 else round(size * inset_ratio)
    tile = QRectF(inset, inset, size - 2 * inset, size - 2 * inset)
    shape = squircle(tile)
    paint_background(p, tile, shape)
    T = tile.width()
    glyph = Glyph(small)
    if not small:
        p.save(); p.setClipPath(shape)
        paint_shadow(p, glyph, T, tile.left(), tile.top())
        p.restore()
        paint_rim(p, shape, T)
    glyph.paint(p, T, tile.left(), tile.top())
    p.end()
    return img


# 安卓自适应图标：画布 108dp，系统遮罩后可见约中间 72dp
ADAPT_VISIBLE = 72 / 108


def render_adaptive(size, layer):
    img = new_image(size)
    p = painter(img)
    T = size * ADAPT_VISIBLE
    o = (size - T) / 2
    tile = QRectF(o, o, T, T)
    glyph = Glyph(False)
    if layer == 'background':
        paint_background(p, tile)
    elif layer == 'foreground':
        paint_shadow(p, glyph, T, o, o)
        glyph.paint(p, T, o, o)
    else:  # monochrome：系统会按主题着色，只看透明度
        glyph.paint(p, T, o, o, flat=True)
    p.end()
    return img


# ---------------------------------------------------------------- 输出

def png_bytes(img):
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, 'PNG')
    return bytes(ba)


def write_ico(path, sizes):
    blobs = [(s, png_bytes(render_tile(s, 0.035))) for s in sizes]
    head = struct.pack('<HHH', 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    entries, data = b'', b''
    for s, b in blobs:
        entries += struct.pack('<BBBBHHII', s % 256, s % 256, 0, 0, 1, 32, len(b), offset + len(data))
        data += b
    with open(path, 'wb') as f:
        f.write(head + entries + data)


def vector_drawable(path):
    """通知栏小图标（24dp 白色剪影，和主图标同一个形状）。"""
    T, g = 24.0, Glyph(True)

    def d(segs):
        out = []
        for s in segs:
            out.append(s[0] + ','.join(f'{v:.2f}' for v in s[1:]))
        return ''.join(out) + 'z'
    xml = ('<?xml version="1.0" encoding="utf-8"?>\n'
           '<!-- 由 图标设计/draw_icon.py 生成：和 App 图标同一个「向下的播放键 + 落点」形状 -->\n'
           '<vector xmlns:android="http://schemas.android.com/apk/res/android"\n'
           '    android:width="24dp" android:height="24dp"\n'
           '    android:viewportWidth="24" android:viewportHeight="24">\n'
           f'    <path android:fillColor="#FFFFFFFF" android:pathData="{d(g.tri_segs(T, 0, 0))}" />\n'
           f'    <path android:fillColor="#FFFFFFFF" android:pathData="{d(g.bar_segs(T, 0, 0))}" />\n'
           '</vector>\n')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(xml)


_cjk = []


def font(file, size, fallback='Microsoft YaHei'):
    if not _cjk:      # 无界面模式下没有系统字体库，手动加载雅黑给中文用
        ids = QFontDatabase.addApplicationFont(r'C:\Windows\Fonts\msyh.ttc')
        _cjk.extend(QFontDatabase.applicationFontFamilies(ids) or [fallback])
    fallback = _cjk[0]
    fam = None
    if FONTS and os.path.exists(os.path.join(FONTS, file)):
        ids = QFontDatabase.addApplicationFont(os.path.join(FONTS, file))
        fams = QFontDatabase.applicationFontFamilies(ids)
        fam = fams[0] if fams else None
    f = QFont()
    f.setFamilies([x for x in (fam, fallback) if x])      # 西文用设计字体，中文回落到雅黑
    f.setPixelSize(size)
    return f


def preview(path):
    W, H = 1800, 1100
    img = new_image(W, H)
    p = painter(img)
    p.fillRect(0, 0, W, H, QColor('#F5F3FC'))
    p.fillRect(0, 760, W, 340, QColor('#17142B'))
    ink, soft = QColor('#2A2550'), QColor('#8C88AE')

    # 主图
    p.drawImage(110, 120, render_tile(512, 0.035))
    p.setFont(font('Jura-Light.ttf', 30)); p.setPen(ink)
    p.drawText(QPointF(110, 82), 'PRISM DROP')
    p.setFont(font('DMMono-Regular.ttf', 15)); p.setPen(soft)
    p.drawText(QPointF(330, 82), '— 棱镜落点 · 视频解析下载器')

    # 各尺寸（按实际像素直接绘制）
    x = 760
    for s in (256, 128, 64, 48, 32, 24, 16):
        y = 120 + (256 - s)
        p.drawImage(x, y, render_tile(s, 0.035))
        p.setFont(font('DMMono-Regular.ttf', 14)); p.setPen(soft)
        p.drawText(QRectF(x - 20, 392, s + 40, 24), Qt.AlignHCenter, f'{s}')
        x += s + (44 if s > 32 else 30)
    p.setPen(QPen(QColor('#D9D5EE'), 1))
    p.drawLine(760, 430, 1700, 430)
    p.setFont(font('DMMono-Regular.ttf', 13)); p.setPen(soft)
    p.drawText(QPointF(760, 462), 'WINDOWS · ICO  16–256 PX · 每个尺寸独立绘制，≤32 PX 合并细缝')

    # 安卓：不同桌面的遮罩
    x = 110
    bg, fg = render_adaptive(432, 'background'), render_adaptive(432, 'foreground')
    for name, mask in (('圆形', 'circle'), ('圆角方', 'squircle'), ('水滴', 'tear'), ('方形', 'square')):
        layer = new_image(432)
        q = painter(layer)
        vis = QRectF(432 * (1 - ADAPT_VISIBLE) / 2, 432 * (1 - ADAPT_VISIBLE) / 2, 432 * ADAPT_VISIBLE,
                     432 * ADAPT_VISIBLE)
        clip = QPainterPath()
        if mask == 'circle':
            clip.addEllipse(vis)
        elif mask == 'squircle':
            clip = squircle(vis)
        elif mask == 'tear':
            r = vis.width() / 2
            clip.addRoundedRect(vis, r, r)
            corner = QPainterPath(); corner.addRect(QRectF(vis.center().x(), vis.center().y(), r, r))
            clip = clip.united(corner)
        else:
            clip.addRoundedRect(vis, vis.width() * 0.12, vis.width() * 0.12)
        q.setClipPath(clip)
        q.drawImage(0, 0, bg); q.drawImage(0, 0, fg)
        q.end()
        sub = layer.copy(vis.toRect()).scaled(192, 192, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        p.drawImage(x, 820, sub)
        p.setFont(font('DMMono-Regular.ttf', 13)); p.setPen(QColor('#9C97C4'))
        p.drawText(QRectF(x, 1030, 192, 24), Qt.AlignHCenter, name)
        x += 250
    # 主题图标（安卓 13+ 单色）
    mono = render_adaptive(432, 'monochrome')
    tint = new_image(432)
    q = painter(tint)
    vis = QRectF(72, 72, 288, 288)
    q.setBrush(QColor('#D8DCF5')); q.setPen(Qt.NoPen); q.drawEllipse(vis)
    colored = new_image(432); cq = painter(colored)
    cq.drawImage(0, 0, mono); cq.setCompositionMode(QPainter.CompositionMode_SourceIn)
    cq.fillRect(0, 0, 432, 432, QColor('#3A3F7A')); cq.end()
    q.drawImage(0, 0, colored); q.end()
    p.drawImage(x, 820, tint.copy(vis.toRect()).scaled(192, 192, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    p.drawText(QRectF(x, 1030, 192, 24), Qt.AlignHCenter, '主题色')
    p.setFont(font('DMMono-Regular.ttf', 13)); p.setPen(QColor('#6E6A92'))
    p.drawText(QPointF(1400, 1060), 'ANDROID · ADAPTIVE ICON')
    p.end()
    img.save(path)


def main():
    os.makedirs(OUT, exist_ok=True)
    old = os.path.join(OUT, '旧图标')
    if not os.path.exists(old):        # 只在第一次运行时备份原来的图标
        os.makedirs(old)
        shutil.copy2(os.path.join(ROOT, 'app', 'icon.ico'), os.path.join(old, 'icon.ico'))
        shutil.copy2(os.path.join(RES, 'mipmap-xxxhdpi', 'ic_launcher.png'), os.path.join(old, 'ic_launcher.png'))
        shutil.copy2(os.path.join(RES, 'drawable', 'ic_stat_download.xml'), os.path.join(old, 'ic_stat_download.xml'))

    render_tile(1024, 0.035).save(os.path.join(OUT, '图标_1024.png'))
    write_ico(os.path.join(ROOT, 'app', 'icon.ico'), [16, 20, 24, 32, 40, 48, 64, 96, 128, 256])

    dens = {'mdpi': 1, 'hdpi': 1.5, 'xhdpi': 2, 'xxhdpi': 3, 'xxxhdpi': 4}
    for d, k in dens.items():
        folder = os.path.join(RES, f'mipmap-{d}')
        os.makedirs(folder, exist_ok=True)
        render_tile(round(48 * k), 0.04).save(os.path.join(folder, 'ic_launcher.png'))
        for layer in ('background', 'foreground', 'monochrome'):
            render_adaptive(round(108 * k), layer).save(os.path.join(folder, f'ic_launcher_{layer}.png'))
    anydpi = os.path.join(RES, 'mipmap-anydpi-v26')
    os.makedirs(anydpi, exist_ok=True)
    with open(os.path.join(anydpi, 'ic_launcher.xml'), 'w', encoding='utf-8') as f:
        f.write('<?xml version="1.0" encoding="utf-8"?>\n'
                '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
                '    <background android:drawable="@mipmap/ic_launcher_background" />\n'
                '    <foreground android:drawable="@mipmap/ic_launcher_foreground" />\n'
                '    <monochrome android:drawable="@mipmap/ic_launcher_monochrome" />\n'
                '</adaptive-icon>\n')
    vector_drawable(os.path.join(RES, 'drawable', 'ic_stat_download.xml'))
    preview(os.path.join(OUT, '图标预览.png'))
    print('ok')


if __name__ == '__main__':
    main()
