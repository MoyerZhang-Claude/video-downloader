"""生成界面用的两套英文字体（Claude 应用的英文字体是 Anthropic 的商用字体，不能随软件分发，这里用最接近的开源字体）：

- VDSans：Hanken Grotesk（SIL OFL 1.1）→ 界面正文的英文、数字。中文自动用系统字体（电脑微软雅黑、手机系统字体），和 Claude 一样。
- VDSerif：Source Serif 4（SIL OFL 1.1）→ 问候语、标题里的英文。中文同样用系统黑体字——Claude 的「周一快乐，Moyer」就是这样：中文黑体、英文衬线。

只保留拉丁字母、数字和常用标点，按固定字重生成静态字体，并按 OFL 要求改名（Source 是保留名称）。
源文件在 源文件/（从 Google Fonts 官方仓库下载的可变字体）。
运行（项目根目录）：.venv\\Scripts\\python.exe 图标设计\\字体\\make_fonts.py
输出：app/fonts/VDSans-*.ttf、VDSerif-*.ttf，以及安卓 res/font/vdsans_*.ttf、vdserif_*.ttf。
"""
import os
import shutil

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(HERE, '源文件')
OUT = os.path.join(ROOT, 'app', 'fonts')
ANDROID = os.path.join(ROOT, 'android', 'app', 'src', 'main', 'res', 'font')

# (输出家族名, 源文件, {样式: 轴取值})
FONTS = [
    ('VDSans', 'HankenGrotesk.ttf', {'Regular': {'wght': 400}, 'Medium': {'wght': 500}, 'SemiBold': {'wght': 600}}),
    ('VDSerif', 'SourceSerif4.ttf', {'Regular': {'wght': 400, 'opsz': 24}, 'SemiBold': {'wght': 600, 'opsz': 24}}),
]
WEIGHT = {'Regular': 400, 'Medium': 500, 'SemiBold': 600}


def charset():
    cps = set(range(0x20, 0x7F)) | set(range(0xA0, 0x180))
    cps |= set(range(0x2010, 0x2028)) | set(range(0x2030, 0x203B))      # 破折号、引号、省略号、‰ 等
    cps |= {ord(c) for c in '·•→←↑↓✓×€£¥'}
    return cps


def rename(font, family, style):
    name = font['name']
    for rec in list(name.names):
        if rec.nameID in (1, 2, 3, 4, 6, 16, 17, 21, 22, 25):
            name.removeNames(nameID=rec.nameID)
    full = family if style == 'Regular' else f'{family} {style}'
    for nid, text in ((1, family), (2, style), (3, f'{family}-{style};subset'), (4, full), (6, f'{family}-{style}'),
                      (16, family), (17, style)):
        name.setName(text, nid, 3, 1, 0x409)
    font['OS/2'].usWeightClass = WEIGHT[style]
    font['OS/2'].fsSelection = (font['OS/2'].fsSelection & ~0b1100001) | (0b1000000 if style == 'Regular' else 0)
    font['head'].macStyle = 0


def main():
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(ANDROID, exist_ok=True)
    opts = subset.Options()
    opts.layout_features = ['kern', 'liga', 'calt', 'locl', 'tnum', 'pnum', 'case']
    opts.name_IDs = ['*']
    opts.name_languages = ['*']
    opts.notdef_outline = True
    for family, src, styles in FONTS:
        for style, axes in styles.items():
            vf = TTFont(os.path.join(SRC, src))
            font = instancer.instantiateVariableFont(vf, axes)
            sub = subset.Subsetter(opts)
            sub.populate(unicodes=charset())
            sub.subset(font)
            rename(font, family, style)
            path = os.path.join(OUT, f'{family}-{style}.ttf')
            font.save(path)
            shutil.copy(path, os.path.join(ANDROID, f'{family.lower()}_{style.lower()}.ttf'))
            print(f'{family}-{style}', f'{os.path.getsize(path) / 1024:.0f} KB')


if __name__ == '__main__':
    main()
