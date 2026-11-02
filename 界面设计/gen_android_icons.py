"""把电脑版的线条图标（app/icons.py）转成安卓 Compose 用的 LineIcons.kt，两端图标保持一致。

运行（项目根目录）：.venv\\Scripts\\python.exe 界面设计\\gen_android_icons.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from app.icons import BODIES  # noqa: E402

OUT = os.path.join(ROOT, 'android', 'app', 'src', 'main', 'java', 'com', 'videodl', 'app', 'LineIcons.kt')


def num(x):
    return f'{float(x):g}'


def attrs(tag):
    return dict(re.findall(r'(\w[\w-]*)="([^"]*)"', tag))


def circle_d(cx, cy, r):
    cx, cy, r = float(cx), float(cy), float(r)
    return (f'M{num(cx - r)},{num(cy)}a{num(r)},{num(r)} 0 1,0 {num(2 * r)},0'
            f'a{num(r)},{num(r)} 0 1,0 {num(-2 * r)},0Z')


def rect_d(x, y, w, h, rx):
    x, y, w, h, rx = map(float, (x, y, w, h, rx or 0))
    if not rx:
        return f'M{num(x)},{num(y)}h{num(w)}v{num(h)}h{num(-w)}Z'
    return (f'M{num(x + rx)},{num(y)}h{num(w - 2 * rx)}a{num(rx)},{num(rx)} 0 0 1 {num(rx)},{num(rx)}'
            f'v{num(h - 2 * rx)}a{num(rx)},{num(rx)} 0 0 1 {num(-rx)},{num(rx)}h{num(-(w - 2 * rx))}'
            f'a{num(rx)},{num(rx)} 0 0 1 {num(-rx)},{num(-rx)}v{num(-(h - 2 * rx))}'
            f'a{num(rx)},{num(rx)} 0 0 1 {num(rx)},{num(-rx)}Z')


def convert(body):
    strokes, fills = [], []
    for tag in re.findall(r'<(?:path|circle|rect)[^>]*/>', body):
        a = attrs(tag)
        if tag.startswith('<path'):
            d = a['d']
        elif tag.startswith('<circle'):
            d = circle_d(a['cx'], a['cy'], a['r'])
        else:
            d = rect_d(a['x'], a['y'], a['width'], a['height'], a.get('rx'))
        (fills if a.get('stroke') == 'none' else strokes).append(d)
    return strokes, fills


def kname(name):
    return ''.join(p.capitalize() for p in name.split('_'))


def main():
    lines = ['// 由 界面设计/gen_android_icons.py 根据电脑版 app/icons.py 生成，请不要手改',
             'package com.videodl.app', '',
             'import androidx.compose.ui.graphics.Color',
             'import androidx.compose.ui.graphics.SolidColor',
             'import androidx.compose.ui.graphics.StrokeCap',
             'import androidx.compose.ui.graphics.StrokeJoin',
             'import androidx.compose.ui.graphics.vector.ImageVector',
             'import androidx.compose.ui.graphics.vector.addPathNodes',
             'import androidx.compose.ui.unit.dp', '',
             '/** 24×24 圆头细线图标（和电脑版同一套），用 Icon(LineIcons.Download, null, tint = …) 着色。 */',
             'object LineIcons {']
    for name, body in BODIES.items():
        strokes, fills = convert(body.replace('{c}', '#000'))
        s = ', '.join(f'"{d}"' for d in strokes)
        f = ', '.join(f'"{d}"' for d in fills)
        lines.append(f'    val {kname(name)} by lazy {{ build("{name}", listOf({s}), listOf({f})) }}')
    lines += ['',
              '    private fun build(name: String, strokes: List<String>, fills: List<String>): ImageVector {',
              '        val b = ImageVector.Builder(name, 24.dp, 24.dp, 24f, 24f)',
              '        strokes.forEach {',
              '            b.addPath(addPathNodes(it), stroke = SolidColor(Color.Black), strokeLineWidth = 1.75f,',
              '                strokeLineCap = StrokeCap.Round, strokeLineJoin = StrokeJoin.Round)',
              '        }',
              '        fills.forEach { b.addPath(addPathNodes(it), fill = SolidColor(Color.Black)) }',
              '        return b.build()',
              '    }',
              '}', '']
    with open(OUT, 'w', encoding='utf-8', newline='\n') as fp:
        fp.write('\n'.join(lines))
    print('wrote', OUT, len(BODIES), 'icons')


if __name__ == '__main__':
    main()
