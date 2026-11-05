"""从各平台官方网页 / 官方图标里取出 logo 的形状，生成单色 logo 数据（颜色跟着主题走：有其形、无其色）。

来源（下载在 图标设计/平台logo/原始文件/；这些网页源码和图标不提交到仓库，重新生成前按下面的来源重新下载）：
- X：x.com 首页源码里的 logo 路径
- YouTube：youtube.com 首页源码里的 logo（取左边的播放按钮；红底里的白三角在单色版里镂空）
- 快手：kuaishou.com 首页引用的官方 logo-text.svg（取左边的图标部分，不要「快手」两个字）
- B站、抖音：官网只提供 32 像素 / 100 像素的位图图标，放大会糊；用 Simple Icons（CC0）照官方图标绘制的矢量轮廓，
  形状和官网图标一致（见 原始文件/bilibili.ico、douyin_launcher.png 对照）
- 夸克网盘：pan.quark.cn 页脚的官方单色矢量 logo（quark_footer.svg，取左边的云朵图标，不要文字；
  和页头彩色 logo quark_header.png 是同一个形状）
- Mega：mega.io 官网的 logo（mega_logo.svg：红色圆 + 白色 M，单色版里 M 镂空）
- 百度网盘、123云盘：官网只有位图（baidupan_logo.png 三个交错的圆环、pan123_favicon.ico 圆角方块里的「123」），
  这里按位图量好尺寸，用几何图形重画成矢量（三个圆环 + 连接带；圆角方块镂空出「123」）
logo 是各平台的商标，这里只用来标识对应平台的分区。

运行（项目根目录）：.venv\\Scripts\\python.exe 界面设计\\make_logos.py
输出：app/brand_logos.py（电脑版）；安卓版由 gen_android_icons.py 读取它生成 BrandLogos.kt。另存一张预览图。
"""
import os
import re
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QByteArray, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPainterPathStroker, QTransform,
)
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, '图标设计', '平台logo', '原始文件')


def read(name):
    with open(os.path.join(SRC, name), encoding='utf-8', errors='replace') as f:
        return f.read()


def x_logo():
    d = re.search(r'\sd="(M18\.244[^"]+)"', read('x.html')).group(1)
    return [d], False, 'x.com 首页源码'


def youtube_logo():
    html = read('youtube.html')
    svg = next(m.group(0) for m in re.finditer(r'<svg[\s\S]*?</svg>', html) if 'viewBox="0 0 93 20"' in m.group(0))
    paths = re.findall(r'<path[^>]*\sd="([^"]+)"', svg)
    return paths[:2], True, 'youtube.com 首页源码（播放按钮）'


def kuaishou_logo():
    svg = read('kuaishou_logo-text.svg')
    d = re.search(r'<path[^>]*fill="#FF4906"[^>]*\sd="([^"]+)"|<path[^>]*\sd="([^"]+)"[^>]*fill="#FF4906"', svg)
    return [d.group(1) or d.group(2)], True, 'kuaishou.com 官方 logo-text.svg（图标部分）'


def simple_icon(name, label):
    d = re.search(r'\sd="([^"]+)"', read(f'simpleicons_{name}.svg')).group(1)
    return [d], False, f'Simple Icons「{label}」矢量轮廓（照官方图标绘制，CC0）'


def parse_d(d):
    """简单的 SVG 路径解析（只支持绝对坐标的 M / L / C / Z，够用于夸克的 logo）。"""
    path = QPainterPath()
    nums = lambda t: [float(x) for x in re.findall(r'-?[\d.]+(?:e-?\d+)?', t)]   # noqa: E731
    for cmd, args in re.findall(r'([MLCZ])([^MLCZ]*)', d):
        v = nums(args)
        if cmd == 'M':
            path.moveTo(v[0], v[1])
            for i in range(2, len(v), 2):
                path.lineTo(v[i], v[i + 1])
        elif cmd == 'L':
            for i in range(0, len(v), 2):
                path.lineTo(v[i], v[i + 1])
        elif cmd == 'C':
            for i in range(0, len(v), 6):
                path.cubicTo(*v[i:i + 6])
        else:
            path.closeSubpath()
    return path


def svg_transform(text):
    """SVG 的 transform 属性 → QTransform（和 SVG 一样，写在前面的最后作用到坐标上）。"""
    t = QTransform()
    for name, args in re.findall(r'(\w+)\(([^)]*)\)', text or ''):
        v = [float(x) for x in re.findall(r'-?[\d.]+', args)]
        if name == 'translate':
            t.translate(v[0], v[1] if len(v) > 1 else 0)
        elif name == 'scale':
            t.scale(v[0], v[1] if len(v) > 1 else v[0])
        elif name == 'rotate':
            t.rotate(v[0])
    return t


def quark_logo():
    svg = read('quark_footer.svg')
    shape = QPainterPath()
    for attrs in re.findall(r'<path([^>]*)>', svg):
        d = re.search(r'\sd="([^"]+)"', attrs).group(1)
        if float(re.match(r'M\s*([\d.]+)', d).group(1)) >= 26:      # 右边是「夸克云」三个字，只要左边的云朵
            continue
        tr = re.search(r'transform="([^"]+)"', attrs)
        part = parse_d(d)
        part.setFillRule(Qt.WindingFill)
        shape = shape.united(svg_transform(tr.group(1) if tr else '').map(part))
    return [path_d(shape.simplified())], True, 'pan.quark.cn 页脚的官方矢量 logo（云朵图标部分）'


def mega_logo():
    svg = read('mega_logo.svg')
    return re.findall(r'<path[^>]*\sd="([^"]+)"', svg)[:2], True, 'mega.io 官网 logo（mega.svg，M 镂空）'


def path_d(path):
    """QPainterPath → SVG 路径字符串。"""
    out, i, n = [], 0, path.elementCount()
    while i < n:
        e = path.elementAt(i)
        if e.type == QPainterPath.MoveToElement:
            out.append(f'M{e.x:.2f} {e.y:.2f}')
        elif e.type == QPainterPath.LineToElement:
            out.append(f'L{e.x:.2f} {e.y:.2f}')
        elif e.type == QPainterPath.CurveToElement:
            c2, end = path.elementAt(i + 1), path.elementAt(i + 2)
            out.append(f'C{e.x:.2f} {e.y:.2f} {c2.x:.2f} {c2.y:.2f} {end.x:.2f} {end.y:.2f}')
            i += 2
        i += 1
    return ''.join(out) + 'Z'


def _stroke(path, width):
    st = QPainterPathStroker()
    st.setWidth(width)
    st.setCapStyle(Qt.RoundCap)
    st.setJoinStyle(Qt.RoundJoin)
    return st.createStroke(path).simplified()


def baidupan_logo():
    """百度网盘：上面一个大圆环，左下一个圆环，右下一个带缺口的圆环，左右两个圆环之间一条斜着的连接带（量自 76×60 的官网位图）。"""
    w = 9.4
    shape = QPainterPath()
    top = QPainterPath()
    top.addEllipse(QPointF(38, 20.2), 15.2, 15.2)
    left = QPainterPath()
    left.addEllipse(QPointF(18.2, 41.2), 13.7, 13.7)
    right = QPainterPath()
    r = QRectF(57.8 - 13.7, 41.2 - 13.7, 27.4, 27.4)
    right.arcMoveTo(r, 140)          # 左上方接连接带，顺时针绕一圈，停在左下方（官方图标这里有个缺口）
    right.arcTo(r, 140, -282)
    band = QPainterPath(QPointF(28.7, 50.0))
    band.lineTo(47.3, 32.4)
    for part in (top, left, right, band):
        shape = shape.united(_stroke(part, w))
    return [path_d(shape.simplified())], True, '照百度网盘官网 logo（pan-logo-blue.png，三个交错的圆环）重画的矢量轮廓'


def pan123_logo():
    """123云盘：圆角方块里镂空出「123」（数字是圆角的笔画，量自 64×64 的官网 favicon）。"""
    box = QPainterPath()
    box.addRoundedRect(QRectF(0, 0, 64, 64), 14, 14)
    digits = QPainterPath()
    digits.moveTo(13, 21)
    digits.lineTo(13, 43)
    for pts in (((20.5, 21), (33, 21), (33, 32), (20.5, 32), (20.5, 43), (33, 43)),
                ((39.5, 21), (52, 21), (52, 43), (39.5, 43)), ((42, 32), (52, 32))):
        digits.moveTo(*pts[0])
        for pt in pts[1:]:
            digits.lineTo(*pt)
    shape = box.subtracted(_stroke(digits, 6.4))
    return [path_d(shape.simplified())], True, '照 123云盘官网图标（favicon，圆角方块里的「123」）重画的矢量轮廓'


LOGOS = {
    'brand_bilibili': lambda: simple_icon('bilibili', 'Bilibili'),
    'brand_x': x_logo,
    'brand_youtube': youtube_logo,
    'brand_douyin': lambda: simple_icon('tiktok', 'TikTok / 抖音'),
    'brand_kuaishou': kuaishou_logo,
    'brand_baidupan': baidupan_logo,
    'brand_quark': quark_logo,
    'brand_123pan': pan123_logo,
    'brand_mega': mega_logo,
}


def bounds(paths, evenodd):
    body = ''.join(f'<path id="p{i}" d="{d}"/>' for i, d in enumerate(paths))
    r = QSvgRenderer(QByteArray(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-200 -200 1000 1000">{body}</svg>'
                                .encode()))
    box = QRectF()
    for i in range(len(paths)):
        box = box.united(r.boundsOnElement(f'p{i}'))
    return round(box.x(), 3), round(box.y(), 3), round(box.width(), 3), round(box.height(), 3)


def svg_of(info, color):
    x, y, w, h = info['box']
    side = max(w, h) * 1.06
    vx, vy = x + w / 2 - side / 2, y + h / 2 - side / 2
    rule = ' fill-rule="evenodd"' if info['evenodd'] else ''
    d = ' '.join(info['paths'])
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vx:.3f} {vy:.3f} {side:.3f} {side:.3f}">'
            f'<path d="{d}" fill="{color}"{rule}/></svg>')


def main():
    app = QGuiApplication(sys.argv[:1])  # noqa: F841
    data = {}
    for key, fn in LOGOS.items():
        paths, evenodd, source = fn()
        data[key] = {'box': bounds(paths, evenodd), 'paths': paths, 'evenodd': evenodd, 'source': source}
        print(key, data[key]['box'], len(paths), 'paths', '|', source)
    out = os.path.join(ROOT, 'app', 'brand_logos.py')
    with open(out, 'w', encoding='utf-8', newline='\n') as f:
        f.write('"""各平台 logo 的单色轮廓（由 界面设计/make_logos.py 从官方网页 / 官方图标生成，请不要手改）。\n'
                'logo 是各平台的商标，这里只用来标识对应平台的分区。颜色由界面主题决定。"""\n\n')
        f.write('LOGOS = {\n')
        for key, info in data.items():
            f.write(f'    {key!r}: {{\n        \'source\': {info["source"]!r},\n        \'box\': {info["box"]!r},\n'
                    f'        \'evenodd\': {info["evenodd"]!r},\n        \'paths\': [\n')
            for d in info['paths']:
                f.write(f'            {d!r},\n')
            f.write('        ],\n    },\n')
        f.write('}\n')
    print('wrote', out)
    # 预览：浅色、深色两种主题下的样子
    W = 120 * len(data) + 20
    img = QImage(W, 280, QImage.Format_RGB32)
    p = QPainter(img)
    for row, (bg, fg) in enumerate((('#faf9f5', '#3d3d3a'), ('#262624', '#e5e3dc'))):
        p.fillRect(0, row * 140, W, 140, QColor(bg))
        for i, info in enumerate(data.values()):
            QSvgRenderer(QByteArray(svg_of(info, fg).encode())).render(p, QRectF(20 + i * 120, row * 140 + 20, 96, 96))
    p.end()
    img.save(os.path.join(ROOT, '图标设计', '平台logo', '单色logo预览.png'))


if __name__ == '__main__':
    main()
