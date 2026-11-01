"""「星芒落点」图标：向 Claude 致敬的原创图标（不是 Anthropic 官方标志）。

十道长短不一的陶土橙星芒，正下方那一道长成下载箭头，落到一条「落点」横线上。
配色取自 Anthropic 品牌色：橙 #d97757、象牙白 #faf9f5、浅灰 #e8e6dc。

运行（项目根目录）：.venv\\Scripts\\python.exe 图标设计\\星芒落点\\draw_icon.py
输出：app/icon.ico、安卓自适应图标（5 档密度 × 背景/前景/单色）、通知栏小图标、预览图。
每个尺寸都按目标像素直接绘制（不是缩放大图），32 像素及以下自动加粗简化。
"""
import math
import os
import struct
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QGuiApplication, QImage, QLinearGradient, QPainter,
                           QPainterPath, QPen, QRadialGradient, QTransform)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
RES = os.path.join(ROOT, 'android', 'app', 'src', 'main', 'res')

ORANGE = QColor('#d97757')
ORANGE_HI = QColor('#e3906f')      # 左上受光
ORANGE_LO = QColor('#c25f3e')      # 右下背光
IVORY = QColor('#faf9f5')
IVORY_LO = QColor('#efebe0')
LIGHT_GRAY = QColor('#e8e6dc')
DARK = QColor('#141413')

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


def rounded_poly(pts, radii):
    """圆角多边形（圆角用三次贝塞尔逼近圆弧）"""
    p = QPainterPath()
    n = len(pts)
    for i in range(n):
        prev, cur, nxt, r = pts[i - 1], pts[i], pts[(i + 1) % n], radii[i]
        v1 = (prev[0] - cur[0], prev[1] - cur[1]); l1 = math.hypot(*v1); v1 = (v1[0] / l1, v1[1] / l1)
        v2 = (nxt[0] - cur[0], nxt[1] - cur[1]); l2 = math.hypot(*v2); v2 = (v2[0] / l2, v2[1] / l2)
        ang = math.acos(max(-1.0, min(1.0, v1[0] * v2[0] + v1[1] * v2[1])))
        d = r / math.tan(ang / 2)
        a = (cur[0] + v1[0] * d, cur[1] + v1[1] * d)
        b = (cur[0] + v2[0] * d, cur[1] + v2[1] * d)
        k = 4 / 3 * math.tan((math.pi - ang) / 4) * r
        p.moveTo(*a) if i == 0 else p.lineTo(*a)
        p.cubicTo(a[0] - v1[0] * k, a[1] - v1[1] * k, b[0] - v2[0] * k, b[1] - v2[1] * k, *b)
    p.closeSubpath()
    return p


def tapered_ray(length, w_base, w_tip):
    """沿 +x 方向的一道星芒：根部粗、尖端细，尖端是圆头。"""
    r = w_tip / 2
    tip_x = length - r
    p = QPainterPath()
    p.moveTo(0, -w_base / 2)
    p.lineTo(tip_x, -r)
    p.arcTo(QRectF(tip_x - r, -r, 2 * r, 2 * r), 90, -180)
    p.lineTo(0, w_base / 2)
    p.closeSubpath()
    return p


class Glyph:
    """星芒 + 下载箭头 + 落点横线。坐标以图块边长 T 为单位，原点在图块左上角。"""
    # 十道星芒（从正上方开始顺时针，每 36°），长度长短交错、略有出入，像手绘；第 6 道（正下方）是箭头
    # 箭头两侧的两道稍短，和下移的箭头留出空隙：整体仍是完整的放射星芒，最下面一道伸长成下载箭头
    LENGTHS = [0.300, 0.214, 0.270, 0.206, 0.188, None, 0.180, 0.274, 0.202, 0.288]

    def __init__(self, small=False):
        self.small = small
        self.cy = 0.415
        k = 1.28 if small else 1.0          # 小尺寸：线条加粗，免得糊成一团
        self.scale = 1.12 if small else 1.0  # 小尺寸：整体放大一点，更饱满
        self.w_base, self.w_tip = 0.092 * k, 0.068 * k   # 圆润、饱满的星芒
        self.hub = 0.094 * k
        self.stem_w = 0.080 * k
        self.head_top, self.head_tip, self.head_half = 0.222, 0.342, 0.112 * (1.06 if small else 1)
        self.tray_y, self.tray_h, self.tray_w = 0.386, 0.040 * (1.4 if small else 1), 0.262

    def path(self, T, ox=0.0, oy=0.0):
        # 小尺寸整体放大：等效于把图块放大后居中
        if self.scale != 1.0:
            ox, oy, T = ox - (self.scale - 1) * T / 2, oy - (self.scale - 1) * T / 2, T * self.scale
        cx, cy = ox + 0.5 * T, oy + self.cy * T
        shape = QPainterPath()
        shape.setFillRule(Qt.WindingFill)
        for i, L in enumerate(self.LENGTHS):
            if not L:
                continue
            ang = -90 + 36 * i
            ray = tapered_ray(L * T, self.w_base * T, self.w_tip * T)
            shape.addPath(QTransform().translate(cx, cy).rotate(ang).map(ray))
        shape.addEllipse(QPointF(cx, cy), self.hub * T, self.hub * T)
        # 正下方：箭杆 + 圆角箭头
        stem = QPainterPath()
        stem.addRoundedRect(QRectF(cx - self.stem_w * T / 2, cy, self.stem_w * T, (self.head_top + 0.02) * T),
                            self.stem_w * T / 2, self.stem_w * T / 2)
        shape.addPath(stem)
        hy, hx = cy + self.head_top * T, self.head_half * T
        shape.addPath(rounded_poly([(cx - hx, hy), (cx + hx, hy), (cx, cy + self.head_tip * T)],
                                   [0.028 * T, 0.028 * T, 0.024 * T]))
        united = shape.simplified()
        tray = QPainterPath()
        tw, th = self.tray_w * T, self.tray_h * T
        tray.addRoundedRect(QRectF(cx - tw / 2, cy + self.tray_y * T, tw, th), th / 2, th / 2)
        return united, tray


# ---------------------------------------------------------------- 绘制

def new_image(w, h=None):
    img = QImage(w, h or w, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    return img


def painter(img):
    p = QPainter(img)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    return p


def paint_background(p, tile, area):
    T = tile.width()
    p.save()
    p.setClipPath(area)
    g = QLinearGradient(tile.topLeft(), tile.bottomRight())
    g.setColorAt(0, IVORY)
    g.setColorAt(1, IVORY_LO)
    p.fillPath(area, g)
    glow = QRadialGradient(QPointF(tile.left() + 0.25 * T, tile.top() + 0.15 * T), 0.7 * T)
    glow.setColorAt(0, QColor(255, 255, 255, 150))
    glow.setColorAt(1, QColor(255, 255, 255, 0))
    p.fillPath(area, glow)
    p.restore()


def spark_brush(tile):
    g = QLinearGradient(QPointF(tile.left() + 0.25 * tile.width(), tile.top() + 0.12 * tile.height()),
                        QPointF(tile.left() + 0.78 * tile.width(), tile.top() + 0.86 * tile.height()))
    g.setColorAt(0, ORANGE_HI)
    g.setColorAt(0.45, ORANGE)
    g.setColorAt(1, ORANGE_LO)
    return g


def paint_shadow(p, paths, T):
    """柔和投影：画到小图再放大，等效模糊。"""
    S = p.device().width()
    k = 6
    small = new_image(max(1, S // k))
    q = painter(small)
    q.scale(1 / k, 1 / k)
    q.translate(0, 0.014 * T)
    q.setPen(Qt.NoPen)
    q.setBrush(QColor(120, 60, 30, 46))
    for path in paths:
        q.drawPath(path)
    q.end()
    blur = small.scaled(S, S, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    blur = blur.scaled(S // 2, S // 2, Qt.IgnoreAspectRatio, Qt.SmoothTransformation) \
               .scaled(S, S, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    p.drawImage(0, 0, blur)


def paint_glyph(p, tile, small, flat_color=None):
    T = tile.width()
    spark, tray = Glyph(small).path(T, tile.left(), tile.top())
    p.setPen(Qt.NoPen)
    if flat_color is not None:
        p.setBrush(flat_color)
        p.drawPath(spark)
        p.drawPath(tray)
        return spark, tray
    brush = spark_brush(tile)
    p.setBrush(brush)
    p.drawPath(spark)
    p.setBrush(QColor(DARK.red(), DARK.green(), DARK.blue(), 170))     # 落点：深灰，和星芒区分开
    p.drawPath(tray)
    return spark, tray


def render_tile(size, inset_ratio=0.035):
    """电脑版 / 旧版安卓启动器：圆角方块图标。"""
    img = new_image(size)
    p = painter(img)
    small = size <= 32
    inset = 0 if size <= 24 else round(size * inset_ratio)
    tile = QRectF(inset, inset, size - 2 * inset, size - 2 * inset)
    shape = squircle(tile)
    paint_background(p, tile, shape)
    T = tile.width()
    if not small:
        spark, tray = Glyph(False).path(T, tile.left(), tile.top())
        p.save(); p.setClipPath(shape)
        paint_shadow(p, [spark, tray], T)
        p.restore()
        # 极细的边框，在白色桌面背景上也有轮廓
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(LIGHT_GRAY.darker(104), max(1.0, 0.006 * T)))
        p.drawPath(shape)
    paint_glyph(p, tile, small)
    p.end()
    return img


ADAPT_VISIBLE = 72 / 108          # 安卓自适应图标：108dp 画布，遮罩后可见约中间 72dp


def render_adaptive(size, layer):
    img = new_image(size)
    p = painter(img)
    T = size * ADAPT_VISIBLE
    o = (size - T) / 2
    tile = QRectF(o, o, T, T)
    if layer == 'background':
        full = QPainterPath()
        full.addRect(QRectF(0, 0, size, size))
        paint_background(p, tile, full)
    elif layer == 'foreground':
        spark, tray = Glyph(False).path(T, o, o)
        paint_shadow(p, [spark, tray], T)
        paint_glyph(p, tile, False)
    else:   # monochrome：系统按主题色着色，只看透明度
        paint_glyph(p, tile, False, flat_color=QColor('white'))
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
    blobs = [(s, png_bytes(render_tile(s))) for s in sizes]
    head = struct.pack('<HHH', 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    entries, data = b'', b''
    for s, b in blobs:
        entries += struct.pack('<BBBBHHII', s % 256, s % 256, 0, 0, 1, 32, len(b), offset + len(data))
        data += b
    with open(path, 'wb') as f:
        f.write(head + entries + data)


def vector_drawable(path):
    """通知栏小图标：24dp 白色剪影（星芒 + 箭头 + 落点），用小尺寸版本的粗线条。"""
    spark, tray = Glyph(True).path(24.0)
    shape = spark.united(tray).simplified()

    def d(poly):
        pts = [poly.at(i) for i in range(poly.size())]
        return 'M' + 'L'.join(f'{q.x():.2f},{q.y():.2f}' for q in pts) + 'Z'
    data = ''.join(d(poly) for poly in shape.toSubpathPolygons())
    xml = ('<?xml version="1.0" encoding="utf-8"?>\n'
           '<!-- 由 图标设计/星芒落点/draw_icon.py 生成：和 App 图标同一个「星芒落点」剪影 -->\n'
           '<vector xmlns:android="http://schemas.android.com/apk/res/android"\n'
           '    android:width="24dp" android:height="24dp"\n'
           '    android:viewportWidth="24" android:viewportHeight="24">\n'
           f'    <path android:fillColor="#FFFFFFFF" android:pathData="{data}" />\n'
           '</vector>\n')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(xml)


_fonts = {}


def font(px, cjk=False):
    if 'cjk' not in _fonts:
        ids = QFontDatabase.addApplicationFont(r'C:\Windows\Fonts\msyh.ttc')
        _fonts['cjk'] = (QFontDatabase.applicationFontFamilies(ids) or ['Microsoft YaHei'])[0]
    f = QFont()
    f.setFamilies(['Segoe UI', _fonts['cjk']] if not cjk else [_fonts['cjk']])
    f.setPixelSize(px)
    return f


def preview(path):
    W, H = 1800, 1100
    img = new_image(W, H)
    p = painter(img)
    p.fillRect(0, 0, W, H, IVORY)
    p.fillRect(0, 760, W, 340, DARK)
    ink, soft = DARK, QColor('#87857d')
    p.drawImage(110, 120, render_tile(512))
    p.setFont(font(30)); p.setPen(ink)
    p.drawText(QPointF(110, 82), 'SPARK DROP')
    p.setFont(font(15, True)); p.setPen(soft)
    p.drawText(QPointF(330, 82), '— 星芒落点 · 视频解析下载器 · 向 Claude 致敬的原创图标（非 Anthropic 官方）')
    x = 760
    for s in (256, 128, 64, 48, 32, 24, 16):
        p.drawImage(x, 120 + (256 - s), render_tile(s))
        p.setFont(font(14)); p.setPen(soft)
        p.drawText(QRectF(x - 20, 392, s + 40, 24), Qt.AlignHCenter, f'{s}')
        x += s + (44 if s > 32 else 30)
    p.setPen(QPen(LIGHT_GRAY, 1))
    p.drawLine(760, 430, 1700, 430)
    p.setFont(font(13, True)); p.setPen(soft)
    p.drawText(QPointF(760, 462), 'WINDOWS · ICO 16–256 PX · 每个尺寸独立绘制，≤32 PX 自动加粗')
    # 安卓：不同桌面的遮罩
    x = 110
    bg, fg = render_adaptive(432, 'background'), render_adaptive(432, 'foreground')
    vis = QRectF(432 * (1 - ADAPT_VISIBLE) / 2, 432 * (1 - ADAPT_VISIBLE) / 2, 432 * ADAPT_VISIBLE, 432 * ADAPT_VISIBLE)
    for name, mask in (('圆形', 'circle'), ('圆角方', 'squircle'), ('水滴', 'tear'), ('方形', 'square')):
        layer = new_image(432)
        q = painter(layer)
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
        p.drawImage(x, 820, layer.copy(vis.toRect()).scaled(192, 192, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        p.setFont(font(13, True)); p.setPen(QColor('#b0aea5'))
        p.drawText(QRectF(x, 1030, 192, 24), Qt.AlignHCenter, name)
        x += 250
    mono = render_adaptive(432, 'monochrome')
    tint = new_image(432)
    q = painter(tint)
    q.setBrush(QColor('#e8e6dc')); q.setPen(Qt.NoPen); q.drawEllipse(vis)
    colored = new_image(432); cq = painter(colored)
    cq.drawImage(0, 0, mono); cq.setCompositionMode(QPainter.CompositionMode_SourceIn)
    cq.fillRect(0, 0, 432, 432, QColor('#5b4a3f')); cq.end()
    q.drawImage(0, 0, colored); q.end()
    p.drawImage(x, 820, tint.copy(vis.toRect()).scaled(192, 192, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    p.drawText(QRectF(x, 1030, 192, 24), Qt.AlignHCenter, '主题色')
    # 通知栏小图标（白色剪影，按实际 24dp × 3 倍显示）
    note = new_image(72)
    nq = painter(note)
    sp, tr = Glyph(True).path(72.0)
    nq.setPen(Qt.NoPen); nq.setBrush(QColor('white')); nq.drawPath(sp.united(tr))
    nq.end()
    p.drawImage(x + 260, 880, note)
    p.drawText(QRectF(x + 230, 1030, 132, 24), Qt.AlignHCenter, '通知栏')
    p.end()
    img.save(path)


def main():
    only_preview = '--preview' in sys.argv
    render_tile(1024).save(os.path.join(HERE, '图标_1024.png'))
    preview(os.path.join(HERE, '图标预览.png'))
    if only_preview:
        print('preview only')
        return
    write_ico(os.path.join(ROOT, 'app', 'icon.ico'), [16, 20, 24, 32, 40, 48, 64, 96, 128, 256])
    for d, k in {'mdpi': 1, 'hdpi': 1.5, 'xhdpi': 2, 'xxhdpi': 3, 'xxxhdpi': 4}.items():
        folder = os.path.join(RES, f'mipmap-{d}')
        os.makedirs(folder, exist_ok=True)
        render_tile(round(48 * k), 0.04).save(os.path.join(folder, 'ic_launcher.png'))
        for layer in ('background', 'foreground', 'monochrome'):
            render_adaptive(round(108 * k), layer).save(os.path.join(folder, f'ic_launcher_{layer}.png'))
    vector_drawable(os.path.join(RES, 'drawable', 'ic_stat_download.xml'))
    print('ok')


if __name__ == '__main__':
    main()
