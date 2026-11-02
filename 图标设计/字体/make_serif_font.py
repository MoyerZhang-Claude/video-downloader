"""从思源宋体（Noto Serif SC，SIL OFL 1.1）截取常用字，生成界面标题用的衬线字体 VDSerif。

只用于问候语、页面标题等大字号文字（向 Claude 应用的衬线标题致敬）。
字符范围：GB2312 全部 6763 个汉字和符号 + 拉丁字母 + 常用标点。
按 OFL 要求改名（不使用原字体的保留名称），授权文本放在 app/fonts/OFL.txt。

运行（项目根目录）：.venv\\Scripts\\python.exe 图标设计\\字体\\make_serif_font.py [源字体路径]
默认源字体：C:\\Windows\\Fonts\\NotoSerifSC-VF.ttf（可变字体，取 wght=400 / 600 两档）。
只在开发时运行，需要 fonttools（pip install fonttools）。
"""
import os
import sys

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(ROOT, 'app', 'fonts')
SRC = sys.argv[1] if len(sys.argv) > 1 else r'C:\Windows\Fonts\NotoSerifSC-VF.ttf'
FAMILY = 'VDSerif'
WEIGHTS = {'Regular': 400, 'SemiBold': 600}


def charset():
    chars = set(chr(c) for c in range(0x20, 0x7F))          # ASCII
    chars |= set(chr(c) for c in range(0xA0, 0x180))         # 拉丁补充
    chars |= set(chr(c) for c in range(0x2000, 0x206F))      # 常用标点
    chars |= set(chr(c) for c in range(0x3000, 0x3040))      # 中文标点
    chars |= set(chr(c) for c in range(0xFF00, 0xFFF0))      # 全角
    chars |= set('·—…‘’“”←→↑↓★☆✓✔✕')
    for hi in range(0xA1, 0xF8):                             # GB2312 全部字符
        for lo in range(0xA1, 0xFF):
            try:
                chars.add(bytes([hi, lo]).decode('gb2312'))
            except UnicodeDecodeError:
                pass
    return chars


def rename(font, style, weight):
    name = font['name']
    full = f'{FAMILY} {style}' if style != 'Regular' else FAMILY
    ps = f'{FAMILY}-{style}'
    for rec in list(name.names):
        if rec.nameID in (1, 2, 3, 4, 6, 16, 17, 21, 22, 25):
            name.removeNames(nameID=rec.nameID)
    # 1/2：家族名 + 样式；Regular 以外的字重用 16/17 归到同一家族
    legacy_family = FAMILY if style in ('Regular', 'Bold') else f'{FAMILY} {style}'
    legacy_style = style if style in ('Regular', 'Bold') else 'Regular'
    for nid, text in ((1, legacy_family), (2, legacy_style), (3, f'{ps};subset'), (4, full), (6, ps),
                      (16, FAMILY), (17, style)):
        name.setName(text, nid, 3, 1, 0x409)
    name.setName('Subset of Noto Serif SC (SIL Open Font License 1.1), renamed for 视频解析下载器.', 10, 3, 1, 0x409)
    font['OS/2'].usWeightClass = weight
    font['OS/2'].fsSelection = (font['OS/2'].fsSelection & ~0b1100001) | (0b1000000 if style == 'Regular' else 0)
    font['head'].macStyle = 0


def main():
    os.makedirs(OUT, exist_ok=True)
    chars = charset()
    opts = subset.Options()
    opts.layout_features = ['kern', 'liga', 'locl', 'vert', 'palt', 'halt']
    opts.name_IDs = ['*']
    opts.name_languages = ['*']
    opts.notdef_outline = True
    opts.hinting = False
    print('源字体：', SRC, '字符数：', len(chars))
    base = TTFont(SRC)
    sub = subset.Subsetter(opts)
    sub.populate(unicodes=[ord(c) for c in chars])
    sub.subset(base)
    tmp = os.path.join(HERE, '_subset_vf.ttf')
    base.save(tmp)
    for style, weight in WEIGHTS.items():
        vf = TTFont(tmp)
        font = instancer.instantiateVariableFont(vf, {'wght': weight})
        rename(font, style, weight)
        path = os.path.join(OUT, f'{FAMILY}-{style}.ttf')
        font.save(path)
        print(style, path, f'{os.path.getsize(path) / 1024:.0f} KB')
    os.remove(tmp)


if __name__ == '__main__':
    main()
