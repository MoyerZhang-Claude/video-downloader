r"""Export the generated avatar to Windows ICO and Android launcher resources.

Run from the project root: .venv\Scripts\python.exe 图标设计\二次元头像\export_icons.py
Only icon assets are written; the download notification symbol is preserved.
"""
import os
import shutil
import struct
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QImage, QPainter, QPainterPath

OUT = Path(__file__).resolve().parent
ROOT = OUT.parent.parent
RES = ROOT / 'android/app/src/main/res'
BACKUP = OUT / '修改前备份'
BG = QColor('#7363AC')
DENSITIES = {'mdpi': 1, 'hdpi': 1.5, 'xhdpi': 2, 'xxhdpi': 3, 'xxxhdpi': 4}
SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def canvas(size, height=None):
    img = QImage(size, height or size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    return img


def painter(img):
    p = QPainter(img)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    return p


def save(img, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not img.save(str(path), 'PNG'):
        raise RuntimeError(f'Cannot save {path}')


def avatar_at(img, box):
    p = painter(img)
    p.drawImage(box, AVATAR)
    p.end()


def tile(size):
    # Supersampling keeps the rounded perimeter and facial features smooth.
    high = max(1024, size)
    img = canvas(high)
    p = painter(img)
    inset = high * .025
    shape = QPainterPath()
    shape.addRoundedRect(QRectF(inset, inset, high - 2 * inset, high - 2 * inset), high * .23, high * .23)
    p.setClipPath(shape)
    p.fillRect(img.rect(), BG)
    p.drawImage(QRectF(high * .025, high * .025, high * .95, high * .95), AVATAR)
    p.end()
    return img.scaled(size, size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


def adaptive(size, layer):
    img = canvas(1296)
    if layer == 'background':
        img.fill(BG)
    else:
        # 108 dp canvas; portrait in the central 72 dp. Eyes, mouth and clip
        # remain inside the central safe circle; only outer hair can be masked.
        avatar_at(img, QRectF(216, 216, 864, 864))
        if layer == 'monochrome':
            rgba = img.convertToFormat(QImage.Format_RGBA8888)
            data = rgba.bits()
            for i in range(0, rgba.sizeInBytes(), 4):
                luminance = .2126 * data[i] + .7152 * data[i+1] + .0722 * data[i+2]
                coverage = max(0, min(1, (190 - luminance) / 80))
                data[i+3] = round(data[i+3] * coverage)
                data[i] = data[i+1] = data[i+2] = 255
            img = rgba
    return img.scaled(size, size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


def png_bytes(img):
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    if not img.save(buf, 'PNG'):
        raise RuntimeError('PNG encode failed')
    return bytes(data)


def write_ico(path):
    blobs = [(s, png_bytes(tile(s))) for s in SIZES]
    offset = 6 + 16 * len(blobs)
    entries, data = b'', b''
    for size, blob in blobs:
        entries += struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(blob), offset + len(data))
        data += blob
    path.write_bytes(struct.pack('<HHH', 0, 1, len(blobs)) + entries + data)


def masked_android(size, shape, monochrome=False):
    img = canvas(432)
    p = painter(img)
    area = QRectF(72, 72, 288, 288)
    clip = QPainterPath()
    if shape == 'circle':
        clip.addEllipse(area)
    else:
        radius = 65 if shape == 'rounded' else 16
        clip.addRoundedRect(area, radius, radius)
    p.setClipPath(clip)
    if monochrome:
        p.fillRect(img.rect(), QColor('#E4D9FF'))
        fg = adaptive(432, 'monochrome')
        q = painter(fg)
        q.setCompositionMode(QPainter.CompositionMode_SourceIn)
        q.fillRect(fg.rect(), QColor('#463465'))
        q.end()
    else:
        p.drawImage(0, 0, adaptive(432, 'background'))
        fg = adaptive(432, 'foreground')
    p.drawImage(0, 0, fg)
    p.end()
    return img.copy(72, 72, 288, 288).scaled(size, size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


def preview():
    img = canvas(1400, 940)
    p = painter(img)
    p.fillRect(img.rect(), QColor('#F7F4FC'))
    font_id = QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
    family = QFontDatabase.applicationFontFamilies(font_id)[0]

    def label(x, y, text, size=22, color='#47365D'):
        f = QFont(family)
        f.setPixelSize(size)
        p.setFont(f)
        p.setPen(QColor(color))
        p.drawText(x, y, text)

    label(64, 68, '视频解析下载器 · 二次元头像图标', 32)
    label(64, 108, '银紫短发 / 紫瞳 / 播放键发夹', 19, '#877693')
    p.drawImage(64, 155, tile(420))
    label(580, 188, 'Windows · 实际像素尺寸', 24)
    x = 580
    for s in (128, 64, 48, 32, 24, 16):
        p.drawImage(x, 240 + 128 - s, tile(s))
        label(x, 402, str(s), 16)
        x += s + 36
    label(580, 475, '桌面快捷方式、窗口、任务栏使用同一头像', 20)
    label(580, 514, '仅更换图标资源，保留原有软件界面和功能', 20, '#877693')
    p.fillRect(0, 625, 1400, 315, QColor('#221C32'))
    for index, (name, shape, mono) in enumerate((('安卓 · 圆形', 'circle', False), ('安卓 · 圆角方形', 'rounded', False), ('安卓 · 方形', 'square', False), ('安卓 · 主题色', 'circle', True))):
        x = 88 + index * 330
        p.drawImage(x, 669, masked_android(190, shape, mono))
        label(x, 902, name, 19, '#DDD2EF')
    p.end()
    save(img, OUT / '图标预览.png')


def backup_once():
    paths = [ROOT / 'app/icon.ico', OUT.parent / '图标_1024.png', OUT.parent / '图标预览.png',
             ROOT / 'android/app/build.gradle.kts']
    paths += sorted(RES.glob('mipmap-*/*.png'))
    for src in paths:
        dst = BACKUP / src.relative_to(ROOT)
        if src.is_file() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


if __name__ == '__main__':
    qt = QGuiApplication([])
    AVATAR = QImage(str(OUT / '头像原稿.png'))
    if AVATAR.isNull() or not AVATAR.hasAlphaChannel():
        raise RuntimeError('A transparent generated avatar is required')
    backup_once()
    save(tile(1024), OUT / '图标_1024.png')
    save(tile(1024), OUT.parent / '图标_1024.png')
    write_ico(ROOT / 'app/icon.ico')
    shutil.copy2(ROOT / 'app/icon.ico', OUT / 'Windows图标.ico')
    for density, factor in DENSITIES.items():
        dest = RES / f'mipmap-{density}'
        save(tile(round(48 * factor)), dest / 'ic_launcher.png')
        for layer in ('background', 'foreground', 'monochrome'):
            save(adaptive(round(108 * factor), layer), dest / f'ic_launcher_{layer}.png')
    preview()
    shutil.copy2(OUT / '图标预览.png', OUT.parent / '图标预览.png')
    print('Exported Windows ICO (10 sizes), Android icons (5 densities), and previews.')
