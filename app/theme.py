"""界面主题：向 Claude 应用看齐的暖色调浅色 / 深色主题。

- 颜色全部是「令牌」（P['text']、P['surface'] …），样式表和自绘控件都从这里取色，切换深浅色只要重新 apply()。
- 中文用微软雅黑；英文正文 VDSans、标题 VDSerif（程序自带的开源字体，最接近 Claude 的字体）。
- 带透明度的颜色写成 Qt 的 #AARRGGBB，样式表和 QColor 都认。
"""
import os
import sys
import tempfile

from PySide6.QtCore import QObject, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QFontDatabase, QGuiApplication, QImage, QPainter, QPainterPath, QPalette, QPen


def _a(hex_rgb, alpha):
    """'#rrggbb' + 不透明度(0~1) → Qt 的 '#AARRGGBB'"""
    return f'#{round(alpha * 255):02x}{hex_rgb[1:]}'


# 颜色参照 Claude 官方应用的新版（v2）配色（官方桌面 App 的界面文件里的颜色变量）。
# panel：设置面板这类浮在页面上的大面板（深色下比页面亮一档，和官方一样）。
LIGHT = {
    # 页面 #fcfcfb、侧边栏 #fafaf9：和官方桌面 App 一样，侧边栏几乎和页面同色，只靠一条细线分开
    'bg': '#fcfcfb', 'sidebar': '#fafaf9', 'surface': '#ffffff', 'field': '#ffffff', 'menu': '#ffffff', 'panel': '#ffffff',
    'subtle': '#f3f3f0', 'chip': '#f6f6f4',
    'hover': _a('#1e1e1d', 0.05), 'press': _a('#1e1e1d', 0.09), 'selected': '#efeeea', 'menu_hover': '#f3f3f0',
    'border': _a('#1e1e1d', 0.11), 'border_strong': _a('#1e1e1d', 0.20), 'border_focus': _a('#1e1e1d', 0.38),
    'text': '#131313', 'text2': '#383835', 'text3': '#7b7974', 'text4': '#a5a49a',
    'brand': '#d97757', 'accent': '#c6613f', 'accent_hover': '#b45636', 'accent_press': '#9f4b2f',
    'accent_off': '#e9b9a7', 'on_accent': '#ffffff', 'tint_accent': _a('#d97757', 0.13),
    'primary': '#131313', 'primary_hover': '#2c2c2a', 'on_primary': '#f9f9f7', 'primary_off': '#d2d1c7',
    'ok': '#3f7a2e', 'tint_ok': _a('#4f8a3a', 0.14), 'err': '#c0392f', 'tint_err': _a('#d03b3b', 0.11),
    'warn': '#9a6a12', 'info': '#256abf', 'tint_info': _a('#2370cd', 0.12),
    'track': '#e7e6e1', 'scroll': _a('#1e1e1d', 0.18), 'scroll_hover': _a('#1e1e1d', 0.32),
    'scrim': _a('#131313', 0.32), 'shadow': _a('#20201f', 0.10), 'selection': _a('#d97757', 0.28),
    'tooltip': '#131313', 'on_tooltip': '#f9f9f7', 'avatar': '#c6613f',
}

# 深色：从官方桌面 App 的截图取色——页面 #191919，侧边栏 #1f1f1f（只比页面亮一点点），侧边栏选中项 #404040；
# 卡片 / 输入框 / 菜单在页面色上逐级变亮
DARK = {
    'bg': '#191919', 'sidebar': '#1f1f1f', 'surface': '#1f1f1f', 'field': '#222222', 'menu': '#2a2a2a', 'panel': '#1f1f1f',
    'subtle': '#242424', 'chip': '#1f1f1f',
    'hover': _a('#ffffff', 0.06), 'press': _a('#ffffff', 0.10), 'selected': '#404040', 'menu_hover': '#383838',
    'border': _a('#ffffff', 0.10), 'border_strong': _a('#ffffff', 0.17), 'border_focus': _a('#ffffff', 0.32),
    'text': '#f9f9f7', 'text2': '#c3c2b7', 'text3': '#97958d', 'text4': '#6d6b67',
    'brand': '#d97757', 'accent': '#c6613f', 'accent_hover': '#d9704c', 'accent_press': '#b45636',
    'accent_off': '#5a3628', 'on_accent': '#ffffff', 'tint_accent': _a('#d97757', 0.18),
    'primary': '#f9f9f7', 'primary_hover': '#e1e0d9', 'on_primary': '#131313', 'primary_off': '#454442',
    'ok': '#8cc26a', 'tint_ok': _a('#4f8a3a', 0.28), 'err': '#eb6263', 'tint_err': _a('#d03b3b', 0.24),
    'warn': '#dcaa4f', 'info': '#5598e7', 'tint_info': _a('#2370cd', 0.24),
    'track': '#2c2c2c', 'scroll': _a('#ffffff', 0.16), 'scroll_hover': _a('#ffffff', 0.30),
    'scrim': _a('#000000', 0.55), 'shadow': _a('#000000', 0.45), 'selection': _a('#d97757', 0.38),
    'tooltip': '#f9f9f7', 'on_tooltip': '#131313', 'avatar': '#c6613f',
}

# 纯黑（OLED）：背景 #000000（OLED 屏上黑色部分不发光、更省电），针对纯黑的几个毛病做了处理：
# 1. 晕光：纯白字在纯黑底上会「渗」进黑色、看着发虚（散光的人更明显），文字改用不刺眼的暖白 #e2e1dc（对比度约 15:1，
#    不是 21:1），次要文字也相应调暗；大块的白色按钮（primary）也调暗一档。
# 2. 拖影：OLED 像素从「全黑熄灭」到「点亮」反应慢，滚动时文字会拖出残影——会滚动的内容都放在 #121212 起步的深灰卡片 / 面板上，
#    文字底下的像素一直亮着；文字不用纯白也让亮灭落差更小。
# 3. 没有层次：黑色上看不见阴影，靠逐级变亮的深灰（#121212 → #161616 → #1c1c1c）和更明显一点的细边框来分层。
# 4. 颜色发颤：高饱和的颜色在纯黑上会「发颤」，成功 / 失败 / 提示色都降低了饱和度。
OLED = {
    'bg': '#000000', 'sidebar': '#000000', 'surface': '#121212', 'field': '#161616', 'menu': '#1c1c1c', 'panel': '#121212',
    'subtle': '#141414', 'chip': '#121212',
    'hover': _a('#ffffff', 0.06), 'press': _a('#ffffff', 0.10), 'selected': '#1e1e1e', 'menu_hover': '#262626',
    'border': _a('#ffffff', 0.12), 'border_strong': _a('#ffffff', 0.20), 'border_focus': _a('#ffffff', 0.36),
    'text': '#e2e1dc', 'text2': '#b5b3ab', 'text3': '#8e8c85', 'text4': '#64625d',
    'brand': '#d97757', 'accent': '#c6613f', 'accent_hover': '#d26e4c', 'accent_press': '#b45636',
    'accent_off': '#4d2f24', 'on_accent': '#f4f3ef', 'tint_accent': _a('#d97757', 0.16),
    'primary': '#d9d8d2', 'primary_hover': '#e6e5e0', 'on_primary': '#121212', 'primary_off': '#3a3936',
    'ok': '#93b885', 'tint_ok': _a('#4f8a3a', 0.22), 'err': '#e07a72', 'tint_err': _a('#d03b3b', 0.20),
    'warn': '#d6ad62', 'info': '#86aee0', 'tint_info': _a('#2370cd', 0.20),
    'track': '#242424', 'scroll': _a('#ffffff', 0.14), 'scroll_hover': _a('#ffffff', 0.26),
    'scrim': _a('#000000', 0.72), 'shadow': _a('#000000', 0.60), 'selection': _a('#d97757', 0.34),
    'tooltip': '#e2e1dc', 'on_tooltip': '#121212', 'avatar': '#c6613f',
}

P = dict(LIGHT)          # 当前主题的颜色
MODES = {'system': '跟随系统', 'light': '浅色', 'dark': '深色'}

PLATFORM_COLORS = {
    'B站': '#fb7299', 'B站番剧': '#fb7299', '抖音': '#4b4b52', '快手': '#ff5000', 'YouTube': '#ff0033',
    'X': '#6b7280', 'TikTok': '#fe2c55', '微博': '#e6162d', '小红书': '#ff2442', 'Instagram': '#c13584',
    'Vimeo': '#1ab7ea', 'Facebook': '#1877f2', 'Twitch': '#9146ff', '西瓜视频': '#ff3b30',
    '百度网盘': '#06a7ff', '夸克网盘': '#4f6bf6', '123云盘': '#5a7cfa', 'Mega': '#d9272e',
}

# 任务状态 → 颜色令牌
STATUS_ROLE = {'running': 'brand', 'merging': 'info', 'done': 'ok', 'exists': 'ok', 'failed': 'err',
               'paused': 'text3', 'canceled': 'text4', 'queued': 'text3'}

# 字体和 Claude 应用一样：中文一律用系统黑体（微软雅黑），英文正文用 VDSans、标题用 VDSerif（最接近 Claude 的开源字体）。
# Claude 的「周一快乐，Moyer」就是中文黑体 + 英文衬线。
SERIF = 'VDSerif'
LATIN = 'VDSans'
SANS = 'Microsoft YaHei UI'


class _Signals(QObject):
    changed = Signal()


signals = _Signals()
_state = {'mode': 'system', 'dark': False, 'oled': False, 'fonts': False}


def c(role, alpha=None):
    """取当前主题颜色（QColor）。alpha 可覆盖不透明度（0~1）。"""
    col = QColor(P.get(role, role))
    if alpha is not None:
        col.setAlphaF(alpha)
    return col


def status_color(status):
    return c(STATUS_ROLE.get(status, 'text3'))


def is_dark():
    return _state['dark']


def mode():
    return _state['mode']


def _system_dark():
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:  # noqa: BLE001 - 旧版 Qt 没有这个接口
        return False


def font_dirs():
    base = os.path.dirname(os.path.abspath(__file__))
    return [os.path.join(base, 'fonts'), os.path.join(getattr(sys, '_MEIPASS', base), 'app', 'fonts')]


def load_fonts():
    """注册自带的英文字体（VDSans / VDSerif）；万一没找到，英文退回 Segoe UI / Georgia。"""
    global SERIF, LATIN
    if _state['fonts']:
        return
    _state['fonts'] = True
    fams = set()
    for d in font_dirs():
        if os.path.isdir(d):
            for name in sorted(os.listdir(d)):
                if name.lower().endswith(('.ttf', '.otf')):
                    fid = QFontDatabase.addApplicationFont(os.path.join(d, name))
                    fams.update(QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else [])
            if fams:
                break
    if 'VDSerif' not in fams:
        SERIF = 'Georgia'
    if 'VDSans' not in fams:
        LATIN = 'Segoe UI'


def ui_families():
    return [LATIN, SANS]


def is_oled():
    """现在是不是纯黑（OLED）配色"""
    return _state['dark'] and _state['oled']


def apply(app, new_mode=None, oled=None):
    """应用主题（new_mode: system / light / dark；oled：深色时用纯黑）。系统深浅色变化时再调用一次即可。"""
    if new_mode:
        _state['mode'] = new_mode if new_mode in MODES else 'system'
    if oled is not None:
        _state['oled'] = bool(oled)
    m = _state['mode']
    dark = _system_dark() if m == 'system' else m == 'dark'
    _state['dark'] = dark
    P.clear()
    P.update((OLED if _state['oled'] else DARK) if dark else LIGHT)
    load_fonts()
    app.setPalette(_palette())
    app.setStyleSheet(stylesheet())
    signals.changed.emit()


def _palette():
    pal = QPalette()
    for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
        pal.setColor(group, QPalette.Window, c('bg'))
        pal.setColor(group, QPalette.Base, c('field'))
        pal.setColor(group, QPalette.AlternateBase, c('subtle'))
        pal.setColor(group, QPalette.Button, c('surface'))
        pal.setColor(group, QPalette.ToolTipBase, c('tooltip'))
        pal.setColor(group, QPalette.ToolTipText, c('on_tooltip'))
        pal.setColor(group, QPalette.Highlight, c('selection'))
        pal.setColor(group, QPalette.HighlightedText, c('text'))
        pal.setColor(group, QPalette.PlaceholderText, c('text4'))
        pal.setColor(group, QPalette.Link, c('accent'))
        dim = group == QPalette.Disabled
        for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
            pal.setColor(group, role, c('text4' if dim else 'text'))
    return pal


# ---------------------------------------------------------------- 样式表用的小图标

def _make_icons():
    d = os.path.join(tempfile.gettempdir(), 'video_downloader_theme')
    os.makedirs(d, exist_ok=True)
    tag = 'd' if _state['dark'] else 'l'
    paths = {}

    def save(name, draw, size=14):
        path = os.path.join(d, f'{name}_{tag}.png')
        img = QImage(size * 3, size * 3, QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(3, 3)
        draw(p)
        p.end()
        img.save(path)
        paths[name] = path.replace('\\', '/')

    def stroke(color, pts, width=1.6):
        def draw(p):
            p.setPen(QPen(QColor(color), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            path = QPainterPath(QPointF(*pts[0]))
            for pt in pts[1:]:
                path.lineTo(*pt)
            p.drawPath(path)
        return draw

    save('chevron', stroke(P['text3'], [(3.5, 5.5), (7, 9), (10.5, 5.5)]))
    save('chevron_off', stroke(P['text4'], [(3.5, 5.5), (7, 9), (10.5, 5.5)]))
    save('chevron_right', stroke(P['text3'], [(5.5, 3.5), (9, 7), (5.5, 10.5)]))
    save('check', stroke(P['on_primary'], [(3.5, 7.2), (6, 9.8), (10.8, 4.4)], 1.8))
    save('check_menu', stroke(P['text'], [(3.5, 7.2), (6, 9.8), (10.8, 4.4)], 1.6))
    save('dash', stroke(P['on_primary'], [(4, 7), (10, 7)], 1.8))
    return paths


def stylesheet():
    p = P
    ic = _make_icons()
    # 任务状态文字的颜色：换状态时只改控件的名字（st_ok、st_err…），不用每次刷新进度都重设样式表
    status_rules = ''.join(f'QLabel#st_{r} {{ font-size: 12px; color: {p[r]}; }}\n'
                           for r in ('brand', 'info', 'ok', 'err', 'text2', 'text3', 'text4'))
    return f"""
QWidget {{ font-size: 13px; color: {p['text']}; }}
QMainWindow, QDialog {{ background: {p['bg']}; }}
QWidget#central, QWidget#page {{ background: {p['bg']}; }}
QWidget#clear {{ background: transparent; }}
QFrame#sidebar {{ background: {p['sidebar']}; border: none; border-right: 1px solid {p['border']}; }}
QLabel {{ background: transparent; }}

QLabel#greeting {{ font-family: "{SERIF}", "{SANS}"; font-size: 34px; color: {p['text']}; }}
QLabel#pageTitle {{ font-family: "{SERIF}", "{SANS}"; font-size: 26px; color: {p['text']}; }}
QLabel#brandName {{ font-family: "{SERIF}", "{SANS}"; font-size: 17px; color: {p['text']}; }}
QLabel#serifSmall {{ font-family: "{SERIF}", "{SANS}"; font-size: 15px; color: {p['text2']}; }}
QLabel#panelTitle {{ font-size: 18px; font-weight: 600; color: {p['text']}; }}
QLabel#dialogTitle {{ font-size: 17px; font-weight: 600; color: {p['text']}; }}
QLabel#dialogText {{ font-size: 13px; color: {p['text2']}; }}
QLabel#dialogSub {{ font-size: 12px; color: {p['text3']}; }}
QLabel#updGroup {{ font-size: 13px; font-weight: 600; color: {p['text']}; padding: 6px 0 2px 0; }}
QLabel#updItem {{ font-size: 13px; color: {p['text2']}; padding: 3px 0; }}
QPushButton#link {{ background: transparent; border: none; color: {p['text3']}; padding: 6px 4px; }}
QPushButton#link:hover {{ color: {p['text']}; text-decoration: underline; }}
QLabel#sectionTitle {{ font-size: 15px; font-weight: 600; color: {p['text']}; }}
QLabel#headTitle {{ font-size: 14px; font-weight: 600; color: {p['text']}; }}
QLabel#cardTitle {{ font-size: 14px; font-weight: 600; color: {p['text']}; }}
QLabel#cardTitleDim {{ font-size: 13px; color: {p['text3']}; }}
QLabel#rowLabel {{ font-size: 14px; color: {p['text']}; }}
QLabel#rowDesc, QLabel#meta, QLabel#hint {{ font-size: 12px; color: {p['text3']}; }}
QLabel#metaStrong {{ font-size: 12px; color: {p['text2']}; }}
QLabel#metaError {{ font-size: 12px; color: {p['err']}; }}
QLabel#metaOk {{ font-size: 12px; color: {p['ok']}; }}
QLabel#metaBusy {{ font-size: 12px; color: {p['brand']}; }}
{status_rules}QLabel#sideSection {{ font-size: 12px; color: {p['text3']}; padding-left: 10px; }}
QLabel#tagOk {{ background: {p['tint_ok']}; color: {p['ok']}; border-radius: 6px; padding: 1px 7px; font-size: 12px; }}
QLabel#tagInfo {{ background: {p['tint_info']}; color: {p['info']}; border-radius: 6px; padding: 1px 7px; font-size: 12px; }}
QLabel#tagAccent {{ background: {p['tint_accent']}; color: {p['accent']}; border-radius: 6px; padding: 1px 7px; font-size: 12px; }}
QLabel#kbd {{ background: {p['subtle']}; color: {p['text3']}; border: 1px solid {p['border']}; border-radius: 4px; padding: 0 5px; font-size: 11px; }}
QLabel#link {{ color: {p['accent']}; }}

/* ---------- 按钮 ---------- */
QPushButton {{
    background: {p['surface']}; color: {p['text']}; border: 1px solid {p['border_strong']};
    border-radius: 8px; padding: 6px 14px;
}}
QPushButton:hover {{ background: {p['chip']}; border-color: {p['border_focus']}; }}
QPushButton:pressed {{ background: {p['subtle']}; }}
QPushButton:disabled {{ color: {p['text4']}; border-color: {p['border']}; background: transparent; }}
QPushButton#primary {{ background: {p['primary']}; color: {p['on_primary']}; border: none; font-weight: 600; padding: 7px 15px; }}
QPushButton#primary:hover {{ background: {p['primary_hover']}; }}
QPushButton#primary:disabled {{ background: {p['primary_off']}; color: {p['on_primary']}; }}
QPushButton#accent {{ background: {p['accent']}; color: {p['on_accent']}; border: none; font-weight: 600; padding: 7px 15px; }}
QPushButton#accent:hover {{ background: {p['accent_hover']}; }}
QPushButton#accent:pressed {{ background: {p['accent_press']}; }}
QPushButton#accent:disabled {{ background: {p['accent_off']}; }}
QPushButton#done {{ background: {p['ok']}; color: {p['on_primary'] if is_dark() else '#ffffff'}; border: none; font-weight: 600; padding: 7px 15px; }}
QPushButton#ghost {{ background: transparent; border: none; color: {p['text2']}; padding: 6px 10px; }}
QPushButton#ghost:hover {{ background: {p['hover']}; color: {p['text']}; }}
QPushButton#ghost:pressed {{ background: {p['press']}; }}
QPushButton#ghost:disabled {{ color: {p['text4']}; }}
QPushButton#danger {{ color: {p['err']}; }}
QPushButton#danger:hover {{ background: {p['tint_err']}; border-color: {p['err']}; }}
QPushButton#small {{ padding: 4px 10px; font-size: 12px; }}

/* ---------- 输入 ---------- */
QLineEdit, QSpinBox, QPlainTextEdit#field {{
    background: {p['field']}; color: {p['text']}; border: 1px solid {p['border_strong']}; border-radius: 8px;
    padding: 6px 10px; selection-background-color: {p['selection']}; selection-color: {p['text']};
}}
QLineEdit:hover, QSpinBox:hover {{ border-color: {p['border_focus']}; }}
QLineEdit:focus, QSpinBox:focus, QPlainTextEdit#field:focus {{ border-color: {p['border_focus']}; }}
QLineEdit:disabled, QSpinBox:disabled {{ color: {p['text4']}; border-color: {p['border']}; }}
QLineEdit#search {{ background: {p['field']}; border-radius: 10px; padding: 7px 10px; font-size: 14px; }}
QLineEdit#searchBig {{ background: transparent; border: none; padding: 8px 2px; font-size: 16px; }}
QPlainTextEdit#composer {{
    background: transparent; border: none; padding: 0; font-size: 15px; color: {p['text']};
    selection-background-color: {p['selection']}; selection-color: {p['text']};
}}
QPlainTextEdit#log {{
    background: transparent; border: none; padding: 4px; color: {p['text2']};
    font-family: Consolas, "{SANS}"; font-size: 12px; selection-background-color: {p['selection']};
}}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}

QComboBox {{
    background: {p['field']}; color: {p['text']}; border: 1px solid {p['border_strong']}; border-radius: 8px;
    padding: 5px 30px 5px 10px; min-height: 20px;
}}
QComboBox:hover {{ border-color: {p['border_focus']}; }}
QComboBox:on {{ border-color: {p['border_focus']}; }}
QComboBox:disabled {{ color: {p['text4']}; border-color: {p['border']}; }}
QComboBox::drop-down {{ border: none; width: 28px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url({ic['chevron']}); width: 14px; height: 14px; }}
QComboBox::down-arrow:disabled {{ image: url({ic['chevron_off']}); }}
QComboBox QAbstractItemView {{
    background: {p['menu']}; color: {p['text']}; border: none; padding: 5px; outline: 0;
    selection-background-color: {p['menu_hover']}; selection-color: {p['text']};
}}
QComboBox QAbstractItemView::item {{ min-height: 30px; padding: 0 10px; border-radius: 6px; color: {p['text']}; }}
QComboBox QAbstractItemView::item:selected, QComboBox QAbstractItemView::item:hover {{ background: {p['menu_hover']}; }}
QComboBox QAbstractItemView::item:disabled {{ color: {p['text4']}; }}

QCheckBox, QRadioButton {{ spacing: 8px; color: {p['text']}; background: transparent; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {p['border_focus']};
    background: {p['field']}; }}
QCheckBox::indicator:hover {{ border-color: {p['text3']}; }}
QCheckBox::indicator:checked {{ border: none; background: {p['primary']}; image: url({ic['check']}); }}
QCheckBox::indicator:disabled {{ border-color: {p['border']}; }}

/* ---------- 列表（选集） ---------- */
QListWidget {{ background: {p['surface']}; border: 1px solid {p['border']}; border-radius: 10px; padding: 4px;
    outline: 0; }}
QListWidget::item {{ padding: 6px 8px; border-radius: 6px; color: {p['text']}; }}
QListWidget::item:hover {{ background: {p['hover']}; }}
QListWidget::item:selected {{ background: {p['selected']}; color: {p['text']}; }}
QListWidget::item:disabled {{ color: {p['text4']}; }}
QListView::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {p['border_focus']};
    background: {p['field']}; }}
QListView::indicator:checked {{ border: none; background: {p['primary']}; image: url({ic['check']}); }}

/* ---------- 网盘分享的文件树 ---------- */
QTreeWidget#panTree {{ background: {p['surface']}; border: 1px solid {p['border']}; border-radius: 10px; padding: 4px;
    outline: 0; }}
QTreeWidget#panTree::item {{ height: 30px; color: {p['text']}; border: none; }}
QTreeWidget#panTree::item:hover {{ background: {p['hover']}; }}
QTreeView::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {p['border_focus']};
    background: {p['field']}; }}
QTreeView::indicator:checked {{ border: none; background: {p['primary']}; image: url({ic['check']}); }}
QTreeView::indicator:indeterminate {{ border: none; background: {p['primary']}; image: url({ic['dash']}); }}
QTreeView::branch {{ background: transparent; }}
QTreeView::branch:has-children:closed {{ image: url({ic['chevron_right']}); }}
QTreeView::branch:has-children:open {{ image: url({ic['chevron']}); }}

QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 3px 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px 3px; }}
QScrollBar::handle {{ background: {p['scroll']}; border-radius: 3px; }}
QScrollBar::handle:vertical {{ min-height: 36px; }}
QScrollBar::handle:horizontal {{ min-width: 36px; }}
QScrollBar::handle:hover {{ background: {p['scroll_hover']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QProgressBar {{ background: {p['track']}; border: none; border-radius: 3px; max-height: 6px; min-height: 6px;
    text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {p['brand']}; border-radius: 3px; }}

QTabWidget::pane {{ border: none; background: transparent; }}
QTabBar::tab {{ background: transparent; color: {p['text3']}; padding: 7px 14px; border: none;
    border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {p['text']}; border-bottom: 2px solid {p['brand']}; }}
QGroupBox {{ background: {p['surface']}; border: 1px solid {p['border']}; border-radius: 12px;
    margin-top: 16px; padding: 14px 10px 10px 10px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; top: 2px; padding: 0 4px; }}

/* ---------- 弹出层 ---------- */
QMenu {{ background: {p['menu']}; border: 1px solid {p['border']}; padding: 6px; }}
QMenu::item {{ padding: 7px 30px 7px 12px; border-radius: 6px; color: {p['text']}; background: transparent; }}
QMenu::item:selected {{ background: {p['menu_hover']}; }}
QMenu::item:disabled {{ color: {p['text3']}; }}
QMenu::separator {{ height: 1px; background: {p['border']}; margin: 5px 8px; }}
QMenu::icon {{ padding-left: 10px; }}
QMenu::indicator {{ width: 14px; height: 14px; padding-left: 8px; }}
QMenu::indicator:checked {{ image: url({ic['check_menu']}); }}
QMenu::right-arrow {{ image: url({ic['chevron_right']}); width: 12px; height: 12px; padding-right: 8px; }}
QToolTip {{ background: {p['tooltip']}; color: {p['on_tooltip']}; border: none; padding: 5px 9px; font-size: 12px; }}
"""
