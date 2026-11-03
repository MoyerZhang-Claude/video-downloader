"""从各平台官方网页 / 官方图标里取出 logo 的形状，生成单色 logo 数据（颜色跟着主题走：有其形、无其色）。

来源（下载在 图标设计/平台logo/原始文件/；这些网页源码和图标不提交到仓库，重新生成前按下面的来源重新下载）：
- X：x.com 首页源码里的 logo 路径
- YouTube：youtube.com 首页源码里的 logo（取左边的播放按钮；红底里的白三角在单色版里镂空）
- 快手：kuaishou.com 首页引用的官方 logo-text.svg（取左边的图标部分，不要「快手」两个字）
- B站、抖音：官网只提供 32 像素 / 100 像素的位图图标，放大会糊；用 Simple Icons（CC0）照官方图标绘制的矢量轮廓，
  形状和官网图标一致（见 原始文件/bilibili.ico、douyin_launcher.png 对照）
logo 是各平台的商标，这里只用来标识对应平台的分区。

运行（项目根目录）：.venv\\Scripts\\python.exe 界面设计\\make_logos.py
输出：app/brand_logos.py（电脑版）；安卓版由 gen_android_icons.py 读取它生成 BrandLogos.kt。另存一张预览图。
"""
import os
import re
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QByteArray, QRectF  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter  # noqa: E402
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


LOGOS = {
    'brand_bilibili': lambda: simple_icon('bilibili', 'Bilibili'),
    'brand_x': x_logo,
    'brand_youtube': youtube_logo,
    'brand_douyin': lambda: simple_icon('tiktok', 'TikTok / 抖音'),
    'brand_kuaishou': kuaishou_logo,
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
